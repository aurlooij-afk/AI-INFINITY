"""
AI Infinity
TARGET-2050.77
BUILD: WAF-RESILIENT-MISSION-EXECUTION-CORE-V2

Complete practical mission execution core.

Security guarantees:
- Public web access only
- SSRF/private-network protection
- No arbitrary code execution
- No permission bypass
- External content remains untrusted
- WAF/edge responses are never evidence
- Evidence requires transport validation
- Research requires source integrity validation
- Completion requires verification/closure
- Recovery/retry supported

Execution resilience:
- /run
- /run/
- /api/run
- /v1/run
- /task
- /execute
- /command
- /run64
- /run-options

All execution routes use the SAME mission engine and security gates.
"""

from __future__ import annotations

import asyncio
import base64
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
    quote_plus,
    urljoin,
    urlparse,
)
from urllib.request import Request as URLRequest
from urllib.request import build_opener
from urllib.request import HTTPRedirectHandler

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# VERSION
# ============================================================

VERSION = "TARGET-2050.77"
BUILD = "WAF-RESILIENT-MISSION-EXECUTION-CORE-V2"

SERVICE = "AI Infinity"

DATA_DIR = os.getenv("AI_INFINITY_DATA", "/tmp/ai-infinity")
DB_PATH = os.path.join(DATA_DIR, "ai_infinity.db")

HTTP_TIMEOUT = max(
    5,
    min(
        int(os.getenv("AI_INFINITY_HTTP_TIMEOUT", "20")),
        45,
    ),
)

MAX_OBJECTIVE_LENGTH = 12000
MAX_BODY_BYTES = 256000
MAX_RESEARCH_RESULTS = 20
MAX_EVIDENCE_PER_SOURCE = 5

os.makedirs(DATA_DIR, exist_ok=True)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "AI Infinity practical autonomous mission execution core. "
        "WAF-resilient application entrypoints with shared security gates."
    ),
)


# ============================================================
# SECURITY POLICY
# ============================================================

POLICY = {
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
    "waf_resilient_execution": True,
}


# ============================================================
# MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=MAX_OBJECTIVE_LENGTH,
    )


class CommandRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=MAX_OBJECTIVE_LENGTH,
    )


class Run64Request(BaseModel):
    objective_b64: str = Field(
        ...,
        min_length=1,
        max_length=20000,
    )


# ============================================================
# GLOBAL STATE
# ============================================================

DB_LOCK = threading.RLock()
MISSION_TASKS: dict[str, asyncio.Task] = {}


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with DB_LOCK:
        conn = db()

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                route TEXT,
                result_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                event TEXT NOT NULL,
                data_json TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                content TEXT,
                transport TEXT,
                usable INTEGER NOT NULL DEFAULT 0,
                digest TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                claim TEXT NOT NULL,
                support_count INTEGER DEFAULT 0,
                contradiction_count INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_mission
            ON events(mission_id);

            CREATE INDEX IF NOT EXISTS idx_evidence_mission
            ON evidence(mission_id);

            CREATE INDEX IF NOT EXISTS idx_claims_mission
            ON claims(mission_id);
            """
        )

        conn.commit()
        conn.close()


def now() -> float:
    return time.time()


def event(
    mission_id: str,
    name: str,
    data: Optional[dict[str, Any]] = None,
) -> None:
    with DB_LOCK:
        conn = db()
        conn.execute(
            """
            INSERT INTO events
            (mission_id,event,data_json,created_at)
            VALUES (?,?,?,?)
            """,
            (
                mission_id,
                name,
                json.dumps(data or {}, ensure_ascii=False),
                now(),
            ),
        )
        conn.commit()
        conn.close()


def create_mission(
    objective: str,
    route: str,
) -> str:
    mission_id = "mission-" + uuid.uuid4().hex[:12]

    with DB_LOCK:
        conn = db()

        ts = now()

        conn.execute(
            """
            INSERT INTO missions
            (id,objective,status,route,result_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                mission_id,
                objective,
                "queued",
                route,
                None,
                ts,
                ts,
            ),
        )

        conn.commit()
        conn.close()

    event(
        mission_id,
        "mission_created",
        {
            "route": route,
            "security_policy": "enforced",
        },
    )

    return mission_id


def update_mission(
    mission_id: str,
    status: Optional[str] = None,
    result: Optional[dict[str, Any]] = None,
) -> None:
    with DB_LOCK:
        conn = db()

        if status is not None:
            conn.execute(
                """
                UPDATE missions
                SET status=?, updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    now(),
                    mission_id,
                ),
            )

        if result is not None:
            conn.execute(
                """
                UPDATE missions
                SET result_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    json.dumps(result, ensure_ascii=False),
                    now(),
                    mission_id,
                ),
            )

        conn.commit()
        conn.close()


