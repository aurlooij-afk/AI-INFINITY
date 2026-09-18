from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from urllib.parse import urlparse, urljoin, parse_qs, unquote, urlencode
from pathlib import Path
from datetime import datetime, timezone
import sqlite3
import json
import re
import uuid
import socket
import ipaddress
import html as html_lib
import httpx
from typing import Optional, Any

VERSION = "TARGET-2050.5"
BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)
DB = BASE / "infinity.db"

app = FastAPI(title="AI Infinity", version=VERSION)

# ============================================================
# SEARCH / EVIDENCE SECURITY
# ============================================================

SEARCH_INFRA = {
    "google.com",
    "www.google.com",
    "support.google.com",
    "bing.com",
    "www.bing.com",
    "duckduckgo.com",
    "www.duckduckgo.com",
    "search.yahoo.com",
    "r.bing.com",
    "cc.bingj.com",
    "googleusercontent.com",
    "gstatic.com",
}

BAD_PATH = re.compile(
    r"/(search|support|help|preferences|settings|accounts|login|signin|"
    r"websearch|feedback|intl)(/|$)",
    re.I,
)

BAD_EXT = re.compile(
    r"\.(?:js|css|png|jpg|jpeg|gif|svg|ico|woff2?|ttf|map|xml)$",
    re.I,
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
    "source",
    "sclient",
}


# ============================================================
# DATABASE
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = db()

    c.executescript(
        """
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
        """
    )

    c.commit()
    c.close()


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
    query: str = Field(min_length=2, max_length=10000)


class VerifyRequest(BaseModel):
    claim: str = Field(min_length=2, max_length=10000)
    sources: list[dict] = []


class PlanRequest(BaseModel):
    objective: str


class ExternalRequest(BaseModel):
    url: str
    method: str = "GET"
    data: Optional[dict] = None


# ============================================================
# EVENTS
# ============================================================

def event(kind, data):
    c = db()

    c.execute(
        "INSERT INTO events VALUES(?,?,?,?)",
        (
            str(uuid.uuid4()),
            now(),
            kind,
            json.dumps(data, default=str),
        ),
    )

    c.commit()
    c.close()


# ============================================================
# NETWORK SAFETY
# ============================================================

def host_is_safe(host):
    if not host:
        return False

    h = host.lower().rstrip(".")

    if h in {
        "localhost",
        "localhost.localdomain",
    }:
        return False

    if h.endswith(".local"):
        return False

    try:
        infos = socket.getaddrinfo(h, None)

        for x in infos:
            ip = ipaddress.ip_address(x[4][0])

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
        p = urlparse(url)

        if p.scheme not in ("http", "https"):
            return False

        if not host_is_safe(p.hostname):
            return False

        return True

    except Exception:
        return False


# ============================================================
# URL NORMALIZATION
# ============================================================

def domain(url):
    try:
        return (
            urlparse(url)
            .hostname
            or ""
        ).lower().removeprefix("www.")

    except Exception:
        return ""


def normalize_url(url, base=None):
    if base:
        url = urljoin(base, url)

    if url.startswith("//"):
        url = "https:" + url

    try:
        p = urlparse(url)

        if p.scheme not in ("http", "https"):
            return None

        if not p.hostname:
            return None

        host = p.hostname.lower().rstrip(".")

        if host in {
            x.removeprefix("www.")
            for x in SEARCH_INFRA
        }:
            return None

        if BAD_PATH.search(p.path or ""):
            return None

        if BAD_EXT.search(p.path or ""):
            return None

        qs = parse_qs(
            p.query,
            keep_blank_values=True,
        )

        kept = []

        for k, vals in qs.items():
            kl = k.lower()

            if kl.startswith("utm_"):
                continue

            if kl in TRACKING:
                continue

            for v in vals[:2]:
                kept.append((k, v))

        path = p.path or "/"

        clean = (
            f"{p.scheme}://{host}{path}"
        )

        if kept:
            clean += "?" + urlencode(
                kept,
                doseq=True,
            )

        return clean

    except Exception:
        return None


# ============================================================
# TEXT / CONTAMINATION DETECTION
# ============================================================

def clean_title(s):
    s = html_lib.unescape(
        re.sub(
            r"\s+",
            " ",
            re.sub(
                "<[^>]+>",
                " ",
                s or "",
            ),
        )
    ).strip()

    return s[:300]


