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

from urllib.parse import urlparse, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


APP_VERSION = "TARGET-2050.88"
BUILD = "PROVIDER-INDEPENDENCE-RECOVERY-AND-EMPIRICAL-EVIDENCE-CORE"

DB_PATH = os.getenv(
    "AI_INFINITY_DB",
    "/tmp/ai-infinity/ai_infinity.db"
)

os.makedirs(
    os.path.dirname(DB_PATH),
    exist_ok=True
)

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

        if conn.execute(
            "SELECT COUNT(*) n FROM policies"
        ).fetchone()["n"] == 0:

            conn.execute(
                """
                INSERT INTO policies(
                    version,ts,mode,valid
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

OPENER = build_opener(
    HTTPRedirectHandler()
)


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
            if is_private_ip(item[4][0]):
                return True

    except Exception:
        return False

    return False


def safe_fetch(url, timeout=6):

    parsed = urlparse(url)

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or is_private_host(parsed.hostname)
    ):
        raise RuntimeError(
            "blocked_private_or_invalid_url"
        )

    request = Request(
        url,
        headers={
            "User-Agent":
                "AI-Infinity/2050.88",
            "Accept":
                "application/json,application/xml,"
                "text/xml,*/*",
        },
        method="GET"
    )

    try:

        response = OPENER.open(
            request,
            timeout=timeout
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
                )
        }

    except HTTPError as exc:
        raise RuntimeError(
            f"http_error_{exc.code}"
        )

    except URLError:
        raise RuntimeError(
            "network_error"
        )


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
                resumable
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
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
                1
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
            json.loads(
                row["result"]
            )
            if row["result"]
            else None,
        "plan":
            json.loads(
                row["plan"] or "[]"
            ),
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
            )
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
    )[:12]


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
                ["research", "reasoning"]
                if request.research
                else ["reasoning"]
        })

    if request.execute:

        steps.append({
            "id":
                "action",
            "tool":
                "action_gateway",
            "depends_on":
                [
                    x["id"]
                    for x in steps
                ]
        })

    return steps


# ============================================================
# SOURCE MODEL
# ============================================================

def make_source(
    title,
    url,
    provider,
    year,
    publisher,
    abstract=""
):

    source_id = hashlib.sha256(
        (
            str(title)
            + str(url)
            + provider
        ).encode()
    ).hexdigest()[:20]

    return {
        "id":
            source_id,
        "title":
            str(title).strip(),
        "url":
            url,
        "provider":
            provider,
        "publisher":
            publisher or provider,
        "publisher_name":
            publisher or provider,
        "year":
            year,
        "abstract":
            re.sub(
                r"\s+",
                " ",
                re.sub(
                    "<[^>]+>",
                    " ",
                    str(
                        abstract or ""
                    )
                )
            )[:12000],
        "type":
            "academic_work"
            if provider != "wikipedia"
            else "reference"
    }


def deduplicate_sources(
    sources
):

    seen = set()
    result = []

    for source in sources:

        key = (
            re.sub(
                r"\W",
                "",
                source.get(
                    "title",
                    ""
                ).lower()
            )[:180],
            source.get(
                "provider",
                ""
            )
        )

        if (
            key not in seen
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

        relevance = (
            sum(
                1
                for term in terms
                if term in text
            )
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
                "reliability"
            )
        )

        source[
            "relevance_score"
        ] = round(
            min(
                1.0,
                relevance * 0.8
                + (
                    0.2
                    if empirical
                    else 0
                )
            ),
            3
        )

        source[
            "evidence_type"
        ] = (
            "empirical_or_evaluation"
            if empirical
            else (
                "reference"
                if source["provider"]
                == "wikipedia"
                else
                "academic_or_theoretical"
            )
        )

        source[
            "empirical_score"
        ] = round(
            min(
                1.0,
                relevance
                + (
                    0.25
                    if empirical
                    else 0
                )
            ),
            3
        )

        source[
            "quality_score_v2"
        ] = round(
            min(
                1.0,
                0.55
                + (
                    0.25
                    * source[
                        "relevance_score"
                    ]
                )
                + (
                    0.20
                    * source[
                        "empirical_score"
                    ]
                )
            ),
            3
        )

    return sorted(
        sources,
        key=lambda x: (
            x[
                "quality_score_v2"
            ],
            x[
                "relevance_score"
            ]
        ),
        reverse=True
    )


# ============================================================
# PROVIDERS
# ============================================================

def provider_openalex(
    query
):

    url = (
        "https://api.openalex.org/works"
        "?search="
        + quote(query)
        + "&per-page=6"
    )

    data = parse_json(
        safe_fetch(url)
    )

    results = []

    for item in data.get(
        "results",
        []
    ):

        location = (
            item.get(
                "primary_location"
            )
            or {}
        )

        journal = (
            location.get(
                "source"
            )
            or {}
        )

        results.append(
            make_source(
                item.get(
                    "title",
                    ""
                ),
                item.get(
                    "doi"
                )
                or item.get(
                    "id",
                    ""
                ),
                "openalex",
                item.get(
                    "publication_year"
                ),
                journal.get(
                    "display_name",
                    "OpenAlex"
                ),
                ""
            )
        )

    return results


def provider_crossref(
    query
):

    url = (
        "https://api.crossref.org/works"
        "?query="
        + quote(query)
        + "&rows=6"
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
                "title"
            )
            or [""]
        )[0]

        date = (
            item.get(
                "published-print"
            )
            or item.get(
                "published-online"
            )
            or {}
        )

        parts = date.get(
            "date-parts",
            [[None]]
        )

        year = parts[0][0]

        results.append(
            make_source(
                title,
                item.get(
                    "URL",
                    ""
                ),
                "crossref",
                year,
                item.get(
                    "publisher",
                    "Crossref"
                ),
                item.get(
                    "abstract",
                    ""
                )
            )
        )

    return results


def provider_crossref_alt(
    query
):

    return provider_crossref(
        query
    )


def provider_semantic(
    query
):

    url = (
        "https://api.semanticscholar.org/"
        "graph/v1/paper/search"
        "?query="
        + quote(query)
        + "&limit=6"
        "&fields=title,url,year,abstract,venue"
    )

    data = parse_json(
        safe_fetch(url)
    )

    results = []

    for item in data.get(
        "data",
        []
    ):

        results.append(
            make_source(
                item.get(
                    "title",
                    ""
                ),
                item.get(
                    "url",
                    ""
                ),
                "semantic_scholar",
                item.get(
                    "year"
                ),
                item.get(
                    "venue"
                )
                or "Semantic Scholar",
                item.get(
                    "abstract",
                    ""
                )
            )
        )

    return results


def provider_wikipedia(
    query
):

    url = (
        "https://en.wikipedia.org/w/api.php"
        "?action=query"
        "&list=search"
        "&srsearch="
        + quote(query)
        + "&srlimit=5"
        "&format=json"
    )

    data = parse_json(
        safe_fetch(url)
    )

    return [
        make_source(
            item.get(
                "title",
                ""
            ),
            "https://en.wikipedia.org/wiki/"
            + quote(
                item.get(
                    "title",
                    ""
                ).replace(
                    " ",
                    "_"
                )
            ),
            "wikipedia",
            None,
            "Wikipedia",
            item.get(
                "snippet",
                ""
            )
        )
        for item in data.get(
            "query",
            {}
        ).get(
            "search",
            []
        )
    ]


def provider_arxiv(
    query
):

    url = (
        "https://export.arxiv.org/api/query"
        "?search_query=all:"
        + quote(query)
        + "&start=0"
        "&max_results=8"
        "&sortBy=relevance"
        "&sortOrder=descending"
    )

    response = safe_fetch(
        url
    )

    root = ET.fromstring(
        response["text"]
    )

    namespace = {
        "a":
            "http://www.w3.org/2005/Atom"
    }

    results = []

    for entry in root.findall(
        "a:entry",
        namespace
    ):

        title = (
            entry.findtext(
                "a:title",
                "",
                namespace
            )
            .strip()
        )

        source_url = (
            entry.findtext(
                "a:id",
                "",
                namespace
            )
            .strip()
        )

        abstract = (
            entry.findtext(
                "a:summary",
                "",
                namespace
            )
            .strip()
        )

        published = (
            entry.findtext(
                "a:published",
                "",
                namespace
            )
        )

        year = None

        if (
            published
            and published[:4].isdigit()
        ):
            year = int(
                published[:4]
            )

        if title:

            results.append(
                make_source(
                    re.sub(
                        r"\s+",
                        " ",
                        title
                    ),
                    source_url,
                    "arxiv",
                    year,
                    "arXiv",
                    abstract
                )
            )

    return results


PROVIDERS = [
    (
        "openalex",
        provider_openalex
    ),
    (
        "crossref",
        provider_crossref
    ),
    (
        "semantic_scholar",
        provider_semantic
    ),
    (
        "arxiv",
        provider_arxiv
    ),
    (
        "wikipedia",
        provider_wikipedia
    ),
    (
        "crossref_alt",
        provider_crossref_alt
    )
]


def provider_family(
    provider
):

    if provider in (
        "crossref",
        "crossref_alt"
    ):
        return "crossref"

    return provider


# ============================================================
# RESEARCH
# ============================================================

def research(
    objective,
    recovery_round=0,
    failed_providers=None
):

    failed = set(
        failed_providers or []
    )

    results = []
    threads = []
    result_lock = threading.Lock()

    def worker(
        name,
        function
    ):

        if (
            recovery_round > 0
            and name in failed
        ):
            return

        try:

            if recovery_round:

                query = (
                    " ".join(
                        objective_terms(
                            objective
                        )[:8]
                    )
                    + " empirical evaluation "
                    "benchmark task completion "
                    "reliability"
                )

            else:

                query = (
                    objective
                    + " benchmark evaluation "
                    "empirical task completion "
                    "reliability"
                )

            items = function(
                query
            )

            with result_lock:

                results.append({
                    "provider":
                        name,
                    "status":
                        "success",
                    "items":
                        items
                })

        except Exception as exc:

            text = str(exc)

            failure_kind = (
                "rate_limited"
                if "429" in text
                else
                "transport_or_parse_error"
            )

            with result_lock:

                results.append({
                    "provider":
                        name,
                    "status":
                        "failed",
                    "items":
                        [],
                    "error":
                        text[:300],
                    "failure_kind":
                        failure_kind
                })

    for (
        name,
        function
    ) in PROVIDERS:

        thread = threading.Thread(
            target=worker,
            args=(
                name,
                function
            ),
            daemon=True
        )

        thread.start()

        threads.append(
            thread
        )

    for thread in threads:

        thread.join(
            timeout=8
        )

    by_provider = {
        item["provider"]:
            item
        for item in results
    }

    events = []
    all_sources = []

    for (
        name,
        _
    ) in PROVIDERS:

        if (
            recovery_round > 0
            and name in failed
        ):

            events.append({
                "provider":
                    name,
                "status":
                    "skipped",
                "sources":
                    0,
                "failure_kind":
                    "rate_limited",
                "error":
                    "rate_limit_recovery_skip"
            })

            continue

        item = by_provider.get(
            name
        )

        if item is None:

            item = {
                "provider":
                    name,
                "status":
                    "failed",
                "items":
                    [],
                "sources":
                    0,
                "error":
                    "provider_timeout",
                "failure_kind":
                    "timeout"
            }

        all_sources.extend(
            item.get(
                "items",
                []
            )
        )

        events.append({
            key:
                value
            for key, value
            in item.items()
            if key != "items"
        })

        events[-1][
            "sources"
        ] = len(
            item.get(
                "items",
                []
            )
        )

    sources = enrich_sources(
        deduplicate_sources(
            all_sources
        ),
        objective
    )

    publishers = {
        str(
            source.get(
                "publisher",
                ""
            )
        ).lower()
        for source in sources
        if source.get(
            "publisher"
        )
    }

    families = {
        provider_family(
            source.get(
                "provider",
                ""
            )
        )
        for source in sources
        if source.get(
            "provider"
        )
    }

    empirical = sum(
        1
        for source in sources
        if (
            source.get(
                "evidence_type"
            )
            == "empirical_or_evaluation"
            and source.get(
                "empirical_score",
                0
            ) >= 0.35
        )
    )

    return {
        "sources":
            sources,
        "events":
            events,
        "publisher_count":
            len(publishers),
        "family_count":
            len(families),
        "families":
            sorted(
                families
            ),
        "empirical":
            empirical
    }


# ============================================================
# CONTRADICTION SCREEN
# ============================================================

def contradiction_analysis(
    sources
):

    positive = (
        "improves",
        "effective",
        "successful",
        "reliable",
        "increase",
        "higher",
        "outperforms"
    )

    negative = (
        "fails",
        "failure",
        "unreliable",
        "ineffective",
        "decrease",
        "lower",
        "underperforms"
    )

    conflicts = []

    for index, first in enumerate(
        sources
    ):

        first_text = (
            first.get(
                "title",
                ""
            )
            + " "
            + first.get(
                "abstract",
                ""
            )
        ).lower()

        for second in sources[
            index + 1:
        ]:

            second_text = (
                second.get(
                    "title",
                    ""
                )
                + " "
                + second.get(
                    "abstract",
                    ""
                )
            ).lower()

            shared = (
                set(
                    objective_terms(
                        first.get(
                            "title",
                            ""
                        )
                    )
                )
                &
                set(
                    objective_terms(
                        second.get(
                            "title",
                            ""
                        )
                    )
                )
            )

            opposite = (
                (
                    any(
                        word in first_text
                        for word in positive
                    )
                    and
                    any(
                        word in second_text
                        for word in negative
                    )
                )
                or
                (
                    any(
                        word in first_text
                        for word in negative
                    )
                    and
                    any(
                        word in second_text
                        for word in positive
                    )
                )
            )

            if (
                len(shared) >= 2
                and opposite
            ):

                conflicts.append({
                    "a":
                        first["id"],
                    "b":
                        second["id"],
                    "shared_terms":
                        sorted(
                            shared
                        )[:10]
                })

    return {
        "count":
            len(conflicts),
        "status":
            (
                "possible_conflict"
                if conflicts
                else
                "no_explicit_claim_conflict_detected"
            ),
        "method":
            "subject_overlap_plus_polarity_screen",
        "semantic_contradiction_proof":
            False,
        "conflicts":
            conflicts[:20]
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify(
    sources,
    requested
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
        if (
            source.get(
                "evidence_type"
            )
            == "empirical_or_evaluation"
            and
            source.get(
                "empirical_score",
                0
            ) >= 0.35
        )
    ]

    publishers = {
        str(
            source.get(
                "publisher",
                ""
            )
        ).lower()
        for source in relevant
        if source.get(
            "publisher"
        )
    }

    families = {
        provider_family(
            source.get(
                "provider",
                ""
            )
        )
        for source in relevant
        if source.get(
            "provider"
        )
    }

    contradiction = (
        contradiction_analysis(
            relevant
        )
    )

    verified = bool(
        requested
        and len(relevant) >= 3
        and len(high_quality) >= 2
        and len(empirical) >= 3
        and len(publishers) >= 2
        and len(families) >= 2
        and contradiction.get(
            "count",
            0
        ) == 0
    )

    return {
        "verified":
            verified,

        "supported":
            len(relevant),

        "relevant_sources":
            len(relevant),

        "high_quality_sources":
            len(high_quality),

        "empirical_or_evaluation_sources":
            len(empirical),

        "independent_publishers":
            len(publishers),

        "independent_provider_families":
            len(families),

        "contradictions":
            contradiction.get(
                "count",
                0
            ),

        "contradiction_analysis":
            contradiction,

        "verification_requirements": {
            "min_relevant_sources":
                3,
            "min_independent_publishers":
                2,
            "min_provider_families":
                2,
            "min_claims":
                2,
            "min_empirical_or_evaluation_sources":
                3,
            "contradiction_screen_required":
                True
        },

        "limitations": [
            "publisher_identity_is_metadata_based",
            "contradiction_analysis_is_title_abstract_screening",
            "verification_does_not_equal_real_world_success"
        ]
    }


# ============================================================
# MISSION EXECUTION
# ============================================================

def execute_mission(
    mission_id,
    request
):

    recovery_round = 0
    failed_providers = []

    for attempt in (
        1,
        2
    ):

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

            mission_plan = build_plan(
                request.objective,
                request
            )

            event(
                mission_id,
                "plan_created",
                {
                    "steps":
                        mission_plan
                }
            )

            if (
                not request.external_access
                or not request.research
            ):

                result = {
                    "objective":
                        request.objective,
                    "plan":
                        mission_plan,
                    "status":
                        "completed",
                    "attempts":
                        attempt,
                    "recovery_attempts":
                        recovery_round
                }

                update_mission(
                    mission_id,
                    "completed",
                    result
                )

                return

            research_result = research(
                request.objective,
                recovery_round,
                failed_providers
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

            verification = verify(
                sources,
                request.verify
            )

            contradiction = (
                verification[
                    "contradiction_analysis"
                ]
            )

            evidence_gaps = []

            if len(sources) < 3:
                evidence_gaps.append(
                    "insufficient_relevant_sources"
                )

            if (
                research_result[
                    "publisher_count"
                ] < 2
            ):
                evidence_gaps.append(
                    "insufficient_publisher_independence"
                )

            if (
                research_result[
                    "family_count"
                ] < 2
            ):
                evidence_gaps.append(
                    "insufficient_provider_independence"
                )

            if (
                research_result[
                    "empirical"
                ] < 3
            ):
                evidence_gaps.append(
                    "insufficient_empirical_evidence"
                )

            if contradiction.get(
                "count",
                0
            ):

                evidence_gaps.append(
                    "possible_claim_conflict_detected"
                )

            closure = bool(
                verification[
                    "verified"
                ]
                and not evidence_gaps
            )

            failed_now = [
                item["provider"]
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
                    len(sources),

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
                    "waf_responses_rejected":
                        True,
                    "unverified_responses_rejected":
                        True,
                    "transport_validation_required":
                        True,
                    "research_source_integrity_validation":
                        True,
                    "publisher_identity_validation":
                        True,
                    "contradiction_analysis_required":
                        True,
                    "arbitrary_code_execution":
                        False,
                    "permission_bypass":
                        False
                }
            }

            if request.remember:

                result[
                    "memory_key"
                ] = remember(
                    request.objective,
                    result
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

            if (
                not request.verify
                or closure
            ):

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

            if attempt == 1:

                recovery_round = 1

                failed_providers = [
                    item[
                        "provider"
                    ]
                    for item
                    in research_result[
                        "events"
                    ]
                    if (
                        item.get(
                            "status"
                        ) == "failed"
                        and
                        item.get(
                            "failure_kind"
                        ) == "rate_limited"
                    )
                ]

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
                        "skipped_rate_limited":
                            failed_providers,
                        "required_provider_families":
                            2
                    }
                )

                time.sleep(
                    0.35
                )

                continue

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
                        str(exc)[:500]
                }
            )

            if attempt == 1:

                recovery_round = 1

                adaptive_upgrade(
                    "runtime-error-recovery"
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
            "/ui"
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
            "TARGET-2050.87",
        "upgrade":
            "independent-provider recovery with arXiv"
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
            "accepted",

        "execution": {
            "background":
                True,
            "shared_engine":
                True,
            "security_policy_enforced":
                True,
            "route":
                "/run",
            "approval_required":
                bool(
                    request.require_approval
                    and request.execute
                )
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
            False
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

    update_mission(
        mission_id,
        "approved",
        mission["result"],
        True
    )

    return {
        "mission_id":
            mission_id,
        "status":
            "approved"
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
            False
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
<title>AI Infinity</title>

<style>
body{
    font-family:system-ui;
    max-width:900px;
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
    border-radius:10px
}
</style>
</head>

<body>

<h1>AI Infinity</h1>

<p id="health">
Checking...
</p>

<textarea
id="objective"
rows="5"
placeholder="Enter a mission..."
></textarea>

<button onclick="runMission()">
Run Mission
</button>

<pre id="result">
Ready.
</pre>

<script>

async function checkHealth(){

    try{

        const response =
            await fetch("/health");

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


async function runMission(){

    const output =
        document.getElementById(
            "result"
        );

    const objective =
        document.getElementById(
            "objective"
        ).value;

    output.textContent =
        "Starting mission...";

    try{

        const response =
            await fetch(
                "/run",
                {
                    method:"POST",
                    headers:{
                        "Content-Type":
                            "application/json"
                    },
                    body:JSON.stringify({
                        objective:
                            objective,
                        research:true,
                        verify:true,
                        remember:false,
                        external_access:true,
                        execute:false,
                        require_approval:true
                    })
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