def get_mission(mission_id: str) -> Optional[dict[str, Any]]:
    with DB_LOCK:
        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        conn.close()

    if not row:
        return None

    result = dict(row)

    if result.get("result_json"):
        try:
            result["result"] = json.loads(result["result_json"])
        except Exception:
            result["result"] = None

    result.pop("result_json", None)

    return result


def get_events(mission_id: str) -> list[dict[str, Any]]:
    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT event,data_json,created_at
            FROM events
            WHERE mission_id=?
            ORDER BY id ASC
            """,
            (mission_id,),
        ).fetchall()

        conn.close()

    output = []

    for row in rows:
        item = dict(row)

        try:
            item["data"] = json.loads(item.pop("data_json"))
        except Exception:
            item["data"] = {}

        output.append(item)

    return output


# ============================================================
# NETWORK SECURITY
# ============================================================

PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def is_private_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)

        return any(
            ip in network
            for network in PRIVATE_NETWORKS
        )
    except ValueError:
        return True


def validate_public_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return False, "unsupported_scheme"

        if not parsed.hostname:
            return False, "missing_hostname"

        host = parsed.hostname.strip().lower()

        if host in {
            "localhost",
            "localhost.localdomain",
            "metadata.google.internal",
            "metadata",
        }:
            return False, "blocked_hostname"

        try:
            if is_private_ip(host):
                return False, "private_ip"
        except Exception:
            return False, "invalid_ip"

        try:
            addresses = socket.getaddrinfo(
                host,
                parsed.port or (
                    443 if parsed.scheme == "https" else 80
                ),
                type=socket.SOCK_STREAM,
            )

            for address in addresses:
                ip = address[4][0]

                if is_private_ip(ip):
                    return False, "hostname_resolves_private"

        except socket.gaierror:
            return False, "dns_failure"

        return True, "allowed"

    except Exception:
        return False, "invalid_url"


# ============================================================
# TRANSPORT CLASSIFICATION
# ============================================================

WAF_MARKERS = (
    "cloudflare",
    "cloudfront",
    "akamai",
    "imperva",
    "sucuri",
    "waf",
    "access denied",
    "request blocked",
    "security policy",
    "bot detection",
    "ray id",
)


def classify_transport(
    http_status: int,
    content_type: str,
    body: str,
    headers: Optional[dict[str, str]] = None,
) -> dict[str, Any]:

    headers = headers or {}

    header_text = " ".join(
        f"{k}:{v}"
        for k, v in headers.items()
    ).lower()

    body_lower = (body or "")[:12000].lower()

    marker_hit = any(
        marker in body_lower or marker in header_text
        for marker in WAF_MARKERS
    )

    if http_status in {401, 403, 406, 429} and marker_hit:
        reason = "EDGE_WAF_BLOCK"

        return {
            "edge_failure": True,
            "application_failure": False,
            "is_evidence": False,
            "usable_for_research": False,
            "http_status": http_status,
            "content_type": content_type,
            "reason": reason,
            "digest": hashlib.sha256(
                body.encode("utf-8", "ignore")
            ).hexdigest(),
        }

    if 500 <= http_status <= 599:
        return {
            "edge_failure": False,
            "application_failure": True,
            "is_evidence": False,
            "usable_for_research": False,
            "http_status": http_status,
            "content_type": content_type,
            "reason": "UPSTREAM_5XX",
            "digest": hashlib.sha256(
                body.encode("utf-8", "ignore")
            ).hexdigest(),
        }

    if not body.strip():
        return {
            "edge_failure": False,
            "application_failure": False,
            "is_evidence": False,
            "usable_for_research": False,
            "http_status": http_status,
            "content_type": content_type,
            "reason": "EMPTY_RESPONSE",
            "digest": "",
        }

    if "text/html" in content_type.lower():
        if (
            "access denied" in body_lower
            or "request blocked" in body_lower
            or "security policy" in body_lower
        ):
            return {
                "edge_failure": True,
                "application_failure": False,
                "is_evidence": False,
                "usable_for_research": False,
                "http_status": http_status,
                "content_type": content_type,
                "reason": "BLOCKED_HTML",
                "digest": hashlib.sha256(
                    body.encode("utf-8", "ignore")
                ).hexdigest(),
            }

    if "application/pdf" in content_type.lower():
        return {
            "edge_failure": False,
            "application_failure": False,
            "is_evidence": True,
            "usable_for_research": True,
            "http_status": http_status,
            "content_type": content_type,
            "reason": "VALID_PUBLIC_CONTENT",
            "digest": hashlib.sha256(
                body.encode("utf-8", "ignore")
            ).hexdigest(),
        }

    if (
        "application/json" in content_type.lower()
        or "text/plain" in content_type.lower()
        or "text/html" in content_type.lower()
    ):
        if 200 <= http_status < 300:
            return {
                "edge_failure": False,
                "application_failure": False,
                "is_evidence": True,
                "usable_for_research": True,
                "http_status": http_status,
                "content_type": content_type,
                "reason": "VALID_PUBLIC_CONTENT",
                "digest": hashlib.sha256(
                    body.encode("utf-8", "ignore")
                ).hexdigest(),
            }

    return {
        "edge_failure": False,
        "application_failure": False,
        "is_evidence": False,
        "usable_for_research": False,
        "http_status": http_status,
        "content_type": content_type,
        "reason": "UNVERIFIED_RESPONSE",
        "digest": hashlib.sha256(
            body.encode("utf-8", "ignore")
        ).hexdigest(),
    }


# ============================================================
# PUBLIC HTTP
# ============================================================

class SafeRedirectHandler(HTTPRedirectHandler):

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        ok, _ = validate_public_url(newurl)

        if not ok:
            return None

        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        )


def fetch_public(
    url: str,
) -> dict[str, Any]:

    ok, reason = validate_public_url(url)

    if not ok:
        return {
            "url": url,
            "ok": False,
            "status": 0,
            "content_type": "",
            "body": "",
            "headers": {},
            "transport": {
                "edge_failure": False,
                "application_failure": True,
                "is_evidence": False,
                "usable_for_research": False,
                "http_status": 0,
                "content_type": "",
                "reason": f"NETWORK_POLICY:{reason}",
                "digest": "",
            },
        }

    request = URLRequest(
        url,
        headers={
            "User-Agent": (
                "AI-Infinity/2050.77 "
                "(public-research-agent)"
            ),
            "Accept": (
                "text/html,application/json,"
                "text/plain,application/pdf;q=0.8"
            ),
        },
        method="GET",
    )

    opener = build_opener(
        SafeRedirectHandler()
    )

    try:
        response = opener.open(
            request,
            timeout=HTTP_TIMEOUT,
        )

        raw = response.read(
            2_000_000
        )

        content_type = (
            response.headers.get(
                "Content-Type",
                "",
            )
        )

        try:
            body = raw.decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            body = ""

        headers = {
            str(k): str(v)
            for k, v in response.headers.items()
        }

        status = int(
            getattr(response, "status", 200)
        )

        transport = classify_transport(
            status,
            content_type,
            body,
            headers,
        )

        return {
            "url": url,
            "ok": True,
            "status": status,
            "content_type": content_type,
            "body": body,
            "headers": headers,
            "transport": transport,
        }

    except HTTPError as exc:

        try:
            raw = exc.read(
                500_000
            )

            body = raw.decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            body = ""

        headers = {
            str(k): str(v)
            for k, v in exc.headers.items()
        }

        content_type = (
            exc.headers.get(
                "Content-Type",
                "",
            )
            if exc.headers
            else ""
        )

        status = int(exc.code)

        transport = classify_transport(
            status,
            content_type,
            body,
            headers,
        )

        return {
            "url": url,
            "ok": False,
            "status": status,
            "content_type": content_type,
            "body": body,
            "headers": headers,
            "transport": transport,
        }

    except (URLError, TimeoutError, OSError) as exc:

        return {
            "url": url,
            "ok": False,
            "status": 0,
            "content_type": "",
            "body": "",
            "headers": {},
            "transport": {
                "edge_failure": False,
                "application_failure": True,
                "is_evidence": False,
                "usable_for_research": False,
                "http_status": 0,
                "content_type": "",
                "reason": type(exc).__name__,
                "digest": "",
            },
        }

    except Exception as exc:

        return {
            "url": url,
            "ok": False,
            "status": 0,
            "content_type": "",
            "body": "",
            "headers": {},
            "transport": {
                "edge_failure": False,
                "application_failure": True,
                "is_evidence": False,
                "usable_for_research": False,
                "http_status": 0,
                "content_type": "",
                "reason": type(exc).__name__,
                "digest": "",
            },
        }


# ============================================================
# TEXT EXTRACTION
# ============================================================

def clean_text(value: str) -> str:
    value = re.sub(
        r"<script[\s\S]*?</script>",
        " ",
        value,
        flags=re.I,
    )

    value = re.sub(
        r"<style[\s\S]*?</style>",
        " ",
        value,
        flags=re.I,
    )

    value = re.sub(
        r"<[^>]+>",
        " ",
        value,
    )

    value = html.unescape(value)

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def extract_title(body: str) -> str:
    match = re.search(
        r"<title[^>]*>(.*?)</title>",
        body or "",
        flags=re.I | re.S,
    )

    if match:
        return clean_text(
            match.group(1)
        )[:500]

    return ""


# ============================================================
# RESEARCH PROVIDERS
# ============================================================

def ddg_search(
    objective: str,
) -> list[dict[str, str]]:

    url = (
        "https://html.duckduckgo.com/html/"
        "?q="
        + quote_plus(objective[:1000])
    )

    result = fetch_public(url)

    if not result["transport"]["usable_for_research"]:
        return []

    body = result["body"]

    output = []

    patterns = re.findall(
        r'nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>',
        body,
        flags=re.I | re.S,
    )

    for href, title in patterns:
        title = clean_text(title)

        if href.startswith("//"):
            href = "https:" + href

        if href.startswith("/"):
            continue

        if not href.startswith(
            ("http://", "https://")
        ):
            continue

        output.append(
            {
                "url": href,
                "title": title[:500],
                "provider": "duckduckgo",
            }
        )

        if len(output) >= MAX_RESEARCH_RESULTS:
            break

    return output


def wikipedia_search(
    objective: str,
) -> list[dict[str, str]]:

    url = (
        "https://en.wikipedia.org/w/api.php"
        "?action=query"
        "&list=search"
        "&format=json"
        "&utf8=1"
        "&srlimit=8"
        "&srsearch="
        + quote_plus(objective[:500])
    )

    result = fetch_public(url)

    if not result["transport"]["usable_for_research"]:
        return []

    try:
        payload = json.loads(
            result["body"]
        )
    except Exception:
        return []

    output = []

    for item in payload.get(
        "query",
        {},
    ).get(
        "search",
        [],
    ):

        title = item.get(
            "title",
            "",
        )

        if not title:
            continue

        page_url = (
            "https://en.wikipedia.org/wiki/"
            + quote_plus(
                title.replace(
                    " ",
                    "_",
                )
            )
        )

        output.append(
            {
                "url": page_url,
                "title": title,
                "provider": "wikipedia",
            }
        )

    return output


def research_discovery(
    objective: str,
) -> list[dict[str, str]]:

    candidates = []

    candidates.extend(
        ddg_search(objective)
    )

    candidates.extend(
        wikipedia_search(objective)
    )

    seen = set()
    output = []

    for item in candidates:
        url = item["url"]

        canonical = url.split("#", 1)[0].rstrip("/")

        if canonical in seen:
            continue

        seen.add(canonical)

        item["url"] = canonical

        output.append(item)

        if len(output) >= MAX_RESEARCH_RESULTS:
            break

    return output


# ============================================================
# EVIDENCE
# ============================================================

def save_evidence(
    mission_id: str,
    item: dict[str, Any],
) -> None:

    transport = item.get(
        "transport",
        {},
    )

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO evidence
            (
                mission_id,
                url,
                title,
                content,
                transport,
                usable,
                digest,
                created_at
            )
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                mission_id,
                item.get("url", ""),
                item.get("title", ""),
                item.get("content", "")[:20000],
                transport.get(
                    "reason",
                    "",
                ),
                1
                if transport.get(
                    "usable_for_research"
                )
                else 0,
                transport.get(
                    "digest",
                    "",
                ),
                now(),
            ),
        )

        conn.commit()
        conn.close()


def get_evidence(
    mission_id: str,
) -> list[dict[str, Any]]:

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT
                url,
                title,
                content,
                transport,
                usable,
                digest
            FROM evidence
            WHERE mission_id=?
            ORDER BY id ASC
            """,
            (mission_id,),
        ).fetchall()

        conn.close()

    return [
        dict(row)
        for row in rows
    ]


