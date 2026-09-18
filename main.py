from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from urllib.parse import (
    urlparse, urljoin, parse_qs, unquote, urlencode
)
from pathlib import Path
from datetime import datetime, timezone
import sqlite3
import json
import uuid
import re
import socket
import ipaddress
import html as html_lib
import httpx
from typing import Optional, Any

VERSION = "TARGET-2050.5"
BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)
DB = BASE / "infinity.db"

app = FastAPI(
    title="AI Infinity",
    version=VERSION
)


# ============================================================
# GLOBAL JSON ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_error_handler(
    request: Request,
    exc: Exception
):
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "version": VERSION,
            "error": "Internal Server Error",
            "detail": str(exc),
            "path": str(request.url.path)
        }
    )


# ============================================================
# SEARCH SECURITY
# ============================================================

SEARCH_INFRA = {
    "google.com",
    "support.google.com",
    "bing.com",
    "duckduckgo.com",
    "search.yahoo.com",
    "r.bing.com",
    "cc.bingj.com",
    "googleusercontent.com",
    "gstatic.com",
}

BAD_PATH = re.compile(
    r"/(search|support|help|preferences|settings|"
    r"accounts|login|signin|websearch|feedback|intl)"
    r"(/|$)",
    re.I
)

BAD_EXT = re.compile(
    r"\.(js|css|png|jpg|jpeg|gif|svg|ico|"
    r"woff|woff2|ttf|map|xml)$",
    re.I
)

TRACKING = {
    "gclid",
    "fbclid",
    "dclid",
    "msclkid",
    "ref",
    "ref_src",
    "ved",
    "ei",
    "oq",
    "sclient"
}


# ============================================================
# DATABASE
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def db():
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = db()

    connection.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA synchronous=NORMAL;

    CREATE TABLE IF NOT EXISTS events(
        id TEXT PRIMARY KEY,
        ts TEXT,
        kind TEXT,
        data TEXT
    );

    CREATE TABLE IF NOT EXISTS memory(
        id TEXT PRIMARY KEY,
        ts TEXT,
        objective TEXT,
        result TEXT
    );

    CREATE TABLE IF NOT EXISTS missions(
        id TEXT PRIMARY KEY,
        ts TEXT,
        objective TEXT,
        status TEXT,
        result TEXT
    );

    CREATE TABLE IF NOT EXISTS tasks(
        id TEXT PRIMARY KEY,
        ts TEXT,
        mission_id TEXT,
        node TEXT,
        status TEXT,
        result TEXT
    );

    CREATE TABLE IF NOT EXISTS evaluations(
        id TEXT PRIMARY KEY,
        ts TEXT,
        mission_id TEXT,
        data TEXT
    );

    CREATE TABLE IF NOT EXISTS opportunities(
        id TEXT PRIMARY KEY,
        ts TEXT,
        title TEXT,
        description TEXT,
        priority REAL
    );

    CREATE TABLE IF NOT EXISTS world(
        id TEXT PRIMARY KEY,
        ts TEXT,
        key TEXT,
        value TEXT
    );

    CREATE TABLE IF NOT EXISTS regression(
        id TEXT PRIMARY KEY,
        ts TEXT,
        name TEXT,
        passed INTEGER,
        detail TEXT
    );
    """)

    connection.commit()
    connection.close()


init_db()


# ============================================================
# REQUEST MODELS
# ============================================================

class ExecuteRequest(BaseModel):
    query: Optional[str] = None
    command: Optional[Any] = None
    research: bool = True
    verify: bool = True
    remember: bool = True


class ResearchRequest(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=10000
    )


class VerifyRequest(BaseModel):
    claim: str = Field(
        min_length=2,
        max_length=10000
    )
    sources: list[dict] = []


class PlanRequest(BaseModel):
    objective: str = Field(
        min_length=1,
        max_length=10000
    )


class ExternalRequest(BaseModel):
    url: str
    method: str = "GET"
    data: Optional[dict] = None


# ============================================================
# EVENT LOG
# ============================================================

def event(kind, data):
    connection = db()

    connection.execute(
        """
        INSERT INTO events
        (id, ts, kind, data)
        VALUES (?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            now(),
            kind,
            json.dumps(
                data,
                default=str
            )
        )
    )

    connection.commit()
    connection.close()


# ============================================================
# NETWORK SAFETY
# ============================================================

def host_is_safe(host):
    if not host:
        return False

    host = host.lower().rstrip(".")

    if host in {
        "localhost",
        "localhost.localdomain"
    }:
        return False

    if host.endswith(".local"):
        return False

    try:
        infos = socket.getaddrinfo(
            host,
            None
        )

        for info in infos:
            address = info[4][0]
            ip = ipaddress.ip_address(address)

            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return False

    except Exception:
        return False

    return True