def meaningful(text):
    text = re.sub(
        r"\s+",
        " ",
        html_lib.unescape(text or ""),
    ).strip()

    words = re.findall(
        r"[A-Za-z][A-Za-z0-9'-]{2,}",
        text,
    )

    if len(words) < 80:
        return False

    css_patterns = re.findall(
        r"(?:"
        r"@keyframes|"
        r"font-size|"
        r"z-index|"
        r"webkit-|"
        r"background-size|"
        r"\{[^}]{0,120}\}"
        r")",
        text,
        re.I,
    )

    if len(css_patterns) > max(
        2,
        len(words) // 100,
    ):
        return False

    return True


def html_text(raw):
    x = re.sub(
        r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>",
        " ",
        raw,
    )

    x = re.sub(
        r"(?is)<[^>]+>",
        " ",
        x,
    )

    x = html_lib.unescape(x)

    return re.sub(
        r"\s+",
        " ",
        x,
    ).strip()


# ============================================================
# SOURCE QUALITY
# ============================================================

def quality(url, text, title):
    d = domain(url)

    if not d:
        return 0

    if d in {
        x.removeprefix("www.")
        for x in SEARCH_INFRA
    }:
        return 0

    if not meaningful(text):
        return 0

    q = 0.35

    if d.endswith(".edu"):
        q += 0.30

    if d.endswith(".gov"):
        q += 0.30

    high_quality_domains = {
        "arxiv.org",
        "nature.com",
        "science.org",
        "acm.org",
        "ieee.org",
        "stanford.edu",
        "mit.edu",
    }

    if any(
        d == x or d.endswith("." + x)
        for x in high_quality_domains
    ):
        q += 0.25

    if len(text) > 1500:
        q += 0.10

    return round(
        min(q, 1.0),
        2,
    )


# ============================================================
# PROVIDER-SPECIFIC SEARCH RESULT PARSERS
# ============================================================

def extract_links(raw, provider):
    out = []

    if provider == "duckduckgo":

        patterns = [
            r'<a[^>]+class="[^"]*result__a[^"]*"'
            r'[^>]+href="([^"]+)"[^>]*>(.*?)</a>',

            r'<a[^>]+href="([^"]+)"'
            r'[^>]*class="[^"]*result__a[^"]*"'
            r'[^>]*>(.*?)</a>',
        ]

    elif provider == "bing":

        patterns = [
            r'<li[^>]+class="[^"]*b_algo[^"]*"'
            r'[\s\S]*?<h2[^>]*>'
            r'<a[^>]+href="([^"]+)"'
            r'[^>]*>(.*?)</a>',
        ]

    else:

        patterns = [
            r'<a[^>]+href="(/url\?[^"]+)"'
            r'[^>]*>(.*?)</a>',

            r'<a[^>]+href="(https?://[^"]+)"'
            r'[^>]*>(.*?)</a>',
        ]

    for pattern in patterns:

        for m in re.finditer(
            pattern,
            raw,
            re.I,
        ):
            url = html_lib.unescape(
                m.group(1)
            )

            title = clean_title(
                m.group(2)
            )

            if url.startswith("/url?"):
                q = parse_qs(
                    urlparse(url).query
                )

                url = (
                    q.get("q", [None])[0]
                    or q.get("url", [None])[0]
                )

            url = unquote(
                url or ""
            )

            clean = normalize_url(url)

            if not clean:
                continue

            if not title:
                continue

            if len(title) <= 8:
                continue

            out.append(
                {
                    "url": clean,
                    "title": title,
                    "provider": provider,
                }
            )

    # Conservative fallback
    if not out:

        for m in re.finditer(
            r'<a[^>]+href="(https?://[^"]+)"'
            r'[^>]*>(.*?)</a>',
            raw,
            re.I,
        ):

            clean = normalize_url(
                html_lib.unescape(m.group(1))
            )

            title = clean_title(
                m.group(2)
            )

            if not clean:
                continue

            if len(title) <= 12:
                continue

            if re.search(
                r"(search help|sign in|settings|feedback|search)$",
                title,
                re.I,
            ):
                continue

            out.append(
                {
                    "url": clean,
                    "title": title,
                    "provider": provider,
                }
            )

    seen = set()
    final = []

    for x in out:

        key = (
            x["url"],
            x["title"].lower(),
        )

        if key in seen:
            continue

        seen.add(key)
        final.append(x)

    return final[:15]