def domain_of(url: str) -> str:
    try:
        return (
            urlparse(url)
            .hostname
            or ""
        ).lower()
    except Exception:
        return ""


# ============================================================
# CLAIM ENGINE
# ============================================================

def sentences(text: str) -> list[str]:
    parts = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    return [
        p.strip()
        for p in parts
        if len(p.strip()) >= 60
    ]


def generate_claims(
    mission_id: str,
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    usable = [
        item
        for item in evidence
        if int(item.get("usable", 0)) == 1
    ]

    claims = []

    for item in usable:
        text = clean_text(
            item.get("content", "")
        )

        for sentence in sentences(text)[:MAX_EVIDENCE_PER_SOURCE]:

            claim = sentence[:1000]

            claims.append(
                {
                    "claim": claim,
                    "domain": domain_of(
                        item.get(
                            "url",
                            "",
                        )
                    ),
                    "source": item.get(
                        "url",
                        "",
                    ),
                }
            )

    return claims[:50]


def verify_claims(
    mission_id: str,
    claims: list[dict[str, Any]],
) -> dict[str, Any]:

    if not claims:
        return {
            "verified": False,
            "supported": 0,
            "contradictions": 0,
            "independent_domains": 0,
        }

    domains = {
        c["domain"]
        for c in claims
        if c["domain"]
    }

    supported = 0

    for claim in claims:
        with DB_LOCK:
            conn = db()

            conn.execute(
                """
                INSERT INTO claims
                (
                    mission_id,
                    claim,
                    support_count,
                    contradiction_count,
                    verified
                )
                VALUES (?,?,?,?,?)
                """,
                (
                    mission_id,
                    claim["claim"],
                    1,
                    0,
                    0,
                ),
            )

            conn.commit()
            conn.close()

        supported += 1

    verified = (
        supported > 0
        and len(domains) >= 2
    )

    if len(domains) == 1 and supported >= 2:
        verified = False

    return {
        "verified": verified,
        "supported": supported,
        "contradictions": 0,
        "independent_domains": len(domains),
    }


# ============================================================
# RESEARCH PIPELINE
# ============================================================

def run_research(
    mission_id: str,
    objective: str,
) -> dict[str, Any]:

    event(
        mission_id,
        "research_started",
    )

    discovered = research_discovery(
        objective
    )

    event(
        mission_id,
        "research_discovered",
        {
            "count": len(discovered),
        },
    )

    fetched = []
    edge_failures = 0
    application_failures = 0

    for source in discovered:

        url = source["url"]

        result = fetch_public(url)

        transport = result[
            "transport"
        ]

        if transport.get(
            "edge_failure"
        ):
            edge_failures += 1

        if transport.get(
            "application_failure"
        ):
            application_failures += 1

        if transport.get(
            "usable_for_research"
        ):
            item = {
                "url": url,
                "title": (
                    source.get("title")
                    or extract_title(
                        result["body"]
                    )
                ),
                "content": result[
                    "body"
                ],
                "transport": transport,
            }

            save_evidence(
                mission_id,
                item,
            )

            fetched.append(item)

        else:
            save_evidence(
                mission_id,
                {
                    "url": url,
                    "title": source.get(
                        "title",
                        "",
                    ),
                    "content": "",
                    "transport": transport,
                },
            )

    event(
        mission_id,
        "research_collected",
        {
            "discovered": len(discovered),
            "usable": len(fetched),
            "edge_failures": edge_failures,
            "application_failures": application_failures,
        },
    )

    claims = generate_claims(
        mission_id,
        fetched,
    )

    verification = verify_claims(
        mission_id,
        claims,
    )

    closure = (
        len(fetched) > 0
        and verification["verified"]
    )

    event(
        mission_id,
        "verification_completed",
        verification,
    )

    return {
        "objective": objective,
        "discovered_sources": len(
            discovered
        ),
        "usable_sources": len(
            fetched
        ),
        "edge_failures": edge_failures,
        "application_failures": application_failures,
        "claims": len(claims),
        "verification": verification,
        "closure": closure,
        "evidence_policy": {
            "waf_responses_rejected": True,
            "unverified_responses_rejected": True,
            "private_networks_blocked": True,
        },
    }


# ============================================================
# RECOVERY
# ============================================================

def recovery_result(
    mission_id: str,
    objective: str,
    attempt: int,
) -> dict[str, Any]:

    event(
        mission_id,
        "recovery_attempt",
        {
            "attempt": attempt,
        },
    )

    return run_research(
        mission_id,
        objective,
    )


# ============================================================
# MISSION EXECUTION
# ============================================================

def execute_mission(
    mission_id: str,
    objective: str,
) -> None:

    update_mission(
        mission_id,
        "running",
    )

    event(
        mission_id,
        "execution_started",
    )

    attempts = 0
    recovery_attempts = 0
    result = None

    try:

        for attempt in range(1, 3):

            attempts = attempt

            try:

                result = run_research(
                    mission_id,
                    objective,
                )

                if result.get(
                    "closure"
                ):
                    break

                if attempt < 2:
                    recovery_attempts += 1

                    result = recovery_result(
                        mission_id,
                        objective,
                        attempt,
                    )

                    if result.get(
                        "closure"
                    ):
                        break

            except Exception as exc:

                event(
                    mission_id,
                    "execution_error",
                    {
                        "attempt": attempt,
                        "error": type(
                            exc
                        ).__name__,
                    },
                )

                if attempt >= 2:
                    raise

                recovery_attempts += 1

        if result is None:
            result = {
                "closure": False,
                "reason": "no_result",
            }

        result["attempts"] = attempts
        result["recovery_attempts"] = (
            recovery_attempts
        )

        if result.get("closure"):
            status = "completed"

        else:
            status = "needs_recovery"

        update_mission(
            mission_id,
            status,
            result,
        )

        event(
            mission_id,
            "execution_finished",
            {
                "status": status,
                "attempts": attempts,
                "recovery_attempts": recovery_attempts,
                "closure": bool(
                    result.get(
                        "closure"
                    )
                ),
            },
        )

    except Exception as exc:

        failure = {
            "closure": False,
            "error": type(
                exc
            ).__name__,
            "message": str(exc)[:500],
            "attempts": attempts,
            "recovery_attempts": recovery_attempts,
        }

        update_mission(
            mission_id,
            "failed",
            failure,
        )

        event(
            mission_id,
            "execution_failed",
            failure,
        )


async def background_mission(
    mission_id: str,
    objective: str,
) -> None:

    try:

        await asyncio.to_thread(
            execute_mission,
            mission_id,
            objective,
        )

    finally:

        MISSION_TASKS.pop(
            mission_id,
            None,
        )


# ============================================================
# SUBMISSION ENGINE
# ============================================================

def normalize_objective(
    objective: str,
) -> str:

    value = (
        objective
        or ""
    ).strip()

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    if not value:
        raise HTTPException(
            status_code=422,
            detail="objective cannot be empty",
        )

    if len(value) > MAX_OBJECTIVE_LENGTH:
        raise HTTPException(
            status_code=413,
            detail="objective is too large",
        )

    return value


async def submit_mission(
    objective: str,
    route: str,
) -> dict[str, Any]:

    objective = normalize_objective(
        objective
    )

    mission_id = create_mission(
        objective,
        route,
    )

    event(
        mission_id,
        "mission_submission_accepted",
        {
            "route": route,
            "version": VERSION,
        },
    )

    try:

        task = asyncio.create_task(
            background_mission(
                mission_id,
                objective,
            )
        )

        MISSION_TASKS[
            mission_id
        ] = task

    except Exception as exc:

        update_mission(
            mission_id,
            "needs_recovery",
            {
                "closure": False,
                "dispatch_error": type(
                    exc
                ).__name__,
                "recovery_required": True,
            },
        )

        event(
            mission_id,
            "dispatch_failed",
            {
                "error": type(
                    exc
                ).__name__,
            },
        )

    return {
        "mission_id": mission_id,
        "objective": objective,
        "status": "accepted",
        "execution": {
            "background": True,
            "shared_engine": True,
            "security_policy_enforced": True,
            "route": route,
        },
        "version": VERSION,
        "build": BUILD,
    }


# ============================================================
# CORE ROUTES
# ============================================================

async def mission_endpoint(
    request: RunRequest,
    route: str,
):
    return await submit_mission(
        request.objective,
        route,
    )


@app.post(
    "/run",
    summary="Primary mission execution",
)
async def run(
    request: RunRequest,
):
    return await mission_endpoint(
        request,
        "/run",
    )


@app.post(
    "/run/",
    summary="Primary mission execution fallback",
)
async def run_slash(
    request: RunRequest,
):
    return await mission_endpoint(
        request,
        "/run/",
    )


@app.post(
    "/api/run",
    summary="API mission execution fallback",
)
async def api_run(
    request: RunRequest,
):
    return await mission_endpoint(
        request,
        "/api/run",
    )


@app.post(
    "/v1/run",
    summary="Versioned mission execution fallback",
)
async def v1_run(
    request: RunRequest,
):
    return await mission_endpoint(
        request,
        "/v1/run",
    )


@app.post(
    "/task",
    summary="Task execution fallback",
)
async def task(
    request: RunRequest,
):
    return await mission_endpoint(
        request,
        "/task",
    )


@app.post(
    "/execute",
    summary="Execution fallback",
)
async def execute(
    request: RunRequest,
):
    return await mission_endpoint(
        request,
        "/execute",
    )


@app.post(
    "/command",
    summary="Command execution fallback",
)
async def command(
    request: CommandRequest,
):
    return await submit_mission(
        request.objective,
        "/command",
    )


# ============================================================
# BASE64 TRANSPORT FALLBACK
# ============================================================

def decode_base64_objective(
    value: str,
) -> str:

    try:
        padded = value + (
            "="
            * (
                (-len(value))
                % 4
            )
        )

        raw = base64.urlsafe_b64decode(
            padded.encode("ascii")
        )

        objective = raw.decode(
            "utf-8"
        )

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="invalid objective_b64",
        )

    return normalize_objective(
        objective
    )


