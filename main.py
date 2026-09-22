import os
import re
import json
import time
import uuid
import socket
import ipaddress
import threading
import sqlite3
import hashlib
import xml.etree.ElementTree as ET

from urllib.parse import urlparse, urljoin, quote, unquote
from urllib.request import (
    Request,
    build_opener,
    HTTPRedirectHandler,
)
from urllib.error import HTTPError, URLError

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


# ============================================================
# AI INFINITY
# TARGET-2050.89
# ============================================================

APP_VERSION = "TARGET-2050.89"

BUILD = (
    "CLAIM-AWARE-EVIDENCE-CLOSURE-"
    "APPROVED-COMMAND-RECOVERY-CORE"
)

DB_PATH = os.getenv(
    "AI_INFINITY_DB",
    os.getenv(
        "AI_INFINITY_DATA",
        "/tmp/ai-infinity"
    ) + "/ai_infinity.db"
)

DB_DIR = os.path.dirname(DB_PATH)

if DB_DIR:
    os.makedirs(DB_DIR, exist_ok=True)

app = FastAPI(
    title="AI Infinity",
    version=APP_VERSION
)

db_lock = threading.Lock()


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        timeout=30
    )
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(
    conn,
    table,
    column,
    definition
):
    columns = {
        row["name"]
        for row in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }

    if column not in columns:
        conn.execute(
            f"""
            ALTER TABLE {table}
            ADD COLUMN {column} {definition}
            """
        )


def init_db():

    with db_lock:

        conn = db()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS missions(
                id TEXT PRIMARY KEY,
                objective TEXT,
                status TEXT,
                created REAL,
                updated REAL,
                result TEXT,
                plan TEXT,
                approval_required INTEGER,
                approved INTEGER,
                resumable INTEGER
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                ts REAL,
                kind TEXT,
                payload TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL,
                key TEXT,
                value TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS policies(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER,
                ts REAL,
                mode TEXT,
                valid INTEGER
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS provenance(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                provider TEXT,
                url TEXT,
                integrity TEXT
            )
        """)

        # 2050.89 migration:
        # preserve existing 2050.88 databases.
        ensure_column(
            conn,
            "missions",
            "request",
            "TEXT"
        )

        if conn.execute(
            """
            SELECT COUNT(*) n
            FROM policies
            """
        ).fetchone()["n"] == 0:

            conn.execute(
                """
                INSERT INTO policies(
                    version,
                    ts,
                    mode,
                    valid
                )
                VALUES(1,?,?,1)
                """,
                (
                    time.time(),
                    "baseline"
                )
            )

        conn.commit()
        conn.close()


init_db()


# ============================================================
# REQUEST CONTRACT
# ============================================================

class MissionRequest(BaseModel):

    objective: str

    research: bool = True

    verify: bool = True

    remember: bool = False

    external_access: bool = True

    execute: bool = False

    require_approval: bool = True


# ============================================================
# SECURITY
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "instance-data",
}

WAF_MARKERS = (
    "request blocked",
    "access denied",
    "security verification",
    "bot detection",
    "captcha",
    "cf-chl-",
)

MAX_RESPONSE_BYTES = 1024 * 1024


def is_private_ip(value):

    try:

        ip = ipaddress.ip_address(value)

        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )

    except Exception:

        return False


def is_private_host(host):

    if not host:
        return True

    host = host.lower().strip(".")

    if host in BLOCKED_HOSTS:
        return True

    if is_private_ip(host):
        return True

    try:

        results = socket.getaddrinfo(
            host,
            None,
            proto=socket.IPPROTO_TCP
        )

        for item in results:

            address = item[4][0]

            if is_private_ip(address):
                return True

    except Exception:

        return False

    return False


class SafeRedirectHandler(
    HTTPRedirectHandler
):

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl
    ):

        target = urljoin(
            req.full_url,
            newurl
        )

        parsed = urlparse(target)

        if (
            parsed.scheme
            not in ("http", "https")
            or not parsed.hostname
            or is_private_host(
                parsed.hostname
            )
        ):
            raise URLError(
                "redirect_to_blocked_destination"
            )

        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            target
        )


OPENER = build_opener(
    SafeRedirectHandler()
)


def safe_fetch(
    url,
    timeout=6
):

    parsed = urlparse(url)

    if (
        parsed.scheme
        not in ("http", "https")
        or not parsed.hostname
        or is_private_host(
            parsed.hostname
        )
    ):
        raise RuntimeError(
            "blocked_private_or_invalid_url"
        )

    request = Request(
        url,
        headers={
            "User-Agent":
                "AI-Infinity/2050.89",
            "Accept":
                (
                    "application/json,"
                    "application/xml,"
                    "text/xml,*/*"
                ),
        },
        method="GET"
    )

    try:

        response = OPENER.open(
            request,
            timeout=timeout
        )

        final_url = response.geturl()

        final_parsed = urlparse(
            final_url
        )

        if (
            final_parsed.scheme
            not in ("http", "https")
            or not final_parsed.hostname
            or is_private_host(
                final_parsed.hostname
            )
        ):
            raise RuntimeError(
                "redirect_destination_blocked"
            )

        data = response.read(
            MAX_RESPONSE_BYTES + 1
        )

        if len(data) > MAX_RESPONSE_BYTES:

            raise RuntimeError(
                "response_too_large"
            )

        text = data.decode(
            "utf-8",
            errors="replace"
        )

        sample = text[:30000].lower()

        if any(
            marker in sample
            for marker in WAF_MARKERS
        ):
            raise RuntimeError(
                "waf_or_block_response_rejected"
            )

        return {
            "text": text,
            "status":
                getattr(
                    response,
                    "status",
                    200
                ),
            "content_type":
                response.headers.get(
                    "Content-Type",
                    ""
                ),
            "final_url":
                final_url
        }

    except HTTPError as exc:

        raise RuntimeError(
            f"http_error_{exc.code}"
        )

    except URLError as exc:

        raise RuntimeError(
            "network_error"
        ) from exc


def parse_json(response):

    try:

        return json.loads(
            response["text"]
        )

    except Exception:

        raise RuntimeError(
            "invalid_json_response"
        )


# ============================================================
# PERSISTENCE
# ============================================================

def current_policy():

    with db_lock:

        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM policies
            ORDER BY version DESC
            LIMIT 1
            """
        ).fetchone()

        conn.close()

    return {
        "version":
            row["version"],
        "mode":
            row["mode"],
        "valid":
            bool(row["valid"])
    }


def adaptive_upgrade(reason):

    policy = current_policy()

    version = (
        policy["version"] + 1
    )

    with db_lock:

        conn = db()

        conn.execute(
            """
            INSERT INTO policies(
                version,
                ts,
                mode,
                valid
            )
            VALUES(?,?,?,1)
            """,
            (
                version,
                time.time(),
                reason
            )
        )

        conn.commit()
        conn.close()

    return version


def event(
    mission_id,
    kind,
    payload=None
):

    with db_lock:

        conn = db()

        conn.execute(
            """
            INSERT INTO events(
                mission_id,
                ts,
                kind,
                payload
            )
            VALUES(?,?,?,?)
            """,
            (
                mission_id,
                time.time(),
                kind,
                json.dumps(
                    payload or {},
                    default=str
                )
            )
        )

        conn.commit()
        conn.close()


def create_mission(
    mission_id,
    request,
    mission_plan
):

    with db_lock:

        conn = db()

        conn.execute(
            """
            INSERT INTO missions(
                id,
                objective,
                status,
                created,
                updated,
                result,
                plan,
                approval_required,
                approved,
                resumable,
                request
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                mission_id,
                request.objective,
                "accepted",
                time.time(),
                time.time(),
                None,
                json.dumps(
                    mission_plan
                ),
                int(
                    request.require_approval
                    and request.execute
                ),
                0,
                1,
                request.model_dump_json()
            )
        )

        conn.commit()
        conn.close()


def update_mission(
    mission_id,
    status,
    result=None,
    approved=None
):

    with db_lock:

        conn = db()

        if approved is None:

            conn.execute(
                """
                UPDATE missions
                SET
                    status=?,
                    updated=?,
                    result=?
                WHERE id=?
                """,
                (
                    status,
                    time.time(),
                    json.dumps(
                        result,
                        default=str
                    ),
                    mission_id
                )
            )

        else:

            conn.execute(
                """
                UPDATE missions
                SET
                    status=?,
                    updated=?,
                    result=?,
                    approved=?
                WHERE id=?
                """,
                (
                    status,
                    time.time(),
                    json.dumps(
                        result,
                        default=str
                    ),
                    int(approved),
                    mission_id
                )
            )

        conn.commit()
        conn.close()


def get_mission(mission_id):

    with db_lock:

        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM missions
            WHERE id=?
            """,
            (mission_id,)
        ).fetchone()

        conn.close()

    if not row:
        return None

    try:
        result = (
            json.loads(
                row["result"]
            )
            if row["result"]
            else None
        )
    except Exception:
        result = None

    try:
        plan = json.loads(
            row["plan"] or "[]"
        )
    except Exception:
        plan = []

    request = None

    if "request" in row.keys():

        try:
            request = json.loads(
                row["request"]
            )
        except Exception:
            request = None

    return {
        "id":
            row["id"],
        "objective":
            row["objective"],
        "status":
            row["status"],
        "created_at":
            row["created"],
        "updated_at":
            row["updated"],
        "result":
            result,
        "plan":
            plan,
        "approval_required":
            bool(
                row["approval_required"]
            ),
        "approved":
            bool(
                row["approved"]
            ),
        "resumable":
            bool(
                row["resumable"]
            ),
        "request":
            request
    }


def get_events(mission_id):

    with db_lock:

        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM events
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,)
        ).fetchall()

        conn.close()

    return [
        {
            "timestamp":
                row["ts"],
            "type":
                row["kind"],
            "payload":
                json.loads(
                    row["payload"]
                    or "{}"
                )
        }
        for row in rows
    ]