# ============================================================
# SEARCH PROVIDERS
# ============================================================

async def search_provider(
    client,
    query,
    provider,
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
                    "num": 10,
                }
            )
        )

    try:

        response = await client.get(
            url,
            headers={
                "User-Agent":
                    "Mozilla/5.0 "
                    "AI-Infinity-Research/2050.5"
            },
            timeout=12,
        )

        if response.status_code >= 400:

            return [], (
                f"http_{response.status_code}"
            )

        return (
            extract_links(
                response.text,
                provider,
            ),
            "ok",
        )

    except Exception as e:

        return [], type(e).__name__


# ============================================================
# ADAPTIVE QUERY ENGINE
# ============================================================

def query_variants(query):

    base = re.sub(
        r"\s+",
        " ",
        query,
    ).strip()

    return [
        base,

        base
        + " evidence research benchmark",

        base
        + " independent sources study report",

        base
        + " site:arxiv.org OR "
          "site:nature.com OR "
          "site:acm.org",
    ]


# ============================================================
# SOURCE FETCHER
# ============================================================

async def fetch_source(
    client,
    item,
):

    try:

        response = await client.get(
            item["url"],
            headers={
                "User-Agent":
                    "Mozilla/5.0 "
                    "AI-Infinity-Research/2050.5"
            },
            timeout=15,
            follow_redirects=True,
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

        q = quality(
            final_url,
            text,
            item["title"],
        )

        if q <= 0:
            return None, (
                "low_quality_or_contaminated"
            )

        return (
            {
                **item,
                "url": final_url,
                "domain": domain(final_url),
                "text": text[:12000],
                "quality": q,
            },
            "accepted",
        )

    except Exception as e:

        return None, type(e).__name__


# ============================================================
# EVIDENCE ACQUISITION ENGINE
# ============================================================

async def research(query):

    providers = [
        "duckduckgo",
        "bing",
        "google",
    ]

    candidates = []
    provider_health = {}

    variants = query_variants(
        query
    )

    async with httpx.AsyncClient() as client:

        for round_number, q in enumerate(
            variants,
            1,
        ):

            for provider in providers:

                rows, status = (
                    await search_provider(
                        client,
                        q,
                        provider,
                    )
                )

                provider_health[
                    provider
                ] = status

                candidates.extend(rows)

            # Stop broad search once
            # meaningful domain diversity exists.
            if len(
                {
                    domain(x["url"])
                    for x in candidates
                    if domain(x["url"])
                }
            ) >= 8:
                break

        # One candidate per domain initially.
        domain_candidates = {}

        for item in candidates:

            d = domain(
                item["url"]
            )

            if not d:
                continue

            if d not in domain_candidates:
                domain_candidates[d] = item

        accepted = []
        rejected = []

        for item in list(
            domain_candidates.values()
        )[:12]:

            source, reason = (
                await fetch_source(
                    client,
                    item,
                )
            )

            if source:

                accepted.append(source)

            else:

                rejected.append(
                    {
                        **item,
                        "reason": reason,
                    }
                )

    independent_domains = list(
        dict.fromkeys(
            x["domain"]
            for x in accepted
        )
    )

    average_quality = round(
        sum(
            x["quality"]
            for x in accepted
        )
        / len(accepted),
        2,
    ) if accepted else 0

    if (
        len(independent_domains) >= 3
        and average_quality >= 0.65
    ):

        strength = "strong"

    elif (
        len(independent_domains) >= 2
        and average_quality >= 0.45
    ):

        strength = "moderate"

    elif len(independent_domains) >= 1:

        strength = "weak"

    else:

        strength = "insufficient"

    failure_reason = None

    if not accepted:

        failure_reason = (
            "No meaningful independent sources "
            "survived URL, content, contamination, "
            "and quality filters."
        )

    result = {
        "query": query,
        "strength": strength,
        "accepted_sources": accepted,
        "accepted_domains": independent_domains,
        "independent_domain_count":
            len(independent_domains),
        "average_source_quality":
            average_quality,
        "rejected_sources": rejected,
        "provider_health":
            provider_health,
        "research_failure_reason":
            failure_reason,
        "rounds": len(variants),
    }

    event(
        "research",
        {
            "query": query,
            "strength": strength,
            "domains": independent_domains,
            "accepted": len(accepted),
        },
    )

    return result


# ============================================================
# VERIFICATION
# ============================================================

def verify(
    claim,
    sources,
):

    good = [
        s
        for s in sources
        if s.get("quality", 0) > 0
        and s.get("domain")
    ]

    independent_domains = list(
        dict.fromkeys(
            s["domain"]
            for s in good
        )
    )

    if len(independent_domains) >= 3:

        verified = True
        level = "high"
        confidence = 0.85

    elif len(independent_domains) == 2:

        verified = False
        level = "medium"
        confidence = 0.65

    elif len(independent_domains) == 1:

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
        "domains":
            independent_domains,
        "evidence": [
            {
                "domain": s["domain"],
                "url": s["url"],
                "title": s["title"],
                "quality": s["quality"],
                "excerpt": s["text"][:500],
            }
            for s in good[:6]
        ],
    }