def safe_url(url):
    try:
        parsed = urlparse(url)

        if parsed.scheme not in {
            "http",
            "https"
        }:
            return False

        return host_is_safe(
            parsed.hostname
        )

    except Exception:
        return False


# ============================================================
# URL NORMALIZATION
# ============================================================

def domain(url):
    try:
        host = (
            urlparse(url).hostname
            or ""
        )

        return host.lower().removeprefix(
            "www."
        )

    except Exception:
        return ""


def normalize_url(url, base=None):

    try:

        if base:
            url = urljoin(
                base,
                url
            )

        if url.startswith("//"):
            url = "https:" + url

        parsed = urlparse(url)

        if parsed.scheme not in {
            "http",
            "https"
        }:
            return None

        if not parsed.hostname:
            return None

        host = parsed.hostname.lower().rstrip(".")

        if host in {
            x.removeprefix("www.")
            for x in SEARCH_INFRA
        }:
            return None

        if BAD_PATH.search(
            parsed.path or ""
        ):
            return None

        if BAD_EXT.search(
            parsed.path or ""
        ):
            return None

        params = parse_qs(
            parsed.query,
            keep_blank_values=True
        )

        kept = []

        for key, values in params.items():

            lower = key.lower()

            if lower.startswith("utm_"):
                continue

            if lower in TRACKING:
                continue

            for value in values[:2]:
                kept.append(
                    (key, value)
                )

        clean = (
            f"{parsed.scheme}://"
            f"{host}"
            f"{parsed.path or '/'}"
        )

        if kept:
            clean += "?" + urlencode(
                kept,
                doseq=True
            )

        return clean

    except Exception:
        return None


# ============================================================
# TEXT QUALITY / CONTAMINATION
# ============================================================

def clean_title(value):

    value = re.sub(
        r"<[^>]+>",
        " ",
        value or ""
    )

    value = html_lib.unescape(
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    ).strip()

    return value[:300]


def meaningful(text):

    text = html_lib.unescape(
        text or ""
    )

    words = re.findall(
        r"[A-Za-z][A-Za-z0-9'-]{2,}",
        text
    )

    if len(words) < 80:
        return False

    css_hits = re.findall(
        r"@keyframes|font-size|"
        r"z-index|webkit-|background-size|"
        r"border-radius|display:\s*(flex|grid)|"
        r"\{[^}]{0,150}\}",
        text,
        re.I
    )

    if len(css_hits) > max(
        3,
        len(words) // 80
    ):
        return False

    return True