def remember(
    objective,
    result
):

    key = hashlib.sha256(
        objective.encode()
    ).hexdigest()[:24]

    with db_lock:

        conn = db()

        conn.execute(
            """
            INSERT INTO memory(
                ts,
                key,
                value
            )
            VALUES(?,?,?)
            """,
            (
                time.time(),
                key,
                json.dumps(
                    result,
                    default=str
                )
            )
        )

        conn.commit()
        conn.close()

    return key


def provenance_add(
    mission_id,
    source
):

    with db_lock:

        conn = db()

        conn.execute(
            """
            INSERT INTO provenance(
                mission_id,
                provider,
                url,
                integrity
            )
            VALUES(?,?,?,?)
            """,
            (
                mission_id,
                source.get(
                    "provider"
                ),
                source.get(
                    "url"
                ),
                "transport_validated"
            )
        )

        conn.commit()
        conn.close()


# ============================================================
# PLANNING
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
    "give",
    "research",
    "test",
    "using",
    "use",
    "how",
    "what",
    "why",
    "are",
    "is",
    "of",
    "to",
    "a",
    "an",
    "on",
    "by",
    "or",
    "be",
    "can",
    "their",
    "they",
    "its",
    "our",
    "your",
    "all",
    "independent",
    "evidence",
    "whether",
    "does",
    "have",
    "has",
    "been",
}


def objective_terms(objective):

    words = re.findall(
        r"[A-Za-z0-9][A-Za-z0-9_-]{2,}",
        objective.lower()
    )

    return list(
        dict.fromkeys(
            word
            for word in words
            if word not in STOPWORDS
        )
    )[:16]


def classify_intent(
    objective,
    request
):

    return {
        "research":
            request.research,
        "verification":
            request.verify,
        "action":
            request.execute,
        "memory":
            request.remember
    }


def build_plan(
    objective,
    request
):

    steps = []

    if request.research:

        steps.append({
            "id":
                "research",
            "tool":
                "research",
            "depends_on":
                []
        })

    steps.append({
        "id":
            "reasoning",
        "tool":
            "reasoning",
        "depends_on":
            ["research"]
            if request.research
            else []
    })

    if request.verify:

        steps.append({
            "id":
                "verification",
            "tool":
                "verification",
            "depends_on":
                (
                    ["research", "reasoning"]
                    if request.research
                    else ["reasoning"]
                )
        })

    if request.execute:

        steps.append({
            "id":
                "action",
            "tool":
                "action_gateway",
            "depends_on":
                [
                    item["id"]
                    for item in steps
                ]
        })

    return steps


# ============================================================
# SOURCE MODEL
# ============================================================

def canonical_url(url):

    if not url:
        return ""

    try:

        parsed = urlparse(
            url.strip()
        )

        scheme = parsed.scheme.lower()

        host = (
            parsed.hostname
            or ""
        ).lower()

        port = parsed.port

        if (
            port
            and not (
                (scheme == "http" and port == 80)
                or
                (scheme == "https" and port == 443)
            )
        ):
            host = (
                host
                + ":"
                + str(port)
            )

        path = parsed.path.rstrip("/")

        return (
            scheme
            + "://"
            + host
            + path
        )

    except Exception:

        return url.strip()


def normalize_title(title):

    return re.sub(
        r"[^a-z0-9]+",
        "",
        str(title or "").lower()
    )