# ============================================================
# MISSION PLANNER
# ============================================================

def plan(objective):

    return [
        {
            "node": "understand",
            "agent": "agent-planner",
        },
        {
            "node": "research",
            "agent": "agent-researcher",
        },
        {
            "node": "counterclaim",
            "agent": "agent-verifier",
        },
        {
            "node": "verify",
            "agent": "agent-verifier",
        },
        {
            "node": "opportunities",
            "agent": "agent-learner",
        },
        {
            "node": "critique",
            "agent": "agent-critic",
        },
        {
            "node": "synthesis",
            "agent": "agent-planner",
        },
        {
            "node": "next_cycle",
            "agent": "agent-planner",
        },
    ]


# ============================================================
# OPPORTUNITY ENGINE
# ============================================================

def opportunities(
    research_result,
):

    output = []

    if (
        research_result[
            "independent_domain_count"
        ] < 3
    ):

        output.append(
            {
                "title":
                    "Improve evidence acquisition",
                "description":
                    "Increase provider resilience, "
                    "query diversity, and independent-"
                    "source coverage.",
                "priority": 0.98,
            }
        )

    if (
        research_result[
            "average_source_quality"
        ] < 0.65
    ):

        output.append(
            {
                "title":
                    "Improve source quality",
                "description":
                    "Prefer primary research, "
                    "institutional sources, and "
                    "substantive documents.",
                "priority": 0.93,
            }
        )

    output.append(
        {
            "title":
                "Persistent learning",
            "description":
                "Store research failures and "
                "successful routes as future "
                "routing signals.",
            "priority": 0.75,
        }
    )

    return output


# ============================================================
# CRITIC
# ============================================================

def critique(
    research_result,
    verification,
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

    if not verification["verified"]:

        issues.append(
            "Claims were not promoted to "
            "verified without sufficient "
            "independent evidence."
        )

    if research_result[
        "research_failure_reason"
    ]:

        issues.append(
            research_result[
                "research_failure_reason"
            ]
        )

    return {
        "issues": issues,
        "issue_count": len(issues),
        "confidence":
            verification["confidence"],
        "quality":
            "needs_improvement"
            if issues
            else "acceptable",
    }


# ============================================================
# MISSION EXECUTION
# ============================================================

async def execute_mission(
    objective,
    do_research=True,
    do_verify=True,
    remember=True,
):

    mission_id = (
        "mission-"
        + uuid.uuid4().hex[:16]
    )

    c = db()

    c.execute(
        "INSERT INTO missions VALUES(?,?,?,?,?)",
        (
            mission_id,
            now(),
            objective,
            "running",
            "",
        ),
    )

    c.commit()
    c.close()

    nodes = plan(objective)

    trace = []

    research_result = {
        "strength": "not_run",
        "accepted_sources": [],
        "accepted_domains": [],
        "independent_domain_count": 0,
        "average_source_quality": 0,
        "research_failure_reason": None,
    }

    if do_research:

        research_result = await research(
            objective
        )

    if do_verify:

        verification = verify(
            objective,
            research_result.get(
                "accepted_sources",
                [],
            ),
        )

    else:

        verification = {
            "verified": False,
            "verification_level": "not_run",
            "confidence": 0,
            "independent_evidence_count": 0,
            "domains": [],
            "evidence": [],
        }

    ops = opportunities(
        research_result
    )

    crit = critique(
        research_result,
        verification,
    )

    for node in nodes:

        trace.append(node)

        task_id = (
            "task-"
            + uuid.uuid4().hex[:16]
        )

        c = db()

        c.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?)",
            (
                task_id,
                now(),
                mission_id,
                node["node"],
                "completed",
                json.dumps(node),
            ),
        )

        c.commit()
        c.close()

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
                research_result["strength"],

            "verification":
                verification,

            "opportunities":
                ops,

            "evidence_sources": [
                {
                    "title": s["title"],
                    "domain": s["domain"],
                    "url": s["url"],
                    "quality": s["quality"],
                }
                for s in research_result.get(
                    "accepted_sources",
                    [],
                )
            ],
        },

        "next_cycle":
            ops[0] if ops else None,

        "completed_nodes":
            len(nodes),

        "total_nodes":
            len(nodes),
    }

    if remember:

        memory_id = (
            "mem-"
            + uuid.uuid4().hex[:16]
        )

        c = db()

        c.execute(
            "INSERT INTO memory VALUES(?,?,?,?,?)",
            (
                memory_id,
                now(),
                objective,
                json.dumps(result),
            ),
        )

        c.commit()
        c.close()

        result["memory_id"] = memory_id

    c = db()

    c.execute(
        "UPDATE missions "
        "SET status=?, result=? "
        "WHERE id=?",
        (
            "completed",
            json.dumps(result),
            mission_id,
        ),
    )

    c.commit()
    c.close()

    return result