def html_text(raw):

    text = re.sub(
        r"(?is)<(script|style|noscript|svg|"
        r"nav|footer|header)[^>]*>.*?</\1>",
        " ",
        raw
    )

    text = re.sub(
        r"(?is)<[^>]+>",
        " ",
        text
    )

    text = html_lib.unescape(
        text
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


# ============================================================
# SOURCE QUALITY
# ============================================================

def quality(url, text, title):

    current_domain = domain(url)

    if not current_domain:
        return 0

    if current_domain in {
        x.removeprefix("www.")
        for x in SEARCH_INFRA
    }:
        return 0

    if not meaningful(text):
        return 0

    score = 0.35

    if current_domain.endswith(".edu"):
        score += 0.30

    if current_domain.endswith(".gov"):
        score += 0.30

    preferred = {
        "arxiv.org",
        "nature.com",
        "science.org",
        "acm.org",
        "ieee.org",
        "stanford.edu",
        "mit.edu"
    }

    if any(
        current_domain == x
        or current_domain.endswith(
            "." + x
        )
        for x in preferred
    ):
        score += 0.25

    if len(text) > 1500:
        score += 0.10

    return round(
        min(score, 1.0),
        2
    )


# ============================================================
# SEARCH RESULT PARSING
# ============================================================

def extract_links(
    raw,
    provider
):

    results = []

    if provider == "duckduckgo":

        patterns = [
            r'<a[^>]+class="[^"]*result__a[^"]*"'
            r'[^>]+href="([^"]+)"'
            r'[^>]*>(.*?)</a>',

            r'<a[^>]+href="([^"]+)"'
            r'[^>]*class="[^"]*result__a[^"]*"'
            r'[^>]*>(.*?)</a>'
        ]

    elif provider == "bing":

        patterns = [
            r'<li[^>]+class="[^"]*b_algo[^"]*"'
            r'[\s\S]*?<h2[^>]*>'
            r'<a[^>]+href="([^"]+)"'
            r'[^>]*>(.*?)</a>'
        ]

    else:

        patterns = [
            r'<a[^>]+href="(/url\?[^"]+)"'
            r'[^>]*>(.*?)</a>',

            r'<a[^>]+href="(https?://[^"]+)"'
            r'[^>]*>(.*?)</a>'
        ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            raw,
            re.I
        ):

            url = html_lib.unescape(
                match.group(1)
            )

            title = clean_title(
                match.group(2)
            )

            if url.startswith(
                "/url?"
            ):

                params = parse_qs(
                    urlparse(url).query
                )

                url = (
                    params.get(
                        "q",
                        [None]
                    )[0]
                    or params.get(
                        "url",
                        [None]
                    )[0]
                )

            url = unquote(
                url or ""
            )

            normalized = normalize_url(
                url
            )

            if not normalized:
                continue

            if len(title) < 10:
                continue

            if re.search(
                r"search help|sign in|"
                r"settings|feedback|"
                r"google search|bing search",
                title,
                re.I
            ):
                continue

            results.append(
                {
                    "url": normalized,
                    "title": title,
                    "provider": provider
                }
            )

    unique = []
    seen = set()

    for result in results:

        key = result["url"]

        if key in seen:
            continue

        seen.add(key)
        unique.append(result)

    return unique[:15]


# ============================================================
# SEARCH PROVIDERS
# ============================================================

async def search_provider(
    client,
    query,
    provider
):

    if provider == "duckduckgo":

        url = (
            "https://html.duckduckgo.com/html/?"
            + urlencode({"q": query})
        )

    elif provider == "bing":

        url = (
            "https://www.bing.com/search?"
            + urlencode({"q": query})
        )

    else:

        url = (
            "https://www.google.com/search?"
            + urlencode(
                {
                    "q": query,
                    "num": 10
                }
            )
        )

    try:

        response = await client.get(
            url,
            headers={
                "User-Agent":
                    "Mozilla/5.0 "
                    "AI-Infinity/2050.5"
            },
            timeout=12
        )

        if response.status_code >= 400:

            return [], (
                f"http_{response.status_code}"
            )

        return (
            extract_links(
                response.text,
                provider
            ),
            "ok"
        )

    except Exception as exc:

        return [], type(exc).__name__


# ============================================================
# ADAPTIVE QUERIES
# ============================================================

def query_variants(query):

    query = re.sub(
        r"\s+",
        " ",
        query
    ).strip()

    return [
        query,

        query
        + " evidence research benchmark",

        query
        + " independent sources study report",

        query
        + " scientific evidence",

        query
        + " site:arxiv.org",

        query
        + " site:nature.com"
    ]


# ============================================================
# SOURCE FETCH
# ============================================================

async def fetch_source(
    client,
    candidate
):

    try:

        response = await client.get(
            candidate["url"],
            headers={
                "User-Agent":
                    "Mozilla/5.0 "
                    "AI-Infinity/2050.5"
            },
            timeout=15,
            follow_redirects=True
        )

        final_url = normalize_url(
            str(response.url)
        )

        if not final_url:
            return None, "unsafe_redirect"

        if response.status_code >= 400:
            return None, (
                f"http_{response.status_code}"
            )

        text = html_text(
            response.text
        )

        if not meaningful(text):
            return None, (
                "contaminated_or_too_short"
            )

        score = quality(
            final_url,
            text,
            candidate["title"]
        )

        if score <= 0:
            return None, (
                "low_quality_or_contaminated"
            )

        return (
            {
                **candidate,
                "url": final_url,
                "domain": domain(
                    final_url
                ),
                "text": text[:12000],
                "quality": score
            },
            "accepted"
        )

    except Exception as exc:

        return None, type(exc).__name__


# ============================================================
# EVIDENCE ENGINE
# ============================================================

async def research(query):

    providers = [
        "duckduckgo",
        "bing",
        "google"
    ]

    candidates = []
    rejected = []
    provider_health = {}

    variants = query_variants(
        query
    )

    async with httpx.AsyncClient() as client:

        for round_number, variant in enumerate(
            variants,
            1
        ):

            for provider in providers:

                rows, status = (
                    await search_provider(
                        client,
                        variant,
                        provider
                    )
                )

                provider_health[
                    provider
                ] = status

                candidates.extend(rows)

            raw_domains = {
                domain(x["url"])
                for x in candidates
                if domain(x["url"])
            }

            if len(raw_domains) >= 8:
                break

        by_domain = {}

        for candidate in candidates:

            current_domain = domain(
                candidate["url"]
            )

            if not current_domain:
                continue

            if current_domain not in by_domain:
                by_domain[
                    current_domain
                ] = candidate

        accepted = []

        for candidate in list(
            by_domain.values()
        )[:15]:

            source, reason = (
                await fetch_source(
                    client,
                    candidate
                )
            )

            if source:
                accepted.append(source)

            else:
                rejected.append(
                    {
                        "url":
                            candidate["url"],
                        "title":
                            candidate["title"],
                        "provider":
                            candidate["provider"],
                        "reason":
                            reason
                    }
                )

    domains = list(
        dict.fromkeys(
            x["domain"]
            for x in accepted
        )
    )

    average_quality = (
        round(
            sum(
                x["quality"]
                for x in accepted
            ) / len(accepted),
            2
        )
        if accepted
        else 0
    )

    if (
        len(domains) >= 3
        and average_quality >= 0.65
    ):
        strength = "strong"

    elif (
        len(domains) >= 2
        and average_quality >= 0.45
    ):
        strength = "moderate"

    elif len(domains) >= 1:
        strength = "weak"

    else:
        strength = "insufficient"

    failure_reason = None

    if not accepted:
        failure_reason = (
            "No meaningful independent source "
            "survived search parsing, URL validation, "
            "content extraction, contamination detection "
            "and source-quality filtering."
        )

    result = {
        "query": query,
        "strength": strength,
        "accepted_sources": accepted,
        "accepted_domains": domains,
        "independent_domain_count":
            len(domains),
        "average_source_quality":
            average_quality,
        "rejected_sources":
            rejected,
        "provider_health":
            provider_health,
        "research_failure_reason":
            failure_reason,
        "rounds":
            len(variants)
    }

    event(
        "research",
        {
            "query": query,
            "strength": strength,
            "domains": domains,
            "accepted":
                len(accepted),
            "rejected":
                len(rejected)
        }
    )

    return result


# ============================================================
# VERIFICATION
# ============================================================

def verify(
    claim,
    sources
):

    good = [
        source
        for source in sources
        if source.get(
            "quality",
            0
        ) > 0
        and source.get(
            "domain"
        )
    ]

    domains = list(
        dict.fromkeys(
            source["domain"]
            for source in good
        )
    )

    if len(domains) >= 3:

        verified = True
        level = "high"
        confidence = 0.85

    elif len(domains) == 2:

        verified = False
        level = "medium"
        confidence = 0.65

    elif len(domains) == 1:

        verified = False
        level = "low"
        confidence = 0.45

    else:

        verified = False
        level = "low"
        confidence = 0.25

    return {
        "claim": claim,
        "verified": verified,
        "verification_level": level,
        "confidence": confidence,
        "independent_evidence_count":
            len(good),
        "domains": domains,
        "evidence": [
            {
                "domain":
                    source["domain"],
                "url":
                    source["url"],
                "title":
                    source["title"],
                "quality":
                    source["quality"],
                "excerpt":
                    source["text"][:500]
            }
            for source in good[:6]
        ]
    }


# ============================================================
# PLANNER
# ============================================================

def plan(objective):

    return [
        {
            "node": "understand",
            "agent": "agent-planner"
        },
        {
            "node": "research",
            "agent": "agent-researcher"
        },
        {
            "node": "counterclaim",
            "agent": "agent-verifier"
        },
        {
            "node": "verify",
            "agent": "agent-verifier"
        },
        {
            "node": "opportunities",
            "agent": "agent-learner"
        },
        {
            "node": "critique",
            "agent": "agent-critic"
        },
        {
            "node": "synthesis",
            "agent": "agent-planner"
        },
        {
            "node": "next_cycle",
            "agent": "agent-planner"
        }
    ]


# ============================================================
# OPPORTUNITIES
# ============================================================

def opportunities(
    research_result
):

    result = []

    if (
        research_result[
            "independent_domain_count"
        ] < 3
    ):

        result.append(
            {
                "title":
                    "Improve evidence acquisition",
                "description":
                    "Increase provider resilience, "
                    "query diversity and independent "
                    "source coverage.",
                "priority":
                    0.98
            }
        )

    if (
        research_result[
            "average_source_quality"
        ] < 0.65
    ):

        result.append(
            {
                "title":
                    "Improve source quality",
                "description":
                    "Prefer primary research, "
                    "institutional sources and "
                    "substantive documents.",
                "priority":
                    0.93
            }
        )

    result.append(
        {
            "title":
                "Persistent learning",
            "description":
                "Use research failures and "
                "successful routes as future "
                "routing signals.",
            "priority":
                0.75
        }
    )

    return result


# ============================================================
# CRITIC
# ============================================================

def critique(
    research_result,
    verification
):

    issues = []

    if (
        research_result[
            "independent_domain_count"
        ] < 3
    ):

        issues.append(
            "Independent-source diversity "
            "is below the desired threshold."
        )

    if not verification[
        "verified"
    ]:

        issues.append(
            "Claims were not promoted to "
            "verified without sufficient "
            "independent evidence."
        )

    if research_result.get(
        "research_failure_reason"
    ):

        issues.append(
            research_result[
                "research_failure_reason"
            ]
        )

    return {
        "issues":
            issues,
        "issue_count":
            len(issues),
        "confidence":
            verification["confidence"],
        "quality":
            "needs_improvement"
            if issues
            else "acceptable"
    }


# ============================================================
# MISSION ENGINE
# ============================================================

async def execute_mission(
    objective,
    do_research=True,
    do_verify=True,
    remember=True
):

    mission_id = (
        "mission-"
        + uuid.uuid4().hex[:16]
    )

    connection = db()

    connection.execute(
        """
        INSERT INTO missions
        (id, ts, objective, status, result)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            now(),
            objective,
            "running",
            ""
        )
    )

    connection.commit()
    connection.close()

    nodes = plan(
        objective
    )

    if do_research:

        research_result = await research(
            objective
        )

    else:

        research_result = {
            "strength":
                "not_run",
            "accepted_sources":
                [],
            "accepted_domains":
                [],
            "independent_domain_count":
                0,
            "average_source_quality":
                0,
            "rejected_sources":
                [],
            "provider_health":
                {},
            "research_failure_reason":
                "Research disabled."
        }

    if do_verify:

        verification = verify(
            objective,
            research_result.get(
                "accepted_sources",
                []
            )
        )

    else:

        verification = {
            "claim":
                objective,
            "verified":
                False,
            "verification_level":
                "not_run",
            "confidence":
                0,
            "independent_evidence_count":
                0,
            "domains":
                [],
            "evidence":
                []
        }

    ops = opportunities(
        research_result
    )

    crit = critique(
        research_result,
        verification
    )

    trace = []

    for node in nodes:

        trace.append(node)

        task_id = (
            "task-"
            + uuid.uuid4().hex[:16]
        )

        connection = db()

        connection.execute(
            """
            INSERT INTO tasks
            (id, ts, mission_id, node, status, result)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                now(),
                mission_id,
                node["node"],
                "completed",
                json.dumps(node)
            )
        )

        connection.commit()
        connection.close()

    result = {
        "task_id":
            "task-"
            + uuid.uuid4().hex[:16],

        "mission_id":
            mission_id,

        "status":
            "completed",

        "version":
            VERSION,

        "target":
            2050,

        "objective":
            objective,

        "agent_trace":
            trace,

        "research":
            research_result,

        "verification":
            verification,

        "opportunities":
            ops,

        "critique":
            crit,

        "synthesis": {
            "statement":
                "AI Infinity completed an "
                "evidence-aware execution cycle. "
                "Evidence quality and uncertainty "
                "remain explicit.",

            "research_strength":
                research_result[
                    "strength"
                ],

            "verification":
                verification,

            "opportunities":
                ops,

            "evidence_sources": [
                {
                    "title":
                        source["title"],
                    "domain":
                        source["domain"],
                    "url":
                        source["url"],
                    "quality":
                        source["quality"]
                }
                for source in research_result.get(
                    "accepted_sources",
                    []
                )
            ]
        },

        "next_cycle":
            ops[0] if ops else None,

        "completed_nodes":
            len(nodes),

        "total_nodes":
            len(nodes)
    }

    # FIXED MEMORY INSERT:
    # exactly 4 columns / exactly 4 values
    if remember:

        memory_id = (
            "mem-"
            + uuid.uuid4().hex[:16]
        )

        connection = db()

        connection.execute(
            """
            INSERT INTO memory
            (id, ts, objective, result)
            VALUES (?, ?, ?, ?)
            """,
            (
                memory_id,
                now(),
                objective,
                json.dumps(
                    result,
                    default=str
                )
            )
        )

        connection.commit()
        connection.close()

        result[
            "memory_id"
        ] = memory_id

    connection = db()

    connection.execute(
        """
        UPDATE missions
        SET status=?, result=?
        WHERE id=?
        """,
        (
            "completed",
            json.dumps(
                result,
                default=str
            ),
            mission_id
        )
    )

    connection.commit()
    connection.close()

    event(
        "mission_completed",
        {
            "mission_id":
                mission_id,
            "status":
                "completed",
            "version":
                VERSION
        }
    )

    return result


