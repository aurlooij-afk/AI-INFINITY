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

from html.parser import HTMLParser
from typing import Optional
from urllib.error import HTTPError
from urllib.parse import quote_plus, unquote, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request as URLRequest,
    build_opener,
)

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY
# TARGET-2050.71
# BUILD:
# SELF-CONSISTENT-EDGE-TRANSPORT-CONTRACT-AND-RECOVERY-CORE
#
# Builds on the 2050.69/2050.70 architecture:
# - workflow persistence
# - mission lifecycle
# - evidence validation
# - transport/application separation
# - WAF detection
# - SSRF protection
# - controlled public web access
# - recovery
# - truthful completion gate
#
# Critical 2050.70 regression fixed:
# /transport/classify accepts TransportRequest correctly.
# ============================================================


VERSION = "TARGET-2050.71"

BUILD = (
    "SELF-CONSISTENT-EDGE-TRANSPORT-CONTRACT-AND-RECOVERY-CORE"
)

DB_PATH = os.getenv(
    "AI_INFINITY_DB",
    "/tmp/ai_infinity.db",
)

MAX_BODY = 1_500_000
MAX_OBJECTIVE = 20_000
FETCH_TIMEOUT = float(
    os.getenv("AI_INFINITY_FETCH_TIMEOUT", "10")
)
MAX_SOURCES = int(
    os.getenv("AI_INFINITY_MAX_SOURCES", "8")
)
MAX_ATTEMPTS = 3

DB_LOCK = threading.RLock()


app = FastAPI(
    title="AI Infinity",
    description=(
        "AI Infinity autonomous workflow runtime with "
        "self-consistent edge transport and application separation."
    ),
    version=VERSION,
)


# ============================================================
# REQUEST CONTRACTS
# ============================================================


class RunRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=MAX_OBJECTIVE,
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
# HTML EXTRACTION
# ============================================================


class HTMLTextParser(HTMLParser):
    def __init__(self):
        super().__init__()

        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag in {
            "script",
            "style",
            "noscript",
            "svg",
            "head",
        }:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in {
            "script",
            "style",
            "noscript",
            "svg",
            "head",
        }:
            if self.skip_depth:
                self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth:
            return

        value = " ".join(data.split())

        if value:
            self.parts.append(value)


# ============================================================
# GENERAL HELPERS
# ============================================================


def now():
    return time.time()


def json_dump(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def get_db():
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )

    connection.row_factory = sqlite3.Row

    return connection


# ============================================================
# DATABASE
# ============================================================


def init_db():
    with DB_LOCK, get_db() as db:
        db.execute("PRAGMA journal_mode=WAL")

        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                error TEXT,
                created_at REAL,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                kind TEXT,
                payload_json TEXT,
                ts REAL
            );

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                url TEXT,
                domain TEXT,
                status TEXT,
                edge_class TEXT,
                http_status INTEGER,
                content_type TEXT,
                title TEXT,
                digest TEXT,
                independence_key TEXT,
                reason TEXT,
                sample TEXT,
                fetched_at REAL
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                source_id INTEGER,
                claim TEXT,
                excerpt TEXT,
                quality TEXT,
                verified INTEGER,
                independence_key TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                text TEXT,
                status TEXT,
                evidence_count INTEGER DEFAULT 0,
                contradiction_count INTEGER DEFAULT 0,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS contradictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                claim_id INTEGER,
                evidence_id INTEGER,
                type TEXT,
                description TEXT,
                resolved INTEGER DEFAULT 0,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS transport_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                classification TEXT,
                http_status INTEGER,
                content_type TEXT,
                reason TEXT,
                digest TEXT,
                ts REAL
            );

            CREATE INDEX IF NOT EXISTS
                idx_events_mission
                ON events(mission_id, ts);

            CREATE INDEX IF NOT EXISTS
                idx_sources_mission
                ON sources(mission_id);

            CREATE INDEX IF NOT EXISTS
                idx_evidence_mission
                ON evidence(mission_id);

            CREATE INDEX IF NOT EXISTS
                idx_transport_mission
                ON transport_events(mission_id);
            """
        )


init_db()


# ============================================================
# DATABASE OPERATIONS
# ============================================================


def record_event(mission_id, kind, payload):
    with DB_LOCK, get_db() as db:
        db.execute(
            """
            INSERT INTO events(
                mission_id,
                kind,
                payload_json,
                ts
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                mission_id,
                kind,
                json_dump(payload),
                now(),
            ),
        )