def make_source(
    title,
    url,
    provider,
    year,
    publisher,
    abstract=""
):

    canonical = canonical_url(url)

    source_id = hashlib.sha256(
        (
            str(title)
            + canonical
        ).encode()
    ).hexdigest()[:20]

    clean_abstract = re.sub(
        r"\s+",
        " ",
        re.sub(
            "<[^>]+>",
            " ",
            str(
                abstract or ""
            )
        )
    ).strip()

    return {
        "id":
            source_id,
        "title":
            str(title).strip(),
        "url":
            url,
        "canonical_url":
            canonical,
        "provider":
            provider,
        "publisher":
            publisher or provider,
        "publisher_name":
            publisher or provider,
        "year":
            year,
        "abstract":
            clean_abstract[:12000],
        "type":
            (
                "academic_work"
                if provider != "wikipedia"
                else "reference"
            )
    }


def deduplicate_sources(
    sources
):

    seen = set()
    result = []

    for source in sources:

        doi_match = re.search(
            r"(10\.\d{4,9}/[-._;()/:a-z0-9]+)",
            (
                source.get(
                    "url",
                    ""
                )
                + " "
                + source.get(
                    "abstract",
                    ""
                )
            ),
            re.I
        )

        doi = (
            doi_match.group(1).lower()
            if doi_match
            else ""
        )

        title_key = normalize_title(
            source.get(
                "title",
                ""
            )
        )

        url_key = canonical_url(
            source.get(
                "url",
                ""
            )
        )

        key = (
            "doi:" + doi
            if doi
            else (
                "title:" + title_key
                if title_key
                else "url:" + url_key
            )
        )

        if (
            key
            and key not in seen
            and source.get("title")
        ):

            seen.add(key)
            result.append(source)

    return result


def enrich_sources(
    sources,
    objective
):

    terms = objective_terms(
        objective
    )

    for source in sources:

        text = (
            source.get(
                "title",
                ""
            )
            + " "
            + source.get(
                "abstract",
                ""
            )
        ).lower()

        matched = [
            term
            for term in terms
            if term in text
        ]

        relevance = (
            len(matched)
            / max(
                1,
                len(terms)
            )
        )

        empirical = any(
            word in text
            for word in (
                "benchmark",
                "evaluation",
                "empirical",
                "experiment",
                "study",
                "results",
                "task completion",
                "success rate",
                "performance",
                "reliability",
                "dataset",
                "trial",
            )
        )

        evidence_type = (
            "empirical_or_evaluation"
            if empirical
            else "descriptive_or_reference"
        )

        publisher = (
            source.get(
                "publisher_name"
            )
            or ""
        ).lower()

        quality = 0.45

        if source.get("year"):
            try:
                age = (
                    2026
                    - int(
                        source["year"]
                    )
                )

                if age <= 5:
                    quality += 0.12

                elif age <= 10:
                    quality += 0.06

            except Exception:
                pass

        if empirical:
            quality += 0.15

        if (
            "doi.org"
            in source.get(
                "url",
                ""
            ).lower()
        ):
            quality += 0.08

        if publisher:
            quality += 0.05

        source[
            "matched_terms"
        ] = matched

        source[
            "relevance_score"
        ] = round(
            min(
                relevance,
                1.0
            ),
            4
        )

        source[
            "evidence_type"
        ] = evidence_type

        source[
            "empirical_score"
        ] = (
            0.75
            if empirical
            else 0.20
        )

        source[
            "quality_score_v2"
        ] = round(
            min(
                quality,
                1.0
            ),
            4
        )

    return sources


# ============================================================
# PROVIDERS
# ============================================================

PROVIDER_FAMILIES = {
    "openalex":
        "openalex",

    "crossref":
        "crossref",

    "semantic_scholar":
        "semantic_scholar",

    "arxiv":
        "arxiv",

    "wikipedia":
        "wikipedia",

    "crossref_alt":
        "crossref",
}


def provider_family(
    provider
):

    return PROVIDER_FAMILIES.get(
        provider,
        provider
    )


def search_openalex(
    query
):

    url = (
        "https://api.openalex.org/works?"
        + "search="
        + quote(query)
        + "&per-page=8"
    )

    data = parse_json(
        safe_fetch(url)
    )

    results = []

    for item in data.get(
        "results",
        []
    ):

        results.append(
            make_source(
                item.get(
                    "display_name"
                ),
                item.get(
                    "doi"
                )
                or item.get(
                    "id"
                ),
                "openalex",
                item.get(
                    "publication_year"
                ),
                (
                    item.get(
                        "primary_location",
                        {}
                    )
                    .get(
                        "source",
                        {}
                    )
                    .get(
                        "display_name"
                    )
                ),
                (
                    item.get(
                        "abstract_inverted_index"
                    )
                )
            )
        )

    return results


def search_crossref(
    query
):

    url = (
        "https://api.crossref.org/works?"
        + "query.bibliographic="
        + quote(query)
        + "&rows=8"
    )

    data = parse_json(
        safe_fetch(url)
    )

    results = []

    for item in data.get(
        "message",
        {}
    ).get(
        "items",
        []
    ):

        title = (
            item.get(
                "title",
                [""]
            )
            or [""]
        )[0]

        publisher = (
            item.get(
                "publisher"
            )
            or "Crossref"
        )

        results.append(
            make_source(
                title,
                item.get(
                    "URL"
                ),
                "crossref",
                (
                    item.get(
                        "published-print",
                        {}
                    )
                    .get(
                        "date-parts",
                        [[None]]
                    )[0][0]
                    if item.get(
                        "published-print"
                    )
                    else None
                ),
                publisher,
                item.get(
                    "abstract",
                    ""
                )
            )
        )

    return results


def search_semantic_scholar(
    query
):

    url = (
        "https://api.semanticscholar.org/"
        "graph/v1/paper/search?"
        + "query="
        + quote(query)
        + "&limit=8"
        + "&fields=title,abstract,year,"
        "url,venue,externalIds"
    )

    data = parse_json(
        safe_fetch(url)
    )

    results = []

    for item in data.get(
        "data",
        []
    ):

        external = (
            item.get(
                "externalIds"
            )
            or {}
        )

        paper_url = (
            (
                "https://doi.org/"
                + external["DOI"]
            )
            if external.get("DOI")
            else item.get("url")
        )

        results.append(
            make_source(
                item.get(
                    "title"
                ),
                paper_url,
                "semantic_scholar",
                item.get(
                    "year"
                ),
                item.get(
                    "venue"
                ),
                item.get(
                    "abstract"
                )
            )
        )

    return results