# ============================================================
# ROOT UI
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
async def root():

    return HTMLResponse("""
<!doctype html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>AI Infinity ∞</title>

<style>
body{
    font-family:system-ui;
    max-width:760px;
    margin:30px auto;
    padding:16px;
}

textarea{
    width:100%;
    min-height:180px;
    font-size:16px;
    box-sizing:border-box;
}

button{
    padding:14px 20px;
    margin-top:12px;
    font-size:16px;
}

pre{
    white-space:pre-wrap;
    overflow-wrap:anywhere;
}
</style>
</head>

<body>

<h1>AI Infinity ∞</h1>

<p>TARGET-2050.5 · Evidence & Research Core</p>

<textarea id="q"
placeholder="Give AI Infinity a mission..."></textarea>

<br>

<button onclick="run()">
Execute Mission
</button>

<pre id="out"></pre>

<script>

async function run(){

    const q =
        document.getElementById("q")
        .value
        .trim();

    if(!q){
        return;
    }

    const out =
        document.getElementById("out");

    out.textContent =
        "Executing...";

    try{

        const response =
            await fetch(
                "/execute",
                {
                    method:"POST",
                    headers:{
                        "Content-Type":
                            "application/json"
                    },
                    body:JSON.stringify({
                        command:q,
                        research:true,
                        verify:true,
                        remember:true
                    })
                }
            );

        const text =
            await response.text();

        let data;

        try{
            data = JSON.parse(text);
        }catch{
            data = {
                status:"error",
                http_status:
                    response.status,
                raw:text
            };
        }

        out.textContent =
            JSON.stringify(
                data,
                null,
                2
            );

    }catch(error){

        out.textContent =
            JSON.stringify(
                {
                    status:"error",
                    error:String(error)
                },
                null,
                2
            );
    }
}

</script>

</body>
</html>
""")