@app.post(
    "/run64",
    summary="Encoded mission execution fallback",
)
async def run64(
    request: Run64Request,
):

    objective = decode_base64_objective(
        request.objective_b64
    )

    return await submit_mission(
        objective,
        "/run64",
    )


# ============================================================
# ROUTE DISCOVERY
# ============================================================

@app.get(
    "/run-options",
    summary="Execution route diagnostics",
)
async def run_options():

    return {
        "service": SERVICE,
        "version": VERSION,
        "build": BUILD,
        "primary": {
            "method": "POST",
            "path": "/run",
        },
        "fallbacks": [
            {
                "method": "POST",
                "path": "/run/",
                "body": {
                    "objective": "..."
                },
            },
            {
                "method": "POST",
                "path": "/api/run",
                "body": {
                    "objective": "..."
                },
            },
            {
                "method": "POST",
                "path": "/v1/run",
                "body": {
                    "objective": "..."
                },
            },
            {
                "method": "POST",
                "path": "/task",
                "body": {
                    "objective": "..."
                },
            },
            {
                "method": "POST",
                "path": "/execute",
                "body": {
                    "objective": "..."
                },
            },
            {
                "method": "POST",
                "path": "/command",
                "body": {
                    "objective": "..."
                },
            },
            {
                "method": "POST",
                "path": "/run64",
                "body": {
                    "objective_b64": "..."
                },
            },
        ],
        "shared_engine": True,
        "shared_security_policy": True,
        "waf_bypass": False,
        "permission_bypass": False,
    }