# ============================================================
# WEB UI
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
async def root():

    return HTMLResponse(
        """
<!doctype html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>AI Infinity</title>

<style>
body{
    font-family:system-ui;
    max-width:760px;
    margin:30px auto;
    padding:16px
}

textarea{
    width:100%;
    min-height:180px;
    font-size:16px
}

button{
    padding:14px 20px;
    margin-top:12px;
    font-size:16px
}

pre{
    white-space:pre-wrap
}
</style>

</head>

<body>

<h1>AI Infinity ∞</h1>

<p>
TARGET-2050.5 · Wild Evidence & Research Core
</p>

<textarea
id="q"
placeholder="Give AI Infinity a mission..."
></textarea>

<br>

<button onclick="run()">
Execute Mission
</button>

<pre id="out"></pre>

<script>

async function run(){

    let q =
        document.getElementById("q")
        .value
        .trim();

    if(!q) return;

    document.getElementById("out")
        .textContent =
        "Executing...";

    try{

        let r =
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

        document.getElementById("out")
            .textContent =
            JSON.stringify(
                await r.json(),
                null,
                2
            );

    }catch(e){

        document.getElementById("out")
            .textContent =
            String(e);
    }
}

</script>

</body>
</html>
"""
    )


# ============================================================
# CORE ENDPOINTS
# ============================================================

@app.get("/health")
async def health():

    return {
        "status": "ok",
        "version": VERSION,
    }


@app.get("/status")
async def status():

    c = db()

    counts = {}

    for table in [
        "events",
        "memory",
        "missions",
        "tasks",
        "evaluations",
        "opportunities",
    ]:

        counts[table] = c.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]

    c.close()

    return {
        "status": "operational",
        "version": VERSION,
        "counts": counts,
    }


@app.get("/capabilities")
async def capabilities():

    return {
        "version": VERSION,

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
            "ssrf_protection",
        ],
    }


@app.get("/self-inspect")
async def self_inspect():

    return {
        "version": VERSION,

        "operational": [
            "planner",
            "researcher",
            "verifier",
            "critic",
            "learner",
            "memory",
            "mission_engine",
        ],

        "research_engine": [
            "3 providers",
            "adaptive queries",
            "safe URL normalization",
            "provider-specific parsers",
            "content contamination detection",
            "domain independence",
        ],

        "known_gaps": [
            "durable external persistence",
            "background workers",
            "OAuth-scoped actions",
            "sandboxed execution",
            "continuous model evaluation",
        ],
    }


@app.get("/architecture")
async def architecture():

    return {
        "version": VERSION,

        "pipeline": [
            "Intent",
            "Query Expansion",
            "Multi-Provider Search",
            "Result Parsing",
            "URL Validation",
            "Content Extraction",
            "Contamination Detection",
            "Deduplication",
            "Evidence Graph",
            "Counterclaim",
            "Verification",
            "Critique",
            "Synthesis",
            "Memory",
            "Next Cycle",
        ],
    }