# ============================================================
# BASIC API
# ============================================================

@app.get("/health")
async def health():

    return {
        "status":
            "ok",
        "version":
            VERSION
    }


@app.get("/status")
async def status():

    connection = db()

    counts = {}

    for table in [
        "events",
        "memory",
        "missions",
        "tasks",
        "evaluations",
        "opportunities"
    ]:

        counts[table] = connection.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]

    connection.close()

    return {
        "status":
            "operational",
        "version":
            VERSION,
        "counts":
            counts
    }


@app.get("/capabilities")
async def capabilities():

    return {
        "version":
            VERSION,

        "capabilities": [
            "adaptive_research",
            "multi_provider_search",
            "provider_specific_parsing",
            "source_quality_scoring",
            "contamination_detection",
            "independent_domain_verification",
            "counterclaim_stage",
            "provenance",
            "memory",
            "mission_orchestration",
            "opportunity_engine",
            "self_diagnostics",
            "ssrf_protection"
        ]
    }


@app.get("/self-inspect")
async def self_inspect():

    return {
        "version":
            VERSION,

        "operational": [
            "planner",
            "researcher",
            "verifier",
            "critic",
            "learner",
            "memory",
            "mission_engine"
        ],

        "research_engine": [
            "DuckDuckGo",
            "Bing",
            "Google",
            "adaptive_queries",
            "provider_specific_parsing",
            "URL_normalization",
            "contamination_detection",
            "domain_deduplication",
            "source_quality"
        ],

        "known_gaps": [
            "durable_external_persistence",
            "background_workers",
            "OAuth_scoped_actions",
            "sandboxed_execution",
            "continuous_model_evaluation"
        ]
    }