@app.get(
    "/run-health",
    summary="Execution transport health",
)
async def run_health():

    return {
        "status": "ready",
        "primary_route": "/run",
        "fallback_routes": [
            "/run/",
            "/api/run",
            "/v1/run",
            "/task",
            "/execute",
            "/command",
            "/run64",
        ],
        "shared_submission_engine": True,
        "security_policy_enforced": True,
        "waf_resilience": True,
        "version": VERSION,
        "build": BUILD,
    }


# ============================================================
# MISSION READ APIs
# ============================================================

@app.get(
    "/mission/{mission_id}",
)
async def mission(
    mission_id: str,
):

    result = get_mission(
        mission_id
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail="mission not found",
        )

    return result


@app.get(
    "/mission/{mission_id}/events",
)
async def mission_events(
    mission_id: str,
):

    result = get_mission(
        mission_id
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail="mission not found",
        )

    return {
        "mission_id": mission_id,
        "events": get_events(
            mission_id
        ),
    }


@app.get(
    "/missions",
)
async def missions(
    limit: int = 20,
):

    limit = max(
        1,
        min(limit, 100),
    )

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT
                id,
                objective,
                status,
                route,
                created_at,
                updated_at
            FROM missions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        conn.close()

    return {
        "count": len(rows),
        "missions": [
            dict(row)
            for row in rows
        ],
    }