def update_mission(mission_id, **fields):
    fields["updated_at"] = now()

    columns = []
    values = []

    for key, value in fields.items():
        columns.append(f"{key}=?")
        values.append(value)

    values.append(mission_id)

    with DB_LOCK, get_db() as db:
        db.execute(
            f"""
            UPDATE missions
            SET {",".join(columns)}
            WHERE id=?
            """,
            values,
        )


def get_mission(mission_id):
    with DB_LOCK, get_db() as db:
        row = db.execute(
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

    result["result"] = (
        json.loads(result["result_json"])
        if result.get("result_json")
        else None
    )

    result.pop("result_json", None)

    return result


# ============================================================
# TRANSPORT CLASSIFICATION
# ============================================================


def classify_transport_payload(
    http_status=None,
    content_type=None,
    body=None,
    headers=None,
):
    content_type = content_type or ""
    body = body or ""
    headers = headers or {}

    lower_body = body[:200_000].lower()

    digest = (
        hashlib.sha256(
            body.encode(
                "utf-8",
                "replace",
            )
        ).hexdigest()
        if body
        else None
    )

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

    is_html = (
        "text/html" in content_type.lower()
        or "<html" in lower_body
        or "<!doctype html" in lower_body
        or "<title>" in lower_body
    )

    is_binary = any(
        marker in content_type.lower()
        for marker in (
            "font/",
            "image/",
            "audio/",
            "video/",
            "application/octet-stream",
        )
    )

    embedded_asset = (
        "data:font/" in lower_body
        or (
            "base64," in lower_body
            and "@font-face" in lower_body
            and len(body) > 10_000
        )
    )

    if (
        http_status in {
            401,
            403,
            406,
            429,
            451,
            503,
        }
        and is_html
        and any(
            marker in lower_body
            for marker in waf_markers
        )
    ):
        classification = "EDGE_WAF_BLOCK"

        return {
            "classification": classification,
            "edge_failure": True,
            "application_failure": False,
            "is_evidence": False,
            "usable_for_research": False,
            "http_status": http_status,
            "content_type": content_type,
            "reason": (
                "HTTP edge/WAF block page detected; "
                "response is not research evidence."
            ),
            "digest": digest,
        }

    if http_status in {
        401,
        403,
        406,
        429,
        451,
    }:
        return {
            "classification": "EDGE_REJECTED",
            "edge_failure": True,
            "application_failure": False,
            "is_evidence": False,
            "usable_for_research": False,
            "http_status": http_status,
            "content_type": content_type,
            "reason": (
                "HTTP edge rejection; "
                "response is not trusted evidence."
            ),
            "digest": digest,
        }

    if http_status is not None and http_status >= 500:
        return {
            "classification": "UPSTREAM_5XX",
            "edge_failure": False,
            "application_failure": True,
            "is_evidence": False,
            "usable_for_research": False,
            "http_status": http_status,
            "content_type": content_type,
            "reason": (
                "Server-side failure; "
                "content cannot be used as evidence."
            ),
            "digest": digest,
        }

    if is_binary or embedded_asset:
        return {
            "classification": "OPAQUE_ASSET",
            "edge_failure": False,
            "application_failure": False,
            "is_evidence": False,
            "usable_for_research": False,
            "http_status": http_status,
            "content_type": content_type,
            "reason": (
                "Binary/embedded asset payload "
                "is not research evidence."
            ),
            "digest": digest,
        }

    if (
        is_html
        and any(
            marker in lower_body
            for marker in waf_markers
        )
    ):
        return {
            "classification": "BLOCKED_HTML",
            "edge_failure": True,
            "application_failure": False,
            "is_evidence": False,
            "usable_for_research": False,
            "http_status": http_status,
            "content_type": content_type,
            "reason": (
                "HTML block/challenge page detected."
            ),
            "digest": digest,
        }

    if not body.strip():
        return {
            "classification": "EMPTY_RESPONSE",
            "edge_failure": False,
            "application_failure": False,
            "is_evidence": False,
            "usable_for_research": False,
            "http_status": http_status,
            "content_type": content_type,
            "reason": (
                "Empty response cannot support evidence."
            ),
            "digest": digest,
        }

    if (
        http_status is not None
        and 200 <= http_status < 300
    ):
        return {
            "classification": "VALID_PUBLIC_CONTENT",
            "edge_failure": False,
            "application_failure": False,
            "is_evidence": True,
            "usable_for_research": True,
            "http_status": http_status,
            "content_type": content_type,
            "reason": (
                "Successful public response passed "
                "transport checks."
            ),
            "digest": digest,
        }

    return {
        "classification": "UNVERIFIED_RESPONSE",
        "edge_failure": False,
        "application_failure": False,
        "is_evidence": False,
        "usable_for_research": False,
        "http_status": http_status,
        "content_type": content_type,
        "reason": (
            "Response did not meet the trusted-content contract."
        ),
        "digest": digest,
    }


# ============================================================
# SSRF / PUBLIC NETWORK POLICY
# ============================================================


def validate_public_host(host):
    if not host:
        raise ValueError("missing host")

    host = host.strip("[]").lower().rstrip(".")

    if (
        host == "localhost"
        or host.endswith(".local")
        or host == "localhost.localdomain"
    ):
        raise ValueError(
            "local host blocked"
        )

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
            raise ValueError(
                "private/reserved address blocked"
            )

        return

    except ValueError as exc:
        if str(exc) == "private/reserved address blocked":
            raise

    addresses = socket.getaddrinfo(
        host,
        443,
        type=socket.SOCK_STREAM,
    )

    if not addresses:
        raise ValueError(
            "host did not resolve"
        )

    for address in addresses:
        resolved_ip = ipaddress.ip_address(
            address[4][0]
        )

        if (
            resolved_ip.is_private
            or resolved_ip.is_loopback
            or resolved_ip.is_link_local
            or resolved_ip.is_multicast
            or resolved_ip.is_reserved
            or resolved_ip.is_unspecified
        ):
            raise ValueError(
                "resolved private/reserved address blocked"
            )


def validate_public_url(url):
    parsed = urlparse(url)

    if parsed.scheme not in {
        "http",
        "https",
    }:
        raise ValueError(
            "only public http/https URLs are allowed"
        )

    if parsed.username or parsed.password:
        raise ValueError(
            "URL credentials are not allowed"
        )

    validate_public_host(
        parsed.hostname
    )

    return url


class SafeRedirectHandler(
    HTTPRedirectHandler
):
    def redirect_request(
        self,
        request,
        response,
        code,
        message,
        headers,
        newurl,
    ):
        validate_public_url(newurl)

        return super().redirect_request(
            request,
            response,
            code,
            message,
            headers,
            newurl,
        )


# ============================================================
# PUBLIC FETCH
# ============================================================


def fetch_public_url(url):
    validate_public_url(url)

    request = URLRequest(
        url,
        headers={
            "User-Agent": (
                "AI-Infinity/2050.71"
            ),
            "Accept": (
                "text/html,"
                "application/xhtml+xml,"
                "text/plain,"
                "application/json;q=0.9,"
                "*/*;q=0.2"
            ),
        },
        method="GET",
    )

    opener = build_opener(
        SafeRedirectHandler,
        ProxyHandler({}),
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

            headers = dict(
                response.headers.items()
            )

            content_type = (
                response.headers.get(
                    "Content-Type"
                )
                or ""
            )

            raw = response.read(
                MAX_BODY + 1
            )

            if len(raw) > MAX_BODY:
                raw = raw[:MAX_BODY]

            encoding = (
                response.headers.get(
                    "Content-Encoding"
                )
                or ""
            ).lower()

            if encoding == "gzip":
                import gzip

                try:
                    raw = gzip.decompress(
                        raw
                    )[:MAX_BODY]
                except Exception:
                    pass

            charset = (
                response.headers.get_content_charset()
                or "utf-8"
            )

            body = raw.decode(
                charset,
                "replace",
            )

            return (
                url,
                status,
                content_type,
                headers,
                body,
                None,
            )

    except HTTPError as exc:
        raw = exc.read(
            MAX_BODY + 1
        )

        content_type = (
            exc.headers.get(
                "Content-Type"
            )
            or ""
        )

        charset = (
            exc.headers.get_content_charset()
            or "utf-8"
        )

        body = raw.decode(
            charset,
            "replace",
        )

        return (
            url,
            exc.code,
            content_type,
            dict(exc.headers.items()),
            body,
            None,
        )

    except Exception as exc:
        return (
            url,
            None,
            "",
            {},
            "",
            str(exc),
        )


# ============================================================
# CONTENT NORMALIZATION
# ============================================================


def normalize_visible_text(
    body,
    content_type,
):
    if (
        "html" in content_type.lower()
        or "<html" in body[:500].lower()
    ):
        parser = HTMLTextParser()

        try:
            parser.feed(body)

            text = " ".join(
                parser.parts
            )

            return (
                text[:12_000],
                text[:300],
            )

        except Exception:
            pass

    return (
        " ".join(body.split())[:12_000],
        "",
    )


# ============================================================
# URL DISCOVERY
# ============================================================


def extract_links(
    body,
    base_url,
):
    found = []

    pattern = re.compile(
        r'''(?:href|url)\s*=\s*["']([^"']+)["']''',
        re.IGNORECASE,
    )

    for match in pattern.finditer(body):
        candidate = unquote(
            match.group(1)
        )

        if candidate.startswith("//"):
            candidate = (
                "https:" + candidate
            )

        if candidate.startswith("/"):
            parsed = urlparse(
                base_url
            )

            candidate = (
                f"{parsed.scheme}://"
                f"{parsed.netloc}"
                f"{candidate}"
            )

        if not candidate.startswith(
            "http"
        ):
            continue

        if (
            "duckduckgo.com"
            in urlparse(candidate).netloc
        ):
            continue

        try:
            validate_public_url(
                candidate
            )
            found.append(candidate)

        except Exception:
            continue

    return list(
        dict.fromkeys(found)
    )


def search_public_web(query):
    search_url = (
        "https://html.duckduckgo.com/html/?q="
        + quote_plus(query)
    )

    (
        url,
        status,
        content_type,
        headers,
        body,
        error,
    ) = fetch_public_url(
        search_url
    )

    if error:
        return [], {
            "classification": "FETCH_ERROR",
            "usable_for_research": False,
            "reason": error,
        }

    classification = (
        classify_transport_payload(
            http_status=status,
            content_type=content_type,
            body=body,
            headers=headers,
        )
    )

    if not classification[
        "usable_for_research"
    ]:
        return [], classification

    return (
        extract_links(
            body,
            url,
        ),
        classification,
    )


def extract_direct_urls(
    objective,
):
    candidates = []

    for match in re.findall(
        r'https?://[^\s<>"\']+',
        objective,
    ):
        url = match.rstrip(
            ".,);]"
        )

        try:
            validate_public_url(
                url
            )
            candidates.append(url)

        except Exception:
            pass

    return list(
        dict.fromkeys(candidates)
    )


# ============================================================
# RESEARCH ENGINE
# ============================================================


def perform_research(
    mission_id,
    objective,
):
    record_event(
        mission_id,
        "workflow_phase",
        {
            "phase": "planning"
        },
    )

    update_mission(
        mission_id,
        phase="planning",
        status="running",
    )

    candidates = extract_direct_urls(
        objective
    )

    transport_failures = []

    queries = [
        objective,
        (
            objective
            + " empirical evidence "
              "study benchmark"
        ),
        (
            objective
            + " systematic review "
              "limitations independent evidence"
        ),
    ]

    if not candidates:

        for query in queries:

            urls, info = search_public_web(
                query[:1800]
            )

            candidates.extend(urls)

            classification = info.get(
                "classification"
            )

            if (
                classification
                and classification
                != "VALID_PUBLIC_CONTENT"
            ):
                transport_failures.append(
                    info
                )

            if (
                len(candidates)
                >= MAX_SOURCES
            ):
                break

    candidates = list(
        dict.fromkeys(candidates)
    )[:MAX_SOURCES]

    record_event(
        mission_id,
        "replan",
        {
            "candidate_sources": len(
                candidates
            ),
            "transport_failures": len(
                transport_failures
            ),
        },
    )

    update_mission(
        mission_id,
        phase="research",
        status="running",
    )

    valid_evidence = 0
    independent_domains = set()
    blocked_sources = 0

    for url in candidates:

        domain = (
            urlparse(url)
            .hostname
            or ""
        ).lower()

        (
            fetched_url,
            status,
            content_type,
            headers,
            body,
            error,
        ) = fetch_public_url(url)

        if error:

            classification = {
                "classification": "FETCH_ERROR",
                "edge_failure": False,
                "application_failure": False,
                "is_evidence": False,
                "usable_for_research": False,
                "http_status": status,
                "content_type": content_type,
                "reason": error,
                "digest": None,
            }

        else:

            classification = (
                classify_transport_payload(
                    http_status=status,
                    content_type=content_type,
                    body=body,
                    headers=headers,
                )
            )

        digest = (
            classification.get(
                "digest"
            )
            or hashlib.sha256(
                body.encode(
                    "utf-8",
                    "replace",
                )
            ).hexdigest()
        )

        sample = body[:1800]

        with DB_LOCK, get_db() as db:

            db.execute(
                """
                INSERT INTO transport_events(
                    mission_id,
                    classification,
                    http_status,
                    content_type,
                    reason,
                    digest,
                    ts
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    classification[
                        "classification"
                    ],
                    status,
                    content_type,
                    classification[
                        "reason"
                    ],
                    digest,
                    now(),
                ),
            )

            db.execute(
                """
                INSERT INTO sources(
                    mission_id,
                    url,
                    domain,
                    status,
                    edge_class,
                    http_status,
                    content_type,
                    title,
                    digest,
                    independence_key,
                    reason,
                    sample,
                    fetched_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    mission_id,
                    fetched_url,
                    domain,
                    (
                        "usable"
                        if classification[
                            "usable_for_research"
                        ]
                        else "rejected"
                    ),
                    classification[
                        "classification"
                    ],
                    status,
                    content_type,
                    "",
                    digest,
                    domain,
                    classification[
                        "reason"
                    ],
                    sample,
                    now(),
                ),
            )

            source_id = db.execute(
                """
                SELECT last_insert_rowid()
                """
            ).fetchone()[0]

        record_event(
            mission_id,
            "transport_classified",
            {
                "source_id": source_id,
                "url": fetched_url,
                "classification":
                    classification[
                        "classification"
                    ],
                "usable_for_research":
                    classification[
                        "usable_for_research"
                    ],
            },
        )

        if not classification[
            "usable_for_research"
        ]:

            if (
                classification[
                    "classification"
                ].startswith("EDGE_")
                or "BLOCK"
                in classification[
                    "classification"
                ]
            ):
                blocked_sources += 1

            continue

        text, title = (
            normalize_visible_text(
                body,
                content_type,
            )
        )

        if len(text) < 200:

            with DB_LOCK, get_db() as db:
                db.execute(
                    """
                    UPDATE sources
                    SET status=?,
                        reason=?
                    WHERE id=?
                    """,
                    (
                        "rejected",
                        "insufficient readable content",
                        source_id,
                    ),
                )

            continue

        valid_evidence += 1
        independent_domains.add(
            domain
        )

        excerpt = text[:900]

        claim = (
            f"The source at {domain} "
            "contains directly inspectable "
            "material relevant to the stated "
            "objective."
        )

        with DB_LOCK, get_db() as db:

            db.execute(
                """
                UPDATE sources
                SET title=?
                WHERE id=?
                """,
                (
                    title or domain,
                    source_id,
                ),
            )

            db.execute(
                """
                INSERT INTO evidence(
                    mission_id,
                    source_id,
                    claim,
                    excerpt,
                    quality,
                    verified,
                    independence_key,
                    created_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    mission_id,
                    source_id,
                    claim,
                    excerpt,
                    "validated",
                    1,
                    domain,
                    now(),
                ),
            )

            evidence_id = db.execute(
                """
                SELECT last_insert_rowid()
                """
            ).fetchone()[0]

            db.execute(
                """
                INSERT INTO claims(
                    mission_id,
                    text,
                    status,
                    evidence_count,
                    contradiction_count,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    claim,
                    "supported_source_observation",
                    1,
                    0,
                    now(),
                ),
            )

        record_event(
            mission_id,
            "evidence_verified",
            {
                "source_id": source_id,
                "evidence_id": evidence_id,
                "domain": domain,
            },
        )

    return (
        valid_evidence,
        independent_domains,
        blocked_sources,
        transport_failures,
    )


# ============================================================
# CLOSURE ENGINE
# ============================================================


def evaluate_closure(
    mission_id,
    objective,
    valid_evidence,
    independent_domains,
    blocked_sources,
    transport_failures,
):
    update_mission(
        mission_id,
        phase="verifying",
        status="running",
    )

    record_event(
        mission_id,
        "workflow_phase",
        {
            "phase": "verifying"
        },
    )

    research_mode = bool(
        re.search(
            r"\b("
            r"research|evidence|verify|"
            r"reliability|empirical|"
            r"sources?|contradict"
            r")\b",
            objective,
            re.IGNORECASE,
        )
    )

    if research_mode:

        evidence_ok = (
            valid_evidence >= 2
            and len(independent_domains) >= 2
        )

    else:

        evidence_ok = (
            valid_evidence >= 1
        )

    result = {
        "objective": objective,
        "evidence_count": valid_evidence,
        "independent_domains": len(
            independent_domains
        ),
        "blocked_sources": blocked_sources,
        "transport_failures":
            transport_failures,
        "research_mode": research_mode,
        "closure_requirements": {
            "independent_evidence":
                2 if research_mode else 1,
            "independent_domains":
                2 if research_mode else 1,
        },
        "contradiction_scan": (
            "heuristic only; semantic "
            "contradiction detection is "
            "not asserted"
        ),
    }

    with DB_LOCK, get_db() as db:
        evidence_rows = db.execute(
            """
            SELECT excerpt
            FROM evidence
            WHERE mission_id=?
            ORDER BY id DESC
            LIMIT 8
            """,
            (mission_id,),
        ).fetchall()

    result["findings"] = [
        row["excerpt"]
        for row in evidence_rows
    ]

    if not evidence_ok:

        result["status"] = (
            "needs_recovery"
        )

        result["next_actions"] = [
            (
                "Retry with alternate "
                "independent public sources"
            ),
            (
                "Inspect transport events "
                "for blocked/rejected endpoints"
            ),
            (
                "Never treat blocked HTML "
                "or opaque assets as evidence"
            ),
        ]

        update_mission(
            mission_id,
            status="needs_recovery",
            phase="recovery",
            result_json=json_dump(result),
            error=(
                "closure gate not satisfied"
            ),
        )

        record_event(
            mission_id,
            "closure_rejected",
            result,
        )

        return

    result["status"] = "completed"

    result["next_actions"] = [
        (
            "Review independently sourced "
            "evidence"
        ),
        (
            "Run a fresh verification pass "
            "before relying on high-impact "
            "conclusions"
        ),
    ]

    update_mission(
        mission_id,
        status="completed",
        phase="closed",
        result_json=json_dump(result),
        error=None,
    )

    record_event(
        mission_id,
        "closure_accepted",
        result,
    )


# ============================================================
# WORKFLOW / RECOVERY
# ============================================================


def execute_mission(
    mission_id,
    objective,
):
    for attempt in range(
        1,
        MAX_ATTEMPTS + 1,
    ):

        update_mission(
            mission_id,
            attempts=attempt,
            phase=(
                "recovery"
                if attempt > 1
                else "planning"
            ),
            status="running",
        )

        record_event(
            mission_id,
            "attempt",
            {
                "attempt": attempt
            },
        )

        try:

            (
                valid_evidence,
                independent_domains,
                blocked_sources,
                transport_failures,
            ) = perform_research(
                mission_id,
                objective,
            )

            evaluate_closure(
                mission_id,
                objective,
                valid_evidence,
                independent_domains,
                blocked_sources,
                transport_failures,
            )

            mission = get_mission(
                mission_id
            )

            if mission and mission[
                "status"
            ] in {
                "completed",
                "needs_recovery",
            }:
                return

        except Exception as exc:

            record_event(
                mission_id,
                "internal_error",
                {
                    "attempt": attempt,
                    "error": str(exc)[:1000],
                },
            )

            if attempt == MAX_ATTEMPTS:

                update_mission(
                    mission_id,
                    status="failed",
                    phase="failed",
                    error=str(exc)[:2000],
                )

                return

            time.sleep(
                min(
                    2 ** attempt,
                    5,
                )
            )


def recover_stale_missions():
    with DB_LOCK, get_db() as db:

        rows = db.execute(
            """
            SELECT id
            FROM missions
            WHERE status IN (
                'running',
                'queued'
            )
            """
        ).fetchall()

        for row in rows:

            db.execute(
                """
                UPDATE missions
                SET status=?,
                    phase=?,
                    error=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    "needs_recovery",
                    "recovery",
                    (
                        "process restart interrupted "
                        "active workflow"
                    ),
                    now(),
                    row["id"],
                ),
            )

            db.execute(
                """
                INSERT INTO events(
                    mission_id,
                    kind,
                    payload_json,
                    ts
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    row["id"],
                    "startup_recovery",
                    json_dump(
                        {
                            "reason":
                                "stale active workflow"
                        }
                    ),
                    now(),
                ),
            )


# ============================================================
# STARTUP
# ============================================================


@app.on_event("startup")
def startup():
    init_db()
    recover_stale_missions()


# ============================================================
# CORE ROUTES
# ============================================================


@app.get("/")
def root():
    return {
        "service": "AI Infinity",
        "version": VERSION,
        "build": BUILD,
        "status": "online",
    }


@app.get("/health")
def health():
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


@app.get("/ready")
def ready():
    return {
        "ready": True,
        "version": VERSION,
        "database": os.path.exists(
            DB_PATH
        ),
    }


@app.get("/policy")
def policy():
    return health()["policy"]


@app.get("/architecture")
def architecture():
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
    }


@app.get("/diagnostics")
def diagnostics():
    with DB_LOCK, get_db() as db:

        mission_rows = db.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM missions
            GROUP BY status
            """
        ).fetchall()

        transport_rows = db.execute(
            """
            SELECT classification,
                   COUNT(*) AS count
            FROM transport_events
            GROUP BY classification
            """
        ).fetchall()

    return {
        "version": VERSION,
        "build": BUILD,
        "missions": {
            row["status"]: row["count"]
            for row in mission_rows
        },
        "transport_classifications": {
            row["classification"]:
                row["count"]
            for row in transport_rows
        },
        "limits": {
            "max_body": MAX_BODY,
            "fetch_timeout":
                FETCH_TIMEOUT,
            "max_sources": MAX_SOURCES,
        },
    }


# ============================================================
# TRANSPORT CONTRACT
# ============================================================


@app.post("/transport/classify")
def transport_classify(
    request: TransportRequest,
):
    """
    TARGET-2050.71 FIX:

    This endpoint explicitly accepts
    TransportRequest.

    It must NOT validate against
    RunRequest/objective.
    """

    result = classify_transport_payload(
        http_status=request.http_status,
        content_type=request.content_type,
        body=request.body,
        headers=request.headers,
    )

    return result


@app.get("/transport/events")
def transport_events(
    limit: int = 20,
):
    limit = max(
        1,
        min(limit, 200),
    )

    with DB_LOCK, get_db() as db:

        rows = db.execute(
            """
            SELECT *
            FROM transport_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {
        "events": [
            dict(row)
            for row in rows
        ]
    }


# ============================================================
# WORKFLOW EVENTS
# ============================================================


@app.get("/workflow/events")
def workflow_events(
    limit: int = 50,
):
    limit = max(
        1,
        min(limit, 500),
    )

    with DB_LOCK, get_db() as db:

        rows = db.execute(
            """
            SELECT *
            FROM events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {
        "events": [
            dict(row)
            for row in rows
        ]
    }


# ============================================================
# MISSION CREATION
# ============================================================


@app.post("/run")
async def run(
    request: Request,
):
    raw = await request.body()

    if len(raw) > 50_000:
        raise HTTPException(
            status_code=413,
            detail="objective too large",
        )

    try:
        data = (
            json.loads(
                raw.decode("utf-8")
            )
            if raw
            else {}
        )

    except Exception:
        data = raw.decode(
            "utf-8",
            "replace",
        )

    if isinstance(data, dict):

        objective = (
            data.get("objective")
            or data.get("command")
        )

    elif isinstance(data, str):

        objective = data

    else:

        objective = None

    if not objective:
        raise HTTPException(
            status_code=422,
            detail="objective is required",
        )

    objective = str(
        objective
    ).strip()

    if not objective:
        raise HTTPException(
            status_code=422,
            detail="objective is required",
        )

    mission_id = (
        "mission-"
        + uuid.uuid4().hex[:12]
    )

    with DB_LOCK, get_db() as db:

        db.execute(
            """
            INSERT INTO missions(
                id,
                objective,
                status,
                phase,
                attempts,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                objective,
                "queued",
                "planning",
                0,
                now(),
                now(),
            ),
        )

    record_event(
        mission_id,
        "mission_created",
        {
            "objective": objective
        },
    )

    asyncio.create_task(
        asyncio.to_thread(
            execute_mission,
            mission_id,
            objective,
        )
    )

    return {
        "mission_id": mission_id,
        "objective": objective,
        "status": "queued",
        "phase": "planning",
        "version": VERSION,
    }


@app.post("/missions")
async def create_mission(
    request: Request,
):
    return await run(request)


# ============================================================
# MISSION READ
# ============================================================


@app.get("/mission/{mission_id}")
def get_single_mission(
    mission_id: str,
):
    mission = get_mission(
        mission_id
    )

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="mission not found",
        )

    return mission