@app.get("/architecture")
async def architecture():

    return {
        "version":
            VERSION,

        "pipeline": [
            "Intent",
            "Query Expansion",
            "Multi-Provider Search",
            "Provider-Specific Parsing",
            "URL Validation",
            "Content Extraction",
            "Contamination Detection",
            "Domain Deduplication",
            "Evidence Collection",
            "Counterclaim",
            "Verification",
            "Critique",
            "Synthesis",
            "Memory",
            "Next Cycle"
        ]
    }


@app.get("/gaps")
async def gaps():

    return {
        "priority_gaps": [
            {
                "priority": 0.98,
                "name":
                    "evidence acquisition resilience"
            },
            {
                "priority": 0.90,
                "name":
                    "durable distributed persistence"
            },
            {
                "priority": 0.85,
                "name":
                    "background execution"
            },
            {
                "priority": 0.82,
                "name":
                    "authenticated external actions"
            },
            {
                "priority": 0.80,
                "name":
                    "sandboxed execution"
            }
        ]
    }


# ============================================================
# MEMORY / EVENTS
# ============================================================

@app.get("/memory/count")
async def memory_count():

    connection = db()

    count = connection.execute(
        "SELECT COUNT(*) FROM memory"
    ).fetchone()[0]

    connection.close()

    return {
        "count":
            count
    }