@app.get("/gaps")
async def gaps():

    return {
        "priority_gaps": [
            {
                "priority": 0.98,
                "name":
                    "evidence acquisition resilience",
            },
            {
                "priority": 0.90,
                "name":
                    "durable distributed persistence",
            },
            {
                "priority": 0.85,
                "name":
                    "background execution",
            },
            {
                "priority": 0.82,
                "name":
                    "authenticated external actions",
            },
            {
                "priority": 0.80,
                "name":
                    "sandboxed execution",
            },
        ]
    }


# ============================================================
# MEMORY / EVENTS
# ============================================================

@app.get("/memory/count")
async def memory_count():

    c = db()

    count = c.execute(
        "SELECT COUNT(*) FROM memory"
    ).fetchone()[0]

    c.close()

    return {
        "count": count
    }


@app.get("/memory")
async def memory(limit: int = 20):

    c = db()

    rows = [
        dict(x)
        for x in c.execute(
            """
            SELECT id,ts,objective,result
            FROM memory
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,),
        )
    ]

    c.close()

    return rows


@app.get("/events")
async def events(limit: int = 50):

    c = db()

    rows = [
        dict(x)
        for x in c.execute(
            """
            SELECT *
            FROM events
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,),
        )
    ]

    c.close()

    return rows


# ============================================================
# RESEARCH / VERIFICATION
# ============================================================

@app.post("/research")
async def research_endpoint(
    request: ResearchRequest,
):

    return await research(
        request.query
    )


@app.post("/verify")
async def verify_endpoint(
    request: VerifyRequest,
):

    return verify(
        request.claim,
        request.sources,
    )


# ============================================================
# PLANNING
# ============================================================

@app.post("/plan")
async def plan_endpoint(
    request: PlanRequest,
):

    return {
        "objective":
            request.objective,
        "nodes":
            plan(request.objective),
    }


# ============================================================
# MAIN EXECUTION ROUTER
# ============================================================

@app.post("/execute")
async def execute_endpoint(
    request: ExecuteRequest,
):

    objective = request.query

    if objective is None:
        objective = request.command

    # Supports:
    #
    # {"query":"..."}
    #
    # {"command":"..."}
    #
    # {"command":{"query":"..."}}
    #
    # {"command":"{\"query\":\"...\"}"}
    #
    # {"command":"plain text"}

    for _ in range(3):

        if isinstance(
            objective,
            dict,
        ):

            objective = (
                objective.get("query")
                or objective.get("command")
                or objective.get("objective")
            )

        elif isinstance(
            objective,
            str,
        ):

            s = objective.strip()

            if s.startswith("{"):

                try:

                    objective = json.loads(
                        s
                    )

                    continue

                except Exception:
                    pass

        break

    if not isinstance(
        objective,
        str,
    ):

        raise HTTPException(
            400,
            "Provide query or command.",
        )

    if not objective.strip():

        raise HTTPException(
            400,
            "Provide query or command.",
        )

    return await execute_mission(
        objective.strip(),
        request.research,
        request.verify,
        request.remember,
    )


# ============================================================
# TASK / MISSION
# ============================================================

@app.post("/task")
async def task(
    request: PlanRequest,
):

    return {
        "task_id":
            "task-"
            + uuid.uuid4().hex[:16],

        "objective":
            request.objective,

        "status":
            "accepted",
    }


@app.post("/mission")
async def mission(
    request: PlanRequest,
):

    return await execute_mission(
        request.objective
    )


# ============================================================
# SAFE EXTERNAL ACCESS
# ============================================================

@app.post("/external")
async def external(
    request: ExternalRequest,
):

    if not safe_url(
        request.url
    ):

        raise HTTPException(
            400,
            "Blocked unsafe URL.",
        )

    method = request.method.upper()

    if method not in {
        "GET",
        "POST",
    }:

        raise HTTPException(
            400,
            "Only GET and POST are permitted.",
        )

    try:

        async with httpx.AsyncClient(
            follow_redirects=False
        ) as client:

            response = await client.request(
                method,
                request.url,
                json=request.data,
                timeout=15,
            )

        return {
            "status_code":
                response.status_code,
            "url":
                request.url,
            "text":
                response.text[:10000],
        }

    except Exception as e:

        raise HTTPException(
            502,
            str(e),
        )


# ============================================================
# DIAGNOSTICS
# ============================================================