@app.get("/missions")
def list_missions(
    limit: int = 20,
):
    limit = max(
        1,
        min(limit, 100),
    )

    with DB_LOCK, get_db() as db:

        rows = db.execute(
            """
            SELECT *
            FROM missions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    result = []

    for row in rows:

        item = dict(row)

        item["result"] = (
            json.loads(
                item["result_json"]
            )
            if item.get("result_json")
            else None
        )

        item.pop(
            "result_json",
            None,
        )

        result.append(item)

    return {
        "missions": result
    }


# ============================================================
# MANUAL RECOVERY
# ============================================================


@app.post(
    "/mission/{mission_id}/retry"
)
def retry_mission(
    mission_id: str,
):
    mission = get_mission(
        mission_id
    )

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="mission not found",
        )

    if mission["status"] == "running":
        raise HTTPException(
            status_code=409,
            detail="mission already running",
        )

    update_mission(
        mission_id,
        status="queued",
        phase="planning",
        error=None,
    )

    record_event(
        mission_id,
        "manual_retry",
        {},
    )

    asyncio.create_task(
        asyncio.to_thread(
            execute_mission,
            mission_id,
            mission["objective"],
        )
    )

    return {
        "mission_id": mission_id,
        "status": "queued",
        "phase": "planning",
    }


# ============================================================
# SOURCE / EVIDENCE / CLAIM INSPECTION
# ============================================================


@app.get(
    "/sources/{mission_id}"
)
def sources(
    mission_id: str,
):
    with DB_LOCK, get_db() as db:

        rows = db.execute(
            """
            SELECT
                id,
                url,
                domain,
                status,
                edge_class,
                http_status,
                content_type,
                title,
                digest,
                independence_key,
                reason,
                fetched_at
            FROM sources
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,),
        ).fetchall()

    return {
        "sources": [
            dict(row)
            for row in rows
        ]
    }


@app.get(
    "/evidence/{mission_id}"
)
def evidence(
    mission_id: str,
):
    with DB_LOCK, get_db() as db:

        rows = db.execute(
            """
            SELECT *
            FROM evidence
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,),
        ).fetchall()

    return {
        "evidence": [
            dict(row)
            for row in rows
        ]
    }


@app.get(
    "/claims/{mission_id}"
)
def claims(
    mission_id: str,
):
    with DB_LOCK, get_db() as db:

        rows = db.execute(
            """
            SELECT *
            FROM claims
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,),
        ).fetchall()

    return {
        "claims": [
            dict(row)
            for row in rows
        ]
    }