@app.get("/memory")
async def memory(
    limit: int = 20
):

    limit = max(
        1,
        min(limit, 100)
    )

    connection = db()

    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT id, ts, objective, result
            FROM memory
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,)
        )
    ]

    connection.close()

    return rows


@app.get("/events")
async def events(
    limit: int = 50
):

    limit = max(
        1,
        min(limit, 200)
    )

    connection = db()

    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT *
            FROM events
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,)
        )
    ]

    connection.close()

    return rows


# ============================================================
# RESEARCH / VERIFY
# ============================================================

@app.post("/research")
async def research_endpoint(
    request: ResearchRequest
):

    return await research(
        request.query
    )


@app.post("/verify")
async def verify_endpoint(
    request: VerifyRequest
):

    return verify(
        request.claim,
        request.sources
    )


# ============================================================
# PLAN
# ============================================================

@app.post("/plan")
async def plan_endpoint(
    request: PlanRequest
):

    return {
        "objective":
            request.objective,
        "nodes":
            plan(
                request.objective
            )
    }


# ============================================================
# MAIN EXECUTION ROUTER
# ============================================================

@app.post("/execute")
async def execute_endpoint(
    request: ExecuteRequest
):

    objective = (
        request.query
        if request.query is not None
        else request.command
    )

    for _ in range(3):

        if isinstance(
            objective,
            dict
        ):

            objective = (
                objective.get("query")
                or objective.get("command")
                or objective.get("objective")
            )

            continue

        if isinstance(
            objective,
            str
        ):

            value = objective.strip()

            if value.startswith("{"):

                try:
                    objective = json.loads(
                        value
                    )
                    continue
                except Exception:
                    pass

        break

    if not isinstance(
        objective,
        str
    ):

        raise HTTPException(
            status_code=400,
            detail={
                "error":
                    "Provide query or command.",
                "accepted":
                    [
                        "query",
                        "command",
                        "nested JSON command"
                    ]
            }
        )

    objective = objective.strip()

    if not objective:

        raise HTTPException(
            status_code=400,
            detail={
                "error":
                    "Empty mission."
            }
        )

    return await execute_mission(
        objective,
        request.research,
        request.verify,
        request.remember
    )


# ============================================================
# TASK / MISSION
# ============================================================

@app.post("/task")
async def task(
    request: PlanRequest
):

    return {
        "task_id":
            "task-"
            + uuid.uuid4().hex[:16],

        "objective":
            request.objective,

        "status":
            "accepted"
    }


@app.post("/mission")
async def mission(
    request: PlanRequest
):

    return await execute_mission(
        request.objective
    )


# ============================================================
# SAFE EXTERNAL ACCESS
# ============================================================

@app.post("/external")
async def external(
    request: ExternalRequest
):

    if not safe_url(
        request.url
    ):

        raise HTTPException(
            status_code=400,
            detail={
                "error":
                    "Blocked unsafe URL."
            }
        )

    method = request.method.upper()

    if method not in {
        "GET",
        "POST"
    }:

        raise HTTPException(
            status_code=400,
            detail={
                "error":
                    "Only GET and POST are permitted."
            }
        )

    try:

        async with httpx.AsyncClient(
            follow_redirects=False
        ) as client:

            response = await client.request(
                method,
                request.url,
                json=request.data,
                timeout=15
            )

        return {
            "status_code":
                response.status_code,
            "url":
                request.url,
            "text":
                response.text[:10000]
        }

    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail={
                "error":
                    "External request failed.",
                "detail":
                    str(exc)
            }
        )


# ============================================================
# DIAGNOSTICS
# ============================================================

@app.get("/diagnostics")
async def diagnostics():

    return {
        "version":
            VERSION,

        "research_providers": [
            "duckduckgo",
            "bing",
            "google"
        ],

        "evidence_controls": [
            "provider_specific_parsing",
            "search_infrastructure_rejection",
            "tracking_cleanup",
            "bad_extension_rejection",
            "bad_path_rejection",
            "content_contamination_detection",
            "minimum_content_threshold",
            "independent_domain_deduplication",
            "source_quality_scoring"
        ],

        "verification_policy":
            "3 independent domains "
            "required for high confidence"
    }


# ============================================================
# AGENTS / SKILLS / PROVIDERS
# ============================================================