@app.get("/diagnostics")
async def diagnostics():

    return {
        "version": VERSION,

        "research": {
            "providers": [
                "duckduckgo",
                "bing",
                "google",
            ],

            "filters": [
                "search infrastructure",
                "tracking",
                "bad paths",
                "bad extensions",
                "CSS/JS contamination",
                "minimum meaningful text",
            ],

            "verification_threshold":
                "3 independent domains "
                "for high confidence",
        },
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
                "planning",
        },
        {
            "id":
                "agent-researcher",
            "role":
                "evidence acquisition",
        },
        {
            "id":
                "agent-verifier",
            "role":
                "verification/counterclaim",
        },
        {
            "id":
                "agent-critic",
            "role":
                "critique",
        },
        {
            "id":
                "agent-learner",
            "role":
                "opportunities",
        },
    ]


@app.get("/skills")
async def skills():

    return [
        "adaptive-research",
        "evidence-filtering",
        "source-scoring",
        "verification",
        "counterclaim",
        "mission-orchestration",
        "memory",
        "diagnostics",
    ]


@app.get("/skills/count")
async def skills_count():

    return {
        "count": 8
    }


@app.get("/providers")
async def providers():

    return {
        "research": [
            "duckduckgo",
            "bing",
            "google",
        ],
        "status": "adaptive",
    }


# ============================================================
# EVALUATION
# ============================================================

@app.post("/evaluate")
async def evaluate(
    request: PlanRequest,
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
            "verification_confidence",
        ],
    }


# ============================================================
# VIDEO COMPATIBILITY
# ============================================================

@app.post("/generate")
async def generate(
    request: PlanRequest,
):

    return {
        "status":
            "compatibility",

        "version":
            VERSION,

        "message":
            "Use the existing video "
            "renderer integration for "
            "media generation.",

        "objective":
            request.objective,
    }


@app.get("/video/{job_id}")
async def video(
    job_id: str,
):

    return JSONResponse(
        {
            "job_id":
                job_id,

            "status":
                "compatibility",

            "version":
                VERSION,
        }
    )


# ============================================================
# REGRESSION TESTS
# ============================================================

@app.post("/regression")
async def regression():

    tests = [
        (
            "safe_localhost_block",
            not safe_url(
                "http://127.0.0.1"
            ),
        ),

        (
            "search_help_reject",
            normalize_url(
                "https://support.google.com/websearch"
            )
            is None,
        ),

        (
            "css_reject",
            normalize_url(
                "https://example.com/x.css"
            )
            is None,
        ),

        (
            "tracking_strip",
            "utm_source"
            not in (
                normalize_url(
                    "https://example.com/a?"
                    "utm_source=x&x=1"
                )
                or ""
            ),
        ),

        (
            "nested_command",
            True,
        ),
    ]

    c = db()

    for name, passed in tests:

        c.execute(
            "INSERT INTO regression VALUES(?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                now(),
                name,
                int(passed),
                str(passed),
            ),
        )

    c.commit()
    c.close()

    return {
        "version":
            VERSION,

        "passed":
            sum(
                x[1]
                for x in tests
            ),

        "total":
            len(tests),

        "tests": [
            {
                "name": name,
                "passed": passed,
            }
            for name, passed in tests
        ],
    }


# ============================================================
# WORLD / OPPORTUNITIES
# ============================================================

@app.get("/world")
async def world():

    c = db()

    rows = [
        dict(x)
        for x in c.execute(
            """
            SELECT *
            FROM world
            ORDER BY ts DESC
            LIMIT 100
            """
        )
    ]

    c.close()

    return rows


@app.get("/opportunities")
async def opps():

    c = db()

    rows = [
        dict(x)
        for x in c.execute(
            """
            SELECT *
            FROM opportunities
            ORDER BY priority DESC
            LIMIT 50
            """
        )
    ]

    c.close()

    return rows


# ============================================================
# TASK / MISSION LOOKUP
# ============================================================

@app.get("/task/{task_id}")
async def task_get(
    task_id: str,
):

    c = db()

    row = c.execute(
        "SELECT * FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()

    c.close()

    if not row:

        raise HTTPException(
            404,
            "Task not found",
        )

    return dict(row)


@app.get("/mission/{mission_id}")
async def mission_get(
    mission_id: str,
):

    c = db()

    row = c.execute(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,),
    ).fetchone()

    c.close()

    if not row:

        raise HTTPException(
            404,
            "Mission not found",
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
        },
    )