def search_arxiv(
    query
):

    url = (
        "https://export.arxiv.org/api/query?"
        + "search_query=all:"
        + quote(query)
        + "&start=0&max_results=8"
    )

    response = safe_fetch(
        url
    )

    root = ET.fromstring(
        response["text"]
    )

    ns = {
        "a":
            "http://www.w3.org/2005/Atom"
    }

    results = []

    for entry in root.findall(
        "a:entry",
        ns
    ):

        title = (
            entry.findtext(
                "a:title",
                default="",
                namespaces=ns
            )
        )

        summary = (
            entry.findtext(
                "a:summary",
                default="",
                namespaces=ns
            )
        )

        published = (
            entry.findtext(
                "a:published",
                default="",
                namespaces=ns
            )
        )

        entry_id = (
            entry.findtext(
                "a:id",
                default="",
                namespaces=ns
            )
        )

        year = None

        if published:
            try:
                year = int(
                    published[:4]
                )
            except Exception:
                pass

        results.append(
            make_source(
                title,
                entry_id,
                "arxiv",
                year,
                "arXiv",
                summary
            )
        )

    return results


def search_wikipedia(
    query
):

    url = (
        "https://en.wikipedia.org/w/api.php?"
        "action=query&format=json"
        "&list=search"
        "&srsearch="
        + quote(query)
        + "&srlimit=6"
    )

    data = parse_json(
        safe_fetch(url)
    )

    results = []

    for item in data.get(
        "query",
        {}
    ).get(
        "search",
        []
    ):

        title = item.get(
            "title"
        )

        page_url = (
            "https://en.wikipedia.org/wiki/"
            + quote(
                title.replace(
                    " ",
                    "_"
                )
            )
        )

        results.append(
            make_source(
                title,
                page_url,
                "wikipedia",
                None,
                "Wikipedia",
                item.get(
                    "snippet",
                    ""
                )
            )
        )

    return results


PROVIDERS = [
    (
        "openalex",
        search_openalex
    ),
    (
        "crossref",
        search_crossref
    ),
    (
        "semantic_scholar",
        search_semantic_scholar
    ),
    (
        "arxiv",
        search_arxiv
    ),
    (
        "wikipedia",
        search_wikipedia
    ),
]


# ============================================================
# RESEARCH
# ============================================================

def research(
    objective,
    mission_id,
    recovery=False,
    skip_providers=None
):

    skip_providers = set(
        skip_providers or []
    )

    query = objective

    if recovery:

        query += (
            " empirical evaluation "
            "benchmark experiment "
            "reliability task completion "
            "success failure results"
        )

    results = []

    events = []

    lock = threading.Lock()

    def worker(
        name,
        function
    ):

        if name in skip_providers:

            events.append({
                "provider":
                    name,
                "family":
                    provider_family(
                        name
                    ),
                "status":
                    "skipped",
                "failure_kind":
                    "recovery_skip"
            })

            return

        started = time.time()

        try:

            items = function(
                query
            )

            with lock:

                results.extend(
                    items
                )

                events.append({
                    "provider":
                        name,
                    "family":
                        provider_family(
                            name
                        ),
                    "status":
                        "ok",
                    "count":
                        len(items),
                    "latency_ms":
                        round(
                            (
                                time.time()
                                - started
                            ) * 1000
                        )
                })

        except Exception as exc:

            message = str(
                exc
            )[:300]

            kind = "provider_error"

            if "429" in message:
                kind = "rate_limited"

            elif "timeout" in message.lower():
                kind = "timeout"

            with lock:

                events.append({
                    "provider":
                        name,
                    "family":
                        provider_family(
                            name
                        ),
                    "status":
                        "failed",
                    "failure_kind":
                        kind,
                    "error":
                        message
                })

    threads = []

    for name, function in PROVIDERS:

        thread = threading.Thread(
            target=worker,
            args=(
                name,
                function
            ),
            daemon=True
        )

        threads.append(
            thread
        )

        thread.start()

    for thread in threads:

        thread.join(
            timeout=9
        )

    results = deduplicate_sources(
        results
    )

    results = enrich_sources(
        results,
        objective
    )

    families = sorted(
        {
            provider_family(
                item.get(
                    "provider"
                )
            )
            for item in results
        }
    )

    publishers = sorted(
        {
            item.get(
                "publisher_name"
            )
            for item in results
            if item.get(
                "publisher_name"
            )
        }
    )

    empirical = sum(
        1
        for item in results
        if item.get(
            "evidence_type"
        )
        == "empirical_or_evaluation"
    )

    return {
        "sources":
            results,
        "events":
            events,
        "families":
            families,
        "publisher_count":
            len(publishers),
        "publishers":
            publishers,
        "empirical":
            empirical,
        "recovery":
            recovery
    }


# ============================================================
# CLAIM ANALYSIS
# ============================================================

CLAIM_POSITIVE = {
    "improves",
    "improved",
    "improvement",
    "effective",
    "effective",
    "successful",
    "success",
    "reliable",
    "reliability",
    "increase",
    "increased",
    "higher",
    "outperforms",
    "outperformed",
    "better",
    "benefit",
    "beneficial",
    "robust",
}

CLAIM_NEGATIVE = {
    "fails",
    "failed",
    "failure",
    "unreliable",
    "ineffective",
    "decrease",
    "decreased",
    "lower",
    "underperforms",
    "underperformed",
    "worse",
    "harm",
    "harmful",
    "fragile",
}

CLAIM_UNCERTAIN = {
    "may",
    "might",
    "could",
    "possibly",
    "potentially",
    "suggests",
    "suggest",
    "unclear",
    "limited",
}

CLAIM_STOPWORDS = (
    STOPWORDS
    | CLAIM_POSITIVE
    | CLAIM_NEGATIVE
    | CLAIM_UNCERTAIN
)


def sentence_split(text):

    text = re.sub(
        r"\s+",
        " ",
        text or ""
    ).strip()

    if not text:
        return []

    parts = re.split(
        r"(?<=[.!?])\s+",
        text
    )

    return [
        part.strip()
        for part in parts
        if len(part.strip()) >= 30
    ]


def claim_tokens(text):

    words = re.findall(
        r"[a-z0-9][a-z0-9_-]{2,}",
        text.lower()
    )

    return {
        word
        for word in words
        if word not in CLAIM_STOPWORDS
    }


def polarity(sentence):

    words = re.findall(
        r"[a-z]+",
        sentence.lower()
    )

    positive = 0
    negative = 0

    for index, word in enumerate(
        words
    ):

        window = words[
            max(0, index - 3):
            index
        ]

        negated = any(
            item in {
                "not",
                "no",
                "never",
                "without"
            }
            for item in window
        )

        if word in CLAIM_POSITIVE:

            if negated:
                negative += 1
            else:
                positive += 1

        if word in CLAIM_NEGATIVE:

            if negated:
                positive += 1
            else:
                negative += 1

    uncertain = any(
        word in CLAIM_UNCERTAIN
        for word in words
    )

    if positive > negative:
        stance = "positive"

    elif negative > positive:
        stance = "negative"

    else:
        stance = "neutral"

    if uncertain and stance != "neutral":
        confidence = 0.48
    elif stance == "neutral":
        confidence = 0.20
    else:
        confidence = min(
            0.95,
            0.55
            + (
                abs(
                    positive
                    - negative
                )
                * 0.12
            )
        )

    return {
        "stance":
            stance,
        "confidence":
            round(
                confidence,
                3
            ),
        "uncertain":
            uncertain
    }