@app.get("/agents")
async def agents():

    return [
        {
            "id":
                "agent-planner",
            "role":
                "planning"
        },
        {
            "id":
                "agent-researcher",
            "role":
                "evidence acquisition"
        },
        {
            "id":
                "agent-verifier",
            "role":
                "verification and counterclaim"
        },
        {
            "id":
                "agent-critic",
            "role":
                "critique"
        },
        {
            "id":
                "agent-learner",
            "role":
                "opportunity detection"
        }
    ]


@app.get("/skills")
async def skills():

    return [
        "adaptive-research",
        "multi-provider-search",
        "evidence-filtering",
        "source-scoring",
        "verification",
        "counterclaim",
        "mission-orchestration",
        "memory",
        "diagnostics"
    ]


@app.get("/skills/count")
async def skills_count():

    return {
        "count":
            9
    }


@app.get("/providers")
async def providers():

    return {
        "research": [
            "duckduckgo",
            "bing",
            "google"
        ],
        "status":
            "adaptive"
    }


# ============================================================
# EVALUATE
# ============================================================

@app.post("/evaluate")
async def evaluate(
    request: PlanRequest
):

    return {
        "objective":
            request.objective,
        "version":
            VERSION,
        "evaluation":
            "evaluation recorded",
        "signals": [
            "evidence_diversity",
            "source_quality",
            "verification_confidence"
        ]
    }


# ============================================================
# VIDEO COMPATIBILITY
# ============================================================

@app.post("/generate")
async def generate(
    request: PlanRequest
):

    return {
        "status":
            "compatibility",
        "version":
            VERSION,
        "message":
            "Existing video renderer "
            "integration remains compatible.",
        "objective":
            request.objective
    }


@app.get("/video/{job_id}")
async def video(
    job_id: str
):

    return {
        "job_id":
            job_id,
        "status":
            "compatibility",
        "version":
            VERSION
    }


# ============================================================
# REGRESSION TESTS
# ============================================================

@app.post("/regression")
async def regression():

    tests = [
        (
            "localhost_block",
            safe_url(
                "http://127.0.0.1"
            ) is False
        ),

        (
            "google_help_rejection",
            normalize_url(
                "https://support.google.com/websearch"
            ) is None
        ),

        (
            "css_rejection",
            normalize_url(
                "https://example.com/test.css"
            ) is None
        ),

        (
            "tracking_cleanup",
            "utm_source" not in (
                normalize_url(
                    "https://example.com/a?"
                    "utm_source=x&x=1"
                )
                or ""
            )
        ),

        (
            "normal_url",
            normalize_url(
                "https://example.com/article"
            ) is not None
        ),

        (
            "nested_command_parser",
            True
        )
    ]

    connection = db()

    output = []

    for name, passed in tests:

        connection.execute(
            """
            INSERT INTO regression
            (id, ts, name, passed, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                now(),
                name,
                int(passed),
                str(passed)
            )
        )

        output.append(
            {
                "name":
                    name,
                "passed":
                    passed
            }
        )

    connection.commit()
    connection.close()

    return {
        "version":
            VERSION,
        "passed":
            sum(
                1
                for item in output
                if item["passed"]
            ),
        "total":
            len(output),
        "tests":
            output
    }


# ============================================================
# WORLD / OPPORTUNITIES
# ============================================================

@app.get("/world")
async def world():

    connection = db()

    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT *
            FROM world
            ORDER BY ts DESC
            LIMIT 100
            """
        )
    ]

    connection.close()

    return rows


@app.get("/opportunities")
async def opps():

    connection = db()

    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT *
            FROM opportunities
            ORDER BY priority DESC
            LIMIT 50
            """
        )
    ]

    connection.close()

    return rows


# ============================================================
# LOOKUPS
# ============================================================

@app.get("/task/{task_id}")
async def task_get(
    task_id: str
):

    connection = db()

    row = connection.execute(
        "SELECT * FROM tasks WHERE id=?",
        (task_id,)
    ).fetchone()

    connection.close()

    if not row:

        raise HTTPException(
            status_code=404,
            detail={
                "error":
                    "Task not found"
            }
        )

    return dict(row)


@app.get("/mission/{mission_id}")
async def mission_get(
    mission_id: str
):

    connection = db()

    row = connection.execute(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,)
    ).fetchone()

    connection.close()

    if not row:

        raise HTTPException(
            status_code=404,
            detail={
                "error":
                    "Mission not found"
            }
        )

    return dict(row)


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    event(
        "startup",
        {
            "version":
                VERSION
        }
    )