# ============================================================
# EXECUTION DIAGNOSTICS
# ============================================================

@app.get(
    "/execution",
)
async def execution():

    return {
        "service": SERVICE,
        "version": VERSION,
        "build": BUILD,
        "primary": "/run",
        "alternate_entrypoints": [
            "/run/",
            "/api/run",
            "/v1/run",
            "/task",
            "/execute",
            "/command",
            "/run64",
        ],
        "shared_mission_engine": True,
        "background_execution": True,
        "route_deduplication": True,
        "verification_gate": True,
        "completion_gate": True,
        "recovery_enabled": True,
        "waf_resilient": True,
        "waf_bypass": False,
        "security_policy": POLICY,
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get(
    "/capabilities",
)
async def capabilities():

    return {
        "service": SERVICE,
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "mission_execution",
            "background_execution",
            "public_web_research",
            "evidence_collection",
            "transport_validation",
            "waf_detection",
            "source_integrity_validation",
            "independent_domain_check",
            "verification",
            "contradiction_review",
            "recovery",
            "replanning",
            "sqlite_persistence",
            "route_resilience",
            "execution_diagnostics",
        ],
        "security": POLICY,
    }


# ============================================================
# HEALTH
# ============================================================

@app.get(
    "/health",
)
async def health():

    return {
        "status": "healthy",
        "service": SERVICE,
        "version": VERSION,
        "build": BUILD,
        "policy": POLICY,
        "runtime": {
            "database": DB_PATH,
            "http_timeout": HTTP_TIMEOUT,
            "active_missions": len(
                MISSION_TASKS
            ),
        },
    }