def extract_claims(
    source
):

    text = (
        source.get(
            "title",
            ""
        )
        + ". "
        + source.get(
            "abstract",
            ""
        )
    )

    claims = []

    for sentence in sentence_split(
        text
    ):

        stance = polarity(
            sentence
        )

        tokens = claim_tokens(
            sentence
        )

        if (
            stance["stance"]
            != "neutral"
            and len(tokens) >= 2
        ):

            claims.append({
                "source_id":
                    source.get(
                        "id"
                    ),
                "provider":
                    source.get(
                        "provider"
                    ),
                "title":
                    source.get(
                        "title"
                    ),
                "text":
                    sentence[:500],
                "tokens":
                    sorted(
                        list(tokens)
                    )[:20],
                "stance":
                    stance["stance"],
                "confidence":
                    stance["confidence"],
                "uncertain":
                    stance["uncertain"]
            })

    return claims[:8]


def analyze_claims(
    sources
):

    claims = []

    for source in sources:

        source_claims = (
            extract_claims(
                source
            )
        )

        source[
            "claim_candidates"
        ] = source_claims

        claims.extend(
            source_claims
        )

    contradictions = []

    for index, left in enumerate(
        claims
    ):

        for right in claims[
            index + 1:
        ]:

            if (
                left["source_id"]
                == right["source_id"]
            ):
                continue

            if (
                left["stance"]
                == right["stance"]
            ):
                continue

            if (
                left["uncertain"]
                or right["uncertain"]
            ):
                continue

            if (
                left["confidence"] < 0.55
                or
                right["confidence"] < 0.55
            ):
                continue

            shared = (
                set(left["tokens"])
                &
                set(right["tokens"])
            )

            union = (
                set(left["tokens"])
                |
                set(right["tokens"])
            )

            similarity = (
                len(shared)
                /
                max(
                    1,
                    len(union)
                )
            )

            if (
                len(shared) >= 3
                or similarity >= 0.32
            ):

                contradictions.append({
                    "source_a":
                        left["source_id"],
                    "source_b":
                        right["source_id"],
                    "provider_a":
                        left["provider"],
                    "provider_b":
                        right["provider"],
                    "claim_a":
                        left["text"],
                    "claim_b":
                        right["text"],
                    "stance_a":
                        left["stance"],
                    "stance_b":
                        right["stance"],
                    "shared_terms":
                        sorted(
                            list(shared)
                        )[:12],
                    "similarity":
                        round(
                            similarity,
                            3
                        ),
                    "reason":
                        (
                            "opposite claim polarity "
                            "with shared topic terms"
                        )
                })

    return {
        "claims":
            claims,
        "claim_count":
            len(claims),
        "contradictions":
            contradictions[:20],
        "contradiction_count":
            len(contradictions),
        "semantic_contradiction_proof":
            False,
        "method":
            (
                "heuristic claim-level "
                "polarity and topic-overlap "
                "screening"
            )
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_evidence(
    sources,
    objective,
    requested=True
):

    relevant = [
        source
        for source in sources
        if source.get(
            "relevance_score",
            0
        ) >= 0.18
    ]

    high_quality = [
        source
        for source in relevant
        if source.get(
            "quality_score_v2",
            0
        ) >= 0.35
    ]

    empirical = [
        source
        for source in relevant
        if source.get(
            "evidence_type"
        )
        == "empirical_or_evaluation"
    ]

    publishers = {
        source.get(
            "publisher_name"
        )
        for source in relevant
        if source.get(
            "publisher_name"
        )
    }

    families = {
        provider_family(
            source.get(
                "provider"
            )
        )
        for source in relevant
    }

    claim_analysis = analyze_claims(
        relevant
    )

    contradictions = (
        claim_analysis[
            "contradiction_count"
        ]
    )

    checks = {

        "relevant_sources":
            len(relevant) >= 3,

        "high_quality_sources":
            len(high_quality) >= 2,

        "empirical_sources":
            len(empirical) >= 3,

        "independent_publishers":
            len(publishers) >= 2,

        "independent_provider_families":
            len(families) >= 2,

        "claim_candidates":
            claim_analysis[
                "claim_count"
            ] >= 2,

        "contradiction_free":
            contradictions == 0,
    }

    verified = (
        requested
        and all(
            checks.values()
        )
    )

    gaps = []

    if not checks[
        "relevant_sources"
    ]:
        gaps.append(
            "need_at_least_3_relevant_sources"
        )

    if not checks[
        "high_quality_sources"
    ]:
        gaps.append(
            "need_at_least_2_high_quality_sources"
        )

    if not checks[
        "empirical_sources"
    ]:
        gaps.append(
            "need_at_least_3_empirical_sources"
        )

    if not checks[
        "independent_publishers"
    ]:
        gaps.append(
            "need_2_independent_publishers"
        )

    if not checks[
        "independent_provider_families"
    ]:
        gaps.append(
            "need_2_provider_families"
        )

    if not checks[
        "claim_candidates"
    ]:
        gaps.append(
            "need_at_least_2_claim_candidates"
        )

    if not checks[
        "contradiction_free"
    ]:
        gaps.append(
            "possible_claim_contradictions_detected"
        )

    return {
        "verified":
            verified,

        "verification_status":
            (
                "verified"
                if verified
                else "insufficient_evidence"
            ),

        "relevant_sources":
            len(relevant),

        "high_quality_sources":
            len(high_quality),

        "empirical_sources":
            len(empirical),

        "independent_publishers":
            len(publishers),

        "independent_provider_families":
            len(families),

        "claim_count":
            claim_analysis[
                "claim_count"
            ],

        "contradiction_count":
            contradictions,

        "claim_analysis":
            claim_analysis,

        "checks":
            checks,

        "evidence_gaps":
            gaps,

        "verification_requirements": {
            "relevant_sources":
                3,
            "high_quality_sources":
                2,
            "empirical_sources":
                3,
            "independent_publishers":
                2,
            "independent_provider_families":
                2,
            "claim_candidates":
                2,
            "contradictions":
                0
        },

        "limitations": [
            (
                "Publisher independence is "
                "metadata-based."
            ),
            (
                "Provider-family independence "
                "does not prove that underlying "
                "papers are independent."
            ),
            (
                "Contradiction detection is "
                "heuristic and is not semantic proof."
            ),
            (
                "Verification means the defined "
                "evidence quorum was met; it does "
                "not prove real-world success."
            )
        ]
    }


# ============================================================
# ACTION BOUNDARY
# ============================================================

def action_gateway(
    objective,
    mission_id
):

    event(
        mission_id,
        "action_gateway_checked",
        {
            "external_side_effect":
                False,
            "reason":
                (
                    "No external action connector "
                    "is installed in this core."
                )
        }
    )

    return {
        "requested":
            True,
        "executed":
            False,
        "status":
            "not_connected",
        "reason":
            (
                "AI Infinity currently has no "
                "authorized external action gateway."
            ),
        "external_side_effect":
            False,
        "arbitrary_code_execution":
            False,
        "permission_bypass":
            False
    }


# ============================================================
# MISSION ENGINE
# ============================================================

def execute_mission(
    mission_id,
    request
):

    mission_plan = build_plan(
        request.objective,
        request
    )

    recovery_round = 0

    failed_providers = []

    for attempt in range(
        1,
        3
    ):

        update_mission(
            mission_id,
            "running",
            None
        )

        event(
            mission_id,
            "attempt_started",
            {
                "attempt":
                    attempt,
                "recovery_round":
                    recovery_round
            }
        )

        try:

            research_result = {
                "sources": [],
                "events": [],
                "families": [],
                "publisher_count": 0,
                "publishers": [],
                "empirical": 0
            }

            if request.research:

                research_result = research(
                    request.objective,
                    mission_id,
                    recovery=(
                        recovery_round > 0
                    ),
                    skip_providers=(
                        failed_providers
                        if recovery_round > 0
                        else []
                    )
                )

            sources = (
                research_result[
                    "sources"
                ]
            )

            for source in sources:

                provenance_add(
                    mission_id,
                    source
                )

            verification = (
                verify_evidence(
                    sources,
                    request.objective,
                    request.verify
                )
            )

            closure = (
                verification[
                    "verified"
                ]
                if request.verify
                else True
            )

            evidence_gaps = (
                verification[
                    "evidence_gaps"
                ]
            )

            failed_now = [
                item[
                    "provider"
                ]
                for item
                in research_result[
                    "events"
                ]
                if item.get(
                    "status"
                ) == "failed"
            ]

            result = {

                "mission_id":
                    mission_id,

                "objective":
                    request.objective,

                "plan":
                    mission_plan,

                "discovered_sources":
                    len(sources),

                "usable_sources":
                    len(sources),

                "relevant_sources":
                    verification[
                        "relevant_sources"
                    ],

                "independent_publishers":
                    verification[
                        "independent_publishers"
                    ],

                "independent_provider_families":
                    verification[
                        "independent_provider_families"
                    ],

                "provider_families":
                    research_result[
                        "families"
                    ],

                "claims":
                    verification[
                        "claim_count"
                    ],

                "verification":
                    verification,

                "closure":
                    closure,

                "evidence_gaps":
                    evidence_gaps,

                "provider_recovery": {
                    "round":
                        recovery_round,
                    "failed_providers":
                        failed_providers,
                    "current_failures":
                        failed_now,
                    "provider_failures_tolerated":
                        True,
                    "independent_provider_recovery":
                        True,
                    "required_provider_families":
                        2
                },

                "providers":
                    research_result[
                        "events"
                    ],

                "sources":
                    sources[:30],

                "attempts":
                    attempt,

                "recovery_attempts":
                    recovery_round,

                "policy_version":
                    current_policy()[
                        "version"
                    ],

                "security": {

                    "private_networks_blocked":
                        True,

                    "redirect_destinations_validated":
                        True,

                    "waf_responses_rejected":
                        True,

                    "transport_validation_required":
                        True,

                    "research_source_integrity_validation":
                        True,

                    "publisher_identity_validation":
                        "metadata_based",

                    "contradiction_analysis_required":
                        True,

                    "arbitrary_code_execution":
                        False,

                    "permission_bypass":
                        False
                }
            }

            if request.execute:

                result[
                    "action"
                ] = action_gateway(
                    request.objective,
                    mission_id
                )

            event(
                mission_id,
                "research_completed",
                {
                    "sources":
                        len(sources),
                    "provider_families":
                        research_result[
                            "families"
                        ],
                    "independent_publishers":
                        research_result[
                            "publisher_count"
                        ],
                    "empirical_sources":
                        research_result[
                            "empirical"
                        ],
                    "verified":
                        verification[
                            "verified"
                        ],
                    "closure":
                        closure,
                    "evidence_gaps":
                        evidence_gaps
                }
            )

            # If a real-world action was requested,
            # never claim it was performed.
            if request.execute:

                result[
                    "execution_boundary"
                ] = (
                    "Research and verification "
                    "completed, but no external "
                    "side effect was performed because "
                    "no authorized action gateway is "
                    "connected."
                )

                if request.remember:

                    result[
                        "memory_key"
                    ] = remember(
                        request.objective,
                        result
                    )

                update_mission(
                    mission_id,
                    "needs_action",
                    result
                )

                event(
                    mission_id,
                    "mission_finished",
                    {
                        "status":
                            "needs_action",
                        "external_action":
                            False
                    }
                )

                return

            if (
                not request.verify
                or closure
            ):

                if request.remember:

                    result[
                        "memory_key"
                    ] = remember(
                        request.objective,
                        result
                    )

                update_mission(
                    mission_id,
                    "completed",
                    result
                )

                event(
                    mission_id,
                    "mission_finished",
                    {
                        "status":
                            "completed",
                        "closure":
                            closure
                    }
                )

                return

            # Recovery.
            if attempt == 1:

                recovery_round = 1

                failed_providers = (
                    failed_now
                )

                adaptive_upgrade(
                    "provider-independence-recovery"
                )

                event(
                    mission_id,
                    "recovery_started",
                    {
                        "reason":
                            "evidence_closure_not_met",
                        "evidence_gaps":
                            evidence_gaps,
                        "skipped_failed_providers":
                            failed_providers,
                        "required_provider_families":
                            2
                    }
                )

                time.sleep(
                    0.35
                )

                continue

            if request.remember:

                result[
                    "memory_key"
                ] = remember(
                    request.objective,
                    result
                )

            update_mission(
                mission_id,
                "needs_recovery",
                result
            )

            event(
                mission_id,
                "mission_finished",
                {
                    "status":
                        "needs_recovery",
                    "closure":
                        closure
                }
            )

            return

        except Exception as exc:

            event(
                mission_id,
                "application_error",
                {
                    "error":
                        str(exc)[:500],
                    "attempt":
                        attempt
                }
            )

            if attempt == 1:

                recovery_round = 1

                adaptive_upgrade(
                    "runtime-error-recovery"
                )

                event(
                    mission_id,
                    "runtime_recovery_started",
                    {
                        "reason":
                            str(exc)[:300]
                    }
                )

                continue

            update_mission(
                mission_id,
                "needs_recovery",
                {
                    "error":
                        str(exc)[:500],
                    "attempts":
                        attempt,
                    "recovery_attempts":
                        recovery_round
                }
            )

            return


# ============================================================
# API
# ============================================================

@app.get("/")
def root():

    return {
        "name":
            "AI Infinity",
        "status":
            "online",
        "version":
            APP_VERSION,
        "build":
            BUILD,
        "docs":
            "/docs",
        "run":
            "/run",
        "command":
            "/command",
        "interface":
            "/ui",
        "capabilities":
            "/capabilities"
    }


@app.get("/health")
def health():

    return {
        "status":
            "healthy",
        "service":
            "AI Infinity",
        "version":
            APP_VERSION,
        "build":
            BUILD,

        "policy": {

            "valid":
                True,

            "network_policy_enforced":
                True,

            "controlled_public_web_access":
                True,

            "arbitrary_code_execution":
                False,

            "unrestricted_private_network_access":
                False,

            "permission_bypass":
                False,

            "external_content_untrusted":
                True,

            "redirect_validation":
                True
        },

        "policy_version":
            current_policy()[
                "version"
            ],

        "router_enabled":
            True,

        "adaptive_recovery_enabled":
            True,

        "self_modification_enabled":
            True
    }


@app.get("/health-89")
def health_89():

    return {
        "status":
            "healthy",
        "service":
            "AI Infinity",
        "version":
            APP_VERSION,
        "build":
            BUILD,

        "core": {
            "mission_engine":
                True,
            "adaptive_recovery":
                True,
            "provider_independence":
                True,
            "empirical_evidence":
                True,
            "claim_analysis":
                True,
            "contradiction_screening":
                True,
            "command_approval":
                True,
            "persistent_mission_requests":
                True
        },

        "security": {
            "ssrf_protection":
                True,
            "redirect_destination_validation":
                True,
            "waf_rejection":
                True,
            "arbitrary_code_execution":
                False,
            "permission_bypass":
                False
        },

        "action_boundary": {
            "external_action_gateway":
                False,
            "real_world_side_effects":
                False,
            "status":
                "not_connected"
        }
    }


@app.get("/capabilities")
def capabilities():

    return {

        "service":
            "AI Infinity",

        "version":
            APP_VERSION,

        "research": {
            "openalex":
                True,
            "crossref":
                True,
            "semantic_scholar":
                True,
            "arxiv":
                True,
            "wikipedia":
                True,
            "parallel_provider_research":
                True,
            "provider_recovery":
                True
        },

        "evidence": {
            "provenance":
                True,
            "empirical_scoring":
                True,
            "claim_candidates":
                True,
            "contradiction_screening":
                True,
            "evidence_closure":
                True
        },

        "mission": {
            "background_execution":
                True,
            "persistent_state":
                True,
            "events":
                True,
            "adaptive_recovery":
                True,
            "policy_adaptation":
                True,
            "approval_workflow":
                True
        },

        "real_world_actions": {
            "action_gateway_connected":
                False,
            "external_side_effects":
                False,
            "arbitrary_code_execution":
                False,
            "status":
                "safe_boundary_only"
        }
    }


@app.get("/provider-quorum")
def provider_quorum():

    return {
        "status":
            "active",

        "version":
            APP_VERSION,

        "required_provider_families":
            2,

        "providers": [
            {
                "provider":
                    name,
                "family":
                    provider_family(
                        name
                    )
            }
            for name, _
            in PROVIDERS
        ],

        "closure_requires": {

            "relevant_sources":
                3,

            "independent_publishers":
                2,

            "independent_provider_families":
                2,

            "claims":
                2,

            "empirical_sources":
                3,

            "contradiction_screen":
                True
        }
    }


@app.get("/health-88")
def health_88():

    return {
        "status":
            "healthy",

        "service":
            "AI Infinity",

        "version":
            APP_VERSION,

        "build":
            BUILD,

        "provider_independence":
            True,

        "arxiv_enabled":
            True,

        "provider_quorum":
            2,

        "security": {
            "ssrf":
                True,
            "waf_rejection":
                True,
            "arbitrary_code_execution":
                False,
            "permission_bypass":
                False
        }
    }


@app.get("/version-88")
def version_88():

    return {
        "status":
            "active",
        "version":
            APP_VERSION,
        "build":
            BUILD,
        "previous":
            "TARGET-2050.88",
        "upgrade":
            (
                "claim-aware evidence closure, "
                "secure redirect handling, "
                "persistent command approval"
            )
    }


@app.get("/version-89")
def version_89():

    return {
        "status":
            "active",
        "version":
            APP_VERSION,
        "build":
            BUILD,
        "previous":
            "TARGET-2050.88",
        "upgrade": [
            "claim-aware evidence analysis",
            "stronger contradiction screening",
            "cross-provider work deduplication",
            "redirect SSRF hardening",
            "persistent mission request state",
            "functional command approval flow",
            "explicit external-action boundary"
        ]
    }


@app.post("/run")
def run(
    request: MissionRequest
):

    mission_id = (
        "mission-"
        + uuid.uuid4().hex[:12]
    )

    mission_plan = build_plan(
        request.objective,
        request
    )

    create_mission(
        mission_id,
        request,
        mission_plan
    )

    requires_approval = (
        request.execute
        and request.require_approval
    )

    if requires_approval:

        update_mission(
            mission_id,
            "awaiting_approval",
            None,
            False
        )

        event(
            mission_id,
            "approval_required",
            {
                "reason":
                    "external command requested"
            }
        )

    else:

        threading.Thread(
            target=execute_mission,
            args=(
                mission_id,
                request
            ),
            daemon=True
        ).start()

    return {
        "mission_id":
            mission_id,

        "status":
            (
                "awaiting_approval"
                if requires_approval
                else "accepted"
            ),

        "execution": {

            "background":
                not requires_approval,

            "shared_engine":
                True,

            "security_policy_enforced":
                True,

            "route":
                "/run",

            "approval_required":
                requires_approval
        },

        "version":
            APP_VERSION,

        "build":
            BUILD
    }


@app.post("/command")
def command(
    request: MissionRequest
):

    request = request.model_copy(
        update={
            "execute":
                True,
            "require_approval":
                True
        }
    )

    mission_id = (
        "mission-"
        + uuid.uuid4().hex[:12]
    )

    mission_plan = build_plan(
        request.objective,
        request
    )

    create_mission(
        mission_id,
        request,
        mission_plan
    )

    update_mission(
        mission_id,
        "awaiting_approval",
        None,
        False
    )

    event(
        mission_id,
        "command_received",
        {
            "approval_required":
                True
        }
    )

    return {
        "mission_id":
            mission_id,
        "status":
            "awaiting_approval",
        "approval_required":
            True,
        "arbitrary_code_execution":
            False,
        "permission_bypass":
            False,
        "next":
            (
                "/mission/"
                + mission_id
                + "/approve"
            )
    }


@app.post(
    "/mission/{mission_id}/approve"
)
def approve(
    mission_id: str
):

    mission = get_mission(
        mission_id
    )

    if not mission:

        return {
            "error":
                "mission_not_found"
        }

    if not mission[
        "approval_required"
    ]:

        return {
            "mission_id":
                mission_id,
            "status":
                mission["status"],
            "approval_required":
                False,
            "execution_started":
                False
        }

    if mission[
        "approved"
    ]:

        return {
            "mission_id":
                mission_id,
            "status":
                mission["status"],
            "already_approved":
                True,
            "execution_started":
                False
        }

    if mission[
        "status"
    ] != "awaiting_approval":

        return {
            "mission_id":
                mission_id,
            "status":
                mission["status"],
            "execution_started":
                False,
            "reason":
                "mission_not_awaiting_approval"
        }

    saved_request = (
        mission.get(
            "request"
        )
    )

    if saved_request:

        try:

            request = MissionRequest(
                **saved_request
            )

        except Exception:

            request = MissionRequest(
                objective=
                    mission["objective"],
                research=True,
                verify=True,
                execute=True,
                require_approval=True
            )

    else:

        request = MissionRequest(
            objective=
                mission["objective"],
            research=True,
            verify=True,
            execute=True,
            require_approval=True
        )

    update_mission(
        mission_id,
        "approved",
        mission["result"],
        True
    )

    event(
        mission_id,
        "approval_granted",
        {
            "execution_started":
                True
        }
    )

    threading.Thread(
        target=execute_mission,
        args=(
            mission_id,
            request
        ),
        daemon=True
    ).start()

    return {
        "mission_id":
            mission_id,
        "status":
            "approved",
        "execution_started":
            True,
        "background":
            True,
        "external_action_boundary":
            True
    }


@app.get(
    "/mission/{mission_id}"
)
def mission(
    mission_id: str
):

    result = get_mission(
        mission_id
    )

    return (
        result
        if result
        else {
            "error":
                "mission_not_found"
        }
    )


@app.get(
    "/mission/{mission_id}/events"
)
def mission_events(
    mission_id: str
):

    if not get_mission(
        mission_id
    ):

        return {
            "error":
                "mission_not_found"
        }

    return {
        "mission_id":
            mission_id,
        "events":
            get_events(
                mission_id
            )
    }


@app.get("/evidence-policy")
def evidence_policy():

    return {
        "status":
            "active",

        "empirical_evidence_required":
            True,

        "publisher_independence_required":
            True,

        "provider_independence_required":
            True,

        "verification_is_not_proof":
            True,

        "contradiction_screening":
            True,

        "semantic_contradiction_proof":
            False,

        "claim_level_analysis":
            True
    }


# ============================================================
# INTERFACE
# ============================================================

@app.get(
    "/ui",
    response_class=HTMLResponse
)
def ui():

    return """
<!doctype html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>AI Infinity 2050.89</title>

<style>

body{
    font-family:system-ui;
    max-width:950px;
    margin:auto;
    padding:20px;
    background:#0b0f14;
    color:#eee
}

textarea,
button{
    width:100%;
    box-sizing:border-box;
    padding:12px;
    margin:6px 0;
    border-radius:10px;
    border:1px solid #334;
    background:#151b23;
    color:#eee
}

button{
    cursor:pointer
}

pre{
    white-space:pre-wrap;
    background:#111720;
    padding:14px;
    border-radius:10px;
    overflow:auto
}

.status{
    padding:10px;
    border-radius:10px;
    background:#151b23;
    margin:10px 0
}

</style>
</head>

<body>

<h1>AI Infinity</h1>

<div class="status" id="health">
Checking...
</div>

<textarea
id="objective"
rows="6"
placeholder="Enter a mission..."
></textarea>

<button onclick="runMission()">
Research / Verify Mission
</button>

<button onclick="runCommand()">
Command — Approval Required
</button>

<pre id="result">
Ready.
</pre>

<script>

async function checkHealth(){

    try{

        const response =
            await fetch("/health-89");

        const data =
            await response.json();

        document.getElementById(
            "health"
        ).textContent =
            data.status
            + " — "
            + data.version;

    }catch(error){

        document.getElementById(
            "health"
        ).textContent =
            "Offline";

    }
}


async function createMission(
    endpoint,
    payload
){

    const output =
        document.getElementById(
            "result"
        );

    output.textContent =
        "Starting...";

    try{

        const response =
            await fetch(
                endpoint,
                {
                    method:"POST",
                    headers:{
                        "Content-Type":
                            "application/json"
                    },
                    body:
                        JSON.stringify(
                            payload
                        )
                }
            );

        const data =
            await response.json();

        output.textContent =
            JSON.stringify(
                data,
                null,
                2
            );

        if(data.mission_id){

            pollMission(
                data.mission_id
            );
        }

    }catch(error){

        output.textContent =
            JSON.stringify(
                {
                    error:
                        String(error)
                },
                null,
                2
            );
    }
}


function runMission(){

    const objective =
        document.getElementById(
            "objective"
        ).value;

    createMission(
        "/run",
        {
            objective:
                objective,
            research:true,
            verify:true,
            remember:false,
            external_access:true,
            execute:false,
            require_approval:true
        }
    );
}


function runCommand(){

    const objective =
        document.getElementById(
            "objective"
        ).value;

    createMission(
        "/command",
        {
            objective:
                objective,
            research:true,
            verify:true,
            remember:false,
            external_access:true,
            execute:true,
            require_approval:true
        }
    );
}


async function pollMission(id){

    try{

        const response =
            await fetch(
                "/mission/"
                + id
            );

        const data =
            await response.json();

        document.getElementById(
            "result"
        ).textContent =
            JSON.stringify(
                data,
                null,
                2
            );

        if(
            data.status === "accepted"
            ||
            data.status === "running"
            ||
            data.status === "approved"
        ){

            setTimeout(
                () =>
                    pollMission(id),
                1500
            );
        }

    }catch(error){

        setTimeout(
            () =>
                pollMission(id),
            2500
        );
    }
}


checkHealth();

</script>

</body>
</html>
"""


@app.get(
    "/interface",
    response_class=HTMLResponse
)
def interface():

    return ui()


# ============================================================
# SERVER
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000"
            )
        )
    )