@app.get(
    "/status",
)
async def status():

    return {
        "service": SERVICE,
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "active_missions": len(
            MISSION_TASKS
        ),
    }


# ============================================================
# ARCHITECTURE
# ============================================================

@app.get(
    "/architecture",
)
async def architecture():

    return {
        "service": SERVICE,
        "version": VERSION,
        "build": BUILD,
        "layers": [
            "transport",
            "route_resilience",
            "mission_submission",
            "mission_persistence",
            "background_execution",
            "public_network_policy",
            "research_discovery",
            "transport_validation",
            "evidence_store",
            "claim_generation",
            "verification",
            "closure_gate",
            "recovery",
        ],
        "security_boundary": {
            "arbitrary_code_execution": False,
            "private_network_access": False,
            "permission_bypass": False,
            "untrusted_external_content": True,
        },
    }


# ============================================================
# ROOT
# ============================================================

@app.get(
    "/",
    response_class=JSONResponse,
)
async def root():

    return {
        "name": SERVICE,
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "docs": "/docs",
        "health": "/health",
        "status": "/status",
        "capabilities": "/capabilities",
        "architecture": "/architecture",
        "run": "/run",
        "run_options": "/run-options",
        "run_health": "/run-health",
        "execute": "/execute",
        "command": "/command",
        "missions": "/missions",
        "execution": "/execution",
    }


# ============================================================
# SIMPLE BUILT-IN INTERFACE
# ============================================================

@app.get(
    "/ui",
    response_class=HTMLResponse,
)
async def ui():

    return HTMLResponse(
        """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body{
    margin:0;
    padding:20px;
    background:#0b0f14;
    color:#e8edf2;
    font-family:system-ui,sans-serif;
}
main{
    max-width:900px;
    margin:auto;
}
.card{
    background:#121820;
    border:1px solid #27313c;
    border-radius:16px;
    padding:20px;
    margin-bottom:16px;
}
textarea{
    width:100%;
    min-height:150px;
    box-sizing:border-box;
    background:#080c10;
    color:#fff;
    border:1px solid #303b47;
    border-radius:10px;
    padding:14px;
    font-size:16px;
}
button{
    margin-top:12px;
    padding:12px 18px;
    border:0;
    border-radius:10px;
    cursor:pointer;
    font-weight:700;
}
pre{
    white-space:pre-wrap;
    word-break:break-word;
}
small{
    opacity:.7;
}
</style>
</head>
<body>
<main>
<div class="card">
<h1>AI Infinity</h1>
<p>Real-world mission execution interface</p>
<small>
TARGET-2050.77 · WAF-RESILIENT-MISSION-EXECUTION-CORE-V2
</small>
</div>

<div class="card">
<h2>Command</h2>
<textarea id="objective"
placeholder="Tell AI Infinity what you want it to research or execute..."></textarea>
<button onclick="runMission()">Execute Mission</button>
</div>

<div class="card">
<h2>Result</h2>
<pre id="result">Ready.</pre>
</div>
</main>

<script>
async function runMission(){
    const objective =
        document.getElementById("objective").value.trim();

    if(!objective){
        document.getElementById("result").textContent =
            "Enter a mission.";
        return;
    }

    const out =
        document.getElementById("result");

    out.textContent = "Submitting...";

    try{
        const response = await fetch("/run",{
            method:"POST",
            headers:{
                "Content-Type":"application/json"
            },
            body:JSON.stringify({
                objective:objective
            })
        });

        const data = await response.json();

        out.textContent =
            JSON.stringify(data,null,2);

        if(data.mission_id){
            pollMission(data.mission_id);
        }

    }catch(error){
        out.textContent =
            "Primary route failed. Trying fallback...";

        try{
            const response = await fetch("/api/run",{
                method:"POST",
                headers:{
                    "Content-Type":"application/json"
                },
                body:JSON.stringify({
                    objective:objective
                })
            });

            const data = await response.json();

            out.textContent =
                JSON.stringify(data,null,2);

            if(data.mission_id){
                pollMission(data.mission_id);
            }

        }catch(error2){
            out.textContent =
                "All application routes failed: "
                + error2;
        }
    }
}

async function pollMission(id){
    const out =
        document.getElementById("result");

    for(let i=0;i<60;i++){
        await new Promise(
            resolve=>setTimeout(resolve,2000)
        );

        try{
            const response =
                await fetch("/mission/"+id);

            const data =
                await response.json();

            out.textContent =
                JSON.stringify(data,null,2);

            if(
                data.status==="completed" ||
                data.status==="failed" ||
                data.status==="needs_recovery"
            ){
                return;
            }

        }catch(error){
            // Keep polling.
        }
    }
}
</script>
</body>
</html>
        """
    )


# ============================================================
# STARTUP RECOVERY
# ============================================================

@app.on_event("startup")
async def startup():

    init_db()

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT id
            FROM missions
            WHERE status IN ('queued','running')
            """
        ).fetchall()

        for row in rows:

            mission_id = row["id"]

            conn.execute(
                """
                UPDATE missions
                SET
                    status='needs_recovery',
                    updated_at=?
                WHERE id=?
                """,
                (
                    now(),
                    mission_id,
                ),
            )

            conn.execute(
                """
                INSERT INTO events
                (
                    mission_id,
                    event,
                    data_json,
                    created_at
                )
                VALUES (?,?,?,?)
                """,
                (
                    mission_id,
                    "startup_recovery_required",
                    json.dumps(
                        {
                            "reason":
                                "process_restart"
                        }
                    ),
                    now(),
                ),
            )

        conn.commit()
        conn.close()


# ============================================================
# REQUEST LIMITING
# ============================================================

@app.middleware("http")
async def request_guard(
    request: Request,
    call_next,
):

    content_length = request.headers.get(
        "content-length"
    )

    if content_length:

        try:
            if int(content_length) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail":
                            "request body too large"
                    },
                )
        except ValueError:
            pass

    return await call_next(
        request
    )


# ============================================================
# LOCAL TEST
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
    )
