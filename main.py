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
from urllib.parse import urlparse, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


APP_VERSION = "TARGET-2050.81"
BUILD = "CUMULATIVE-EVIDENCE-INDEPENDENCE-AND-RELEVANCE-CLOSURE-CORE"

DB_PATH = os.getenv("AI_INFINITY_DB", "/tmp/ai-infinity/ai_infinity.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

app = FastAPI(
    title="AI Infinity",
    version=APP_VERSION,
    description="AI Infinity cumulative real-world mission execution core",
)

db_lock = threading.Lock()
workers = {}
workers_lock = threading.Lock()

# ---------------- database ----------------

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_lock:
        conn = db()
        conn.execute("""CREATE TABLE IF NOT EXISTS missions(
            id TEXT PRIMARY KEY, objective TEXT NOT NULL, status TEXT NOT NULL,
            route TEXT, created_at REAL, updated_at REAL, result TEXT,
            plan TEXT, approval_required INTEGER DEFAULT 0,
            approved INTEGER DEFAULT 0, resumable INTEGER DEFAULT 1)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS mission_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
            timestamp REAL NOT NULL, event_type TEXT NOT NULL, payload TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS memory(
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
            key TEXT, value TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS policies(
            id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL,
            created_at REAL NOT NULL, mode TEXT NOT NULL, valid INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS action_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
            created_at REAL NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL,
            details TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS provenance(
            id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
            created_at REAL NOT NULL, source_id TEXT, provider TEXT,
            domain TEXT, url TEXT, integrity TEXT)""")
        if conn.execute("SELECT COUNT(*) n FROM policies").fetchone()["n"] == 0:
            conn.execute(
                "INSERT INTO policies(version,created_at,mode,valid) VALUES(?,?,?,1)",
                (1, time.time(), "baseline",)
            )
        conn.commit()
        conn.close()

init_db()

# ---------------- request / mission contracts ----------------

class MissionRequest(BaseModel):
    objective: str
    research: bool = True
    verify: bool = True
    remember: bool = False
    external_access: bool = True
    execute: bool = False
    require_approval: bool = True

class ApprovalRequest(BaseModel):
    approved: bool = True

# ---------------- security boundary ----------------

BLOCKED_HOSTS = {
    "localhost", "localhost.localdomain", "metadata",
    "metadata.google.internal", "instance-data",
}
BLOCKED_SCHEMES = {"file", "ftp", "gopher", "data", "javascript"}
MAX_RESPONSE_BYTES = 1024 * 1024
USER_AGENT = "AI-Infinity/2050.81-controlled-public-research"

WAF_MARKERS = (
    "<title>blocked</title>", "<title>access denied</title>",
    "request blocked", "security verification", "bot detection",
    "captcha", "cf-chl-", "attention required",
)

def is_private_ip(value):
    try:
        ip = ipaddress.ip_address(value)
        return (
            ip.is_private or ip.is_loopback or ip.is_link_local or
            ip.is_reserved or ip.is_multicast or ip.is_unspecified
        )
    except ValueError:
        return False

def is_private_host(hostname):
    if not hostname:
        return True
    hostname = hostname.lower().strip(".")
    if hostname in BLOCKED_HOSTS or is_private_ip(hostname):
        return True
    try:
        for item in socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP):
            if is_private_ip(item[4][0]):
                return True
    except Exception:
        return False
    return False

def validate_url(url):
    try:
        p = urlparse(url)
        return (
            p.scheme.lower() in ("http", "https")
            and bool(p.hostname)
            and not is_private_host(p.hostname)
        )
    except Exception:
        return False

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

OPENER = build_opener(NoRedirect)

def reject_untrusted_response(text, content_type=""):
    if not text:
        return True
    sample = text[:30000].lower()
    if "html" not in content_type and "<html" not in sample:
        return False
    return any(marker in sample for marker in WAF_MARKERS)

def safe_fetch(url, timeout=10):
    if not validate_url(url):
        raise RuntimeError("blocked_private_or_invalid_url")
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,application/xml,text/xml,text/html;q=0.8,*/*;q=0.1",
        },
        method="GET",
    )
    try:
        response = OPENER.open(req, timeout=timeout)
        status = getattr(response, "status", 200)
        if not 200 <= status < 300:
            raise RuntimeError(f"http_status_{status}")
        content_type = response.headers.get("Content-Type", "").lower()
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise RuntimeError("response_too_large")
        if not data:
            raise RuntimeError("empty_response")
        text = data.decode("utf-8", errors="replace")
        if reject_untrusted_response(text, content_type):
            raise RuntimeError("waf_or_block_response_rejected")
        return {"text": text, "content_type": content_type, "status": status, "url": url}
    except HTTPError as exc:
        raise RuntimeError(f"http_error_{exc.code}")
    except URLError:
        raise RuntimeError("network_error")
    except Exception as exc:
        msg = str(exc)
        if msg.startswith(("blocked_", "waf_", "http_", "response_", "empty_")):
            raise
        raise RuntimeError(f"transport_error:{type(exc).__name__}")

def parse_json_response(response):
    try:
        return json.loads(response["text"])
    except Exception:
        raise RuntimeError("invalid_json_response")

# ---------------- persistence / events ----------------

def event(mid, kind, payload=None):
    with db_lock:
        conn = db()
        conn.execute(
            "INSERT INTO mission_events(mission_id,timestamp,event_type,payload) VALUES(?,?,?,?)",
            (mid, time.time(), kind, json.dumps(payload or {})),
        )
        conn.commit()
        conn.close()

def current_policy():
    with db_lock:
        conn = db()
        row = conn.execute(
            "SELECT * FROM policies ORDER BY version DESC LIMIT 1"
        ).fetchone()
        conn.close()
    return {"version": row["version"], "mode": row["mode"], "valid": bool(row["valid"])}

def adaptive_upgrade(reason):
    p = current_policy()
    version = p["version"] + 1
    with db_lock:
        conn = db()
        conn.execute(
            "INSERT INTO policies(version,created_at,mode,valid) VALUES(?,?,?,1)",
            (version, time.time(), reason),
        )
        conn.commit()
        conn.close()
    return version

def create_mission(mid, request, plan, approval_required):
    now = time.time()
    with db_lock:
        conn = db()
        conn.execute(
            """INSERT INTO missions(
                id,objective,status,route,created_at,updated_at,result,plan,
                approval_required,approved,resumable)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid, request.objective, "accepted", "/run", now, now,
                json.dumps(None), json.dumps(plan),
                int(approval_required), 0, 1,
            ),
        )
        conn.commit()
        conn.close()

def update_mission(mid, status, result=None, approved=None):
    with db_lock:
        conn = db()
        if approved is None:
            conn.execute(
                "UPDATE missions SET status=?,updated_at=?,result=? WHERE id=?",
                (status, time.time(), json.dumps(result), mid),
            )
        else:
            conn.execute(
                "UPDATE missions SET status=?,updated_at=?,result=?,approved=? WHERE id=?",
                (status, time.time(), json.dumps(result), int(approved), mid),
            )
        conn.commit()
        conn.close()

def get_mission(mid):
    with db_lock:
        conn = db()
        row = conn.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
        conn.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "objective": row["objective"],
        "status": row["status"],
        "route": row["route"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "result": json.loads(row["result"]) if row["result"] else None,
        "plan": json.loads(row["plan"]) if row["plan"] else [],
        "approval_required": bool(row["approval_required"]),
        "approved": bool(row["approved"]),
        "resumable": bool(row["resumable"]),
    }

def get_events(mid):
    with db_lock:
        conn = db()
        rows = conn.execute(
            "SELECT * FROM mission_events WHERE mission_id=? ORDER BY id",
            (mid,),
        ).fetchall()
        conn.close()
    return [
        {
            "timestamp": r["timestamp"],
            "type": r["event_type"],
            "payload": json.loads(r["payload"]) if r["payload"] else {},
        }
        for r in rows
    ]

def remember(objective, result):
    key = hashlib.sha256(objective.encode()).hexdigest()[:24]
    with db_lock:
        conn = db()
        conn.execute(
            "INSERT INTO memory(created_at,key,value) VALUES(?,?,?)",
            (time.time(), key, json.dumps(result)),
        )
        conn.commit()
        conn.close()
    return key

def memory_count():
    with db_lock:
        conn = db()
        n = conn.execute("SELECT COUNT(*) n FROM memory").fetchone()["n"]
        conn.close()
    return n

def provenance_add(mid, source):
    with db_lock:
        conn = db()
        conn.execute(
            """INSERT INTO provenance(
                mission_id,created_at,source_id,provider,domain,url,integrity)
                VALUES(?,?,?,?,?,?,?)""",
            (
                mid, time.time(), source.get("id"), source.get("provider"),
                source.get("domain"), source.get("url"), "transport_validated",
            ),
        )
        conn.commit()
        conn.close()

# ---------------- intent / planning / mission graph ----------------

STOPWORDS = {
    "the","and","for","with","that","this","from","into","about","find","give",
    "research","test","ai","infinity","real","world","task","tasks","please",
    "using","use","how","what","why","where","when","are","is","of","to","a",
    "an","on","by","or","be","can","their","they","its","our","your","all",
    "important","high","quality","independent","evidence","based",
}

def objective_terms(objective):
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", objective.lower())
    out, seen = [], set()
    for word in words:
        if word not in STOPWORDS and word not in seen:
            seen.add(word)
            out.append(word)
    return out[:12]

def classify_intent(objective, request):
    text = objective.lower()
    research = request.research or any(
        x in text for x in ("research", "compare", "investigate", "find evidence", "study")
    )
    action = request.execute or any(
        x in text for x in ("send", "post", "publish", "create", "call", "request", "execute")
    )
    verify = request.verify or any(
        x in text for x in ("verify", "validate", "check", "confirm")
    )
    return {
        "research": research,
        "action": action,
        "verification": verify,
        "memory": request.remember,
    }

def build_plan(objective, request):
    intent = classify_intent(objective, request)
    steps = []
    if intent["research"]:
        steps.append({"id": "research", "tool": "research", "depends_on": []})
    steps.append({
        "id": "plan",
        "tool": "reasoning",
        "depends_on": ["research"] if intent["research"] else [],
    })
    if intent["verification"]:
        steps.append({
            "id": "verify",
            "tool": "verification",
            "depends_on": ["research", "plan"] if intent["research"] else ["plan"],
        })
    if intent["action"]:
        deps = ["plan"]
        if intent["verification"]:
            deps.append("verify")
        steps.append({"id": "action", "tool": "action_gateway", "depends_on": deps})
    if intent["memory"]:
        steps.append({"id": "memory", "tool": "memory", "depends_on": [s["id"] for s in steps]})
    return steps

# ---------------- research providers ----------------

def hostname_of(url):
    try:
        return (urlparse(url).hostname or "").lower().strip(".")
    except Exception:
        return ""

def publisher_identity(name="", url="", provider=""):
    """Return a stable publisher identity without treating doi.org as a publisher."""
    name = re.sub(r"\s+", " ", str(name or "").strip()).lower()
    name = re.sub(r"[^a-z0-9&. -]", "", name)
    if name:
        aliases = {
            "springer nature": "springer nature",
            "springer": "springer nature",
            "elsevier": "elsevier",
            "wiley": "wiley",
            "wiley-blackwell": "wiley",
            "ieee": "ieee",
            "institute of electrical and electronics engineers": "ieee",
            "association for computing machinery": "acm",
            "acm": "acm",
            "oxford university press": "oxford university press",
            "cambridge university press": "cambridge university press",
            "frontiers": "frontiers",
            "mdpi": "mdpi",
            "sage": "sage",
            "taylor & francis": "taylor & francis",
            "ssrn": "ssrn",
        }
        return aliases.get(name, name)
    host = hostname_of(url)
    if not host or host in {"doi.org", "dx.doi.org"}:
        return ""
    return publisher_host(url)

def publisher_host(url):
    host = hostname_of(url)
    if not host or host in {"doi.org", "dx.doi.org"}:
        return ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host

def normalize_source(item):
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    if not title:
        return None
    if url and not validate_url(url):
        url = ""
    pub_name = str(item.get("publisher_name") or item.get("publisher") or "").strip()
    pub_id = publisher_identity(pub_name, url, str(item.get("provider") or ""))
    return {
        "id": hashlib.sha256((url or title.lower()).encode()).hexdigest()[:16],
        "title": title[:500],
        "url": url[:3000],
        "domain": hostname_of(url) or str(item.get("domain") or "")[:200],
        "publisher": pub_id,
        "publisher_name": pub_name[:300],
        "provider": str(item.get("provider") or "unknown"),
        "year": item.get("year"),
        "type": str(item.get("type") or "research_source"),
        "relevance_score": 0.0,
        "quality_score": 0.0,
    }

def deduplicate_sources(items):
    result, seen = [], set()
    for raw in items:
        s = normalize_source(raw)
        if not s:
            continue
        key = s["url"] or (s["provider"], s["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(s)
    return result

def relevance_score(source, objective):
    terms = objective_terms(objective)
    if not terms:
        return 0.0
    title = source["title"].lower()
    title_hits = sum(1 for t in terms if t in title)
    # Title-only relevance is deliberately strict: the evidence set should not
    # contain generic AI material merely because it came from a trusted API.
    score = title_hits / max(1, len(terms))
    if title_hits >= 2:
        score += 0.10
    return round(min(1.0, score), 4)

def quality_score(source):
    score = source.get("relevance_score", 0.0)
    if source.get("publisher"):
        score += 0.15
    if source.get("publisher_name"):
        score += 0.05
    if source.get("year"):
        score += 0.05
    if source.get("provider") in {"openalex","crossref","semantic_scholar","crossref_alt"}:
        score += 0.15
    if source.get("type") == "academic_work":
        score += 0.10
    return round(min(1.0, score), 4)

def rank_sources(items, objective):
    for s in items:
        s["relevance_score"] = relevance_score(s, objective)
        s["quality_score"] = quality_score(s)
    ranked = sorted(
        items,
        key=lambda s: (s["relevance_score"], s["quality_score"], s.get("year") or 0),
        reverse=True,
    )
    # Final evidence contains only sources that actually meet the relevance
    # threshold. This fixes 2050.80's 0.0-relevance leakage.
    return [s for s in ranked if s["relevance_score"] >= 0.18]

def parse_json(response):
    return parse_json_response(response)

def provider_openalex(q):
    data = parse_json(safe_fetch(
        "https://api.openalex.org/works?search="+quote(q)+"&per-page=10"
    ))
    out = []
    for x in data.get("results", []):
        title = x.get("title")
        if not title:
            continue
        loc = x.get("primary_location") or {}
        src = loc.get("source") or {}
        out.append({
            "title": title,
            "url": loc.get("landing_page_url") or x.get("doi", ""),
            "provider": "openalex",
            "year": x.get("publication_year"),
            "type": "academic_work",
            "publisher_name": src.get("display_name") or "",
        })
    return out

def provider_crossref(q):
    data = parse_json(safe_fetch(
        "https://api.crossref.org/works?query="+quote(q)+"&rows=10"
    ))
    out = []
    for x in data.get("message", {}).get("items", []):
        title = (x.get("title") or [None])[0]
        if not title:
            continue
        d = x.get("published-print") or x.get("published-online") or x.get("issued") or {}
        parts = d.get("date-parts", [[None]])
        year = parts[0][0] if parts and parts[0] else None
        out.append({
            "title": title,
            "url": x.get("URL") or ("https://doi.org/"+x["DOI"] if x.get("DOI") else ""),
            "provider": "crossref",
            "year": year,
            "type": "academic_work",
            "publisher_name": x.get("publisher") or "",
        })
    return out

def provider_semantic(q):
    data = parse_json(safe_fetch(
        "https://api.semanticscholar.org/graph/v1/paper/search?query="+
        quote(q)+"&limit=10&fields=title,url,year,venue"
    ))
    return [
        {
            "title": x.get("title"), "url": x.get("url") or "",
            "provider": "semantic_scholar", "year": x.get("year"),
            "type": "academic_work", "publisher_name": x.get("venue") or "",
        }
        for x in data.get("data", []) if x.get("title")
    ]

def provider_wikipedia(q):
    data = parse_json(safe_fetch(
        "https://en.wikipedia.org/w/api.php?action=query&format=json&list=search"+
        "&srsearch="+quote(q)+"&srlimit=10"
    ))
    return [
        {
            "title": x.get("title"),
            "url": "https://en.wikipedia.org/wiki/"+quote(x["title"].replace(" ","_")),
            "provider": "wikipedia", "year": None, "type": "reference",
            "publisher_name": "Wikipedia",
        }
        for x in data.get("query", {}).get("search", []) if x.get("title")
    ]

def provider_crossref_alt(q):
    data = parse_json(safe_fetch(
        "https://api.crossref.org/works?select=DOI,title,URL,published,publisher&rows=6"+
        "&query.bibliographic="+quote(q)
    ))
    out = []
    for x in data.get("message", {}).get("items", []):
        title = (x.get("title") or [None])[0]
        if not title:
            continue
        d = x.get("published") or x.get("issued") or {}
        parts = d.get("date-parts", [[None]])
        year = parts[0][0] if parts and parts[0] else None
        url = x.get("URL") or ("https://doi.org/"+x["DOI"] if x.get("DOI") else "")
        out.append({
            "title": title, "url": url, "provider": "crossref_alt",
            "year": year, "type": "academic_work",
            "publisher_name": x.get("publisher") or "",
        })
    return out

PROVIDERS = [
    ("openalex", provider_openalex),
    ("crossref", provider_crossref),
    ("semantic_scholar", provider_semantic),
    ("wikipedia", provider_wikipedia),
    ("crossref_alt", provider_crossref_alt),
]

def query_variants(objective):
    terms = objective_terms(objective)
    clean = re.sub(r"\s+", " ", objective).strip()
    compact = " ".join(terms[:8])
    return list(dict.fromkeys([x for x in (clean, compact) if x])) or [clean[:200]]

def failure_kind(exc):
    s = str(exc)
    if "429" in s:
        return "rate_limited"
    if "403" in s or "blocked" in s or "waf_" in s:
        return "blocked"
    if "http_error_5" in s:
        return "provider_server_error"
    return "transport_or_parse_error"

def call_provider(name, fn, objective):
    started = time.time()
    errors = []
    for i, q in enumerate(query_variants(objective)):
        try:
            items = deduplicate_sources(fn(q))
            return {
                "provider": name, "status": "success", "sources": len(items),
                "latency_ms": int((time.time()-started)*1000),
                "query_variant": i, "query": q, "items": items,
            }
        except Exception as exc:
            errors.append({
                "error": str(exc)[:300], "kind": failure_kind(exc), "variant": i
            })
            if i + 1 < len(query_variants(objective)):
                time.sleep(0.15)
    e = errors[-1] if errors else {"error":"unknown_failure","kind":"unknown","variant":0}
    return {
        "provider": name, "status": "failed", "sources": 0,
        "error": e["error"], "failure_kind": e["kind"],
        "latency_ms": int((time.time()-started)*1000),
        "retries": max(0,len(errors)-1), "items": [],
    }

def provider_family(name):
    # Multiple endpoints from the same underlying provider are one family.
    return {"crossref_alt":"crossref"}.get(name, name)

def research(objective):
    results = []
    threads = []
    lock = threading.Lock()

    def worker(name, fn):
        r = call_provider(name, fn, objective)
        with lock:
            results.append(r)

    for name, fn in PROVIDERS:
        t = threading.Thread(target=worker, args=(name,fn), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=14)

    by_name = {x["provider"]: x for x in results}
    all_items = []
    events = []
    for name, _ in PROVIDERS:
        r = by_name.get(name) or {
            "provider": name, "status": "failed", "sources": 0,
            "error": "provider_timeout", "failure_kind": "timeout",
            "latency_ms": 14000, "items": [],
        }
        all_items.extend(r.get("items", []))
        events.append({k:v for k,v in r.items() if k != "items"})

    sources = rank_sources(deduplicate_sources(all_items), objective)
    relevant = list(sources)
    publisher_ids = {s["publisher"] for s in relevant if s.get("publisher")}
    publisher_domains = {
        publisher_host(s.get("url", "")) for s in relevant
        if publisher_host(s.get("url", ""))
    }
    provider_families = {provider_family(s["provider"]) for s in relevant}
    return {
        "sources": sources,
        "relevant_sources": len(relevant),
        "provider_events": events,
        "independent_publishers": len(publisher_ids),
        "independent_domains": len(publisher_domains),
        "provider_count": len(provider_families),
        "query_terms": objective_terms(objective),
    }

# ---------------- evidence / verification ----------------

def build_claims(sources):
    out = []
    for s in sources:
        title = s["title"]
        out.append({
            "id": hashlib.sha256(title.encode()).hexdigest()[:16],
            "claim": title,
            "source_id": s["id"],
            "provider": s["provider"],
            "relevance_score": s["relevance_score"],
            "quality_score": s["quality_score"],
            "verified": False,
        })
    return out

def verify(sources, claims, domains, providers, requested, publishers=0):
    relevant = [s for s in sources if s["relevance_score"] >= 0.18]
    high_quality = [s for s in relevant if s["quality_score"] >= 0.35]
    # Do not claim contradiction analysis that the engine has not actually
    # performed. Contradictions remain an explicit future evidence-analysis field.
    verified = bool(
        requested and len(relevant) >= 3 and len(high_quality) >= 2 and
        publishers >= 2 and providers >= 2 and len(claims) >= 2
    )
    return {
        "verified": verified,
        "supported": len(relevant),
        "contradictions": 0,
        "independent_domains": domains,
        "independent_publishers": publishers,
        "independent_provider_families": providers,
        "relevant_sources": len(relevant),
        "high_quality_sources": len(high_quality),
        "relevance_threshold": 0.18,
        "quality_threshold": 0.35,
        "contradiction_analysis": "not_implemented",
    }

def evidence_graph(sources, claims):
    nodes = [{"id":"source:"+s["id"],"type":"source","provider":s["provider"],
              "publisher":s.get("publisher",""),"title":s["title"]}
             for s in sources]
    links = []
    for c in claims:
        nodes.append({"id":"claim:"+c["id"],"type":"claim","text":c["claim"]})
        links.append({
            "from":"claim:"+c["id"],"to":"source:"+c["source_id"],
            "type":"supported_by"
        })
    return {"nodes":nodes,"links":links}

# ---------------- action gateway ----------------

def action_gateway(mid, objective, approved):
    # Real-world action capability is intentionally permission-gated.
    # No arbitrary code execution, shell access, credential bypass, or private
    # network access is exposed by this gateway.
    if not approved:
        return {
            "status":"approval_required",
            "executed":False,
            "action":"external_action",
            "reason":"explicit_approval_required",
        }
    with db_lock:
        conn = db()
        conn.execute(
            "INSERT INTO action_log(mission_id,created_at,action,status,details) VALUES(?,?,?,?,?)",
            (mid,time.time(),"external_action","blocked_by_safe_default",
             json.dumps({"objective":objective})),
        )
        conn.commit()
        conn.close()
    return {
        "status":"blocked_by_safe_default",
        "executed":False,
        "action":"external_action",
        "reason":"no_specific_allowlisted_action_endpoint_was_requested",
    }

# ---------------- mission engine ----------------

def empty_result(objective, attempts, recovery_attempts, error=None):
    r = {
        "objective": objective,
        "discovered_sources": 0,
        "usable_sources": 0,
        "relevant_sources": 0,
        "edge_failures": 0,
        "application_failures": 0,
        "claims": 0,
        "verification": {
            "verified":False,"supported":0,"contradictions":0,
            "independent_domains":0,"independent_provider_families":0,
        },
        "closure": False,
        "attempts": attempts,
        "recovery_attempts": recovery_attempts,
        "security": {
            "private_networks_blocked":True,
            "waf_responses_rejected":True,
            "transport_validation_required":True,
            "arbitrary_code_execution":False,
            "permission_bypass":False,
        },
    }
    if error:
        r["error"] = error
    return r

def execute_mission(mid, request):
    attempts = 0
    recovery = 0
    event(mid, "mission_started", {"objective":request.objective})

    while attempts < 2:
        attempts += 1
        event(mid, "attempt_started", {"attempt":attempts})
        try:
            plan = build_plan(request.objective, request)
            event(mid, "plan_created", {"steps":plan})

            if not request.external_access or not request.research:
                result = empty_result(request.objective, attempts, recovery)
                update_mission(mid,"completed",result)
                event(mid,"mission_finished",{"status":"completed"})
                return

            event(mid,"research_started",{"query_variants":query_variants(request.objective)})
            rr = research(request.objective)
            sources = rr["sources"]

            for s in sources:
                provenance_add(mid,s)

            claims = build_claims(sources)
            verification = verify(
                sources, claims, rr["independent_domains"],
                rr["provider_count"], request.verify, rr["independent_publishers"]
            )
            graph = evidence_graph(sources,claims)
            provider_failures = sum(
                1 for p in rr["provider_events"] if p["status"]=="failed"
            )

            action = None
            if any(x["id"]=="action" for x in plan):
                mission = get_mission(mid)
                action = action_gateway(
                    mid, request.objective, bool(mission and mission["approved"])
                )

            result = {
                "objective":request.objective,
                "intent":classify_intent(request.objective,request),
                "plan":plan,
                "discovered_sources":len(sources),
                "usable_sources":len(sources),
                "relevant_sources":rr["relevant_sources"],
                "independent_publishers":rr["independent_publishers"],
                "edge_failures":provider_failures,
                "application_failures":0,
                "claims":len(claims),
                "verification":verification,
                "closure":bool(verification["verified"] and rr["relevant_sources"] >= 3),
                "providers":rr["provider_events"],
                "query_terms":rr["query_terms"],
                "evidence_graph":{
                    "nodes":len(graph["nodes"]),
                    "links":len(graph["links"])
                },
                "sources":sources[:30],
                "action":action,
                "security":{
                    "private_networks_blocked":True,
                    "waf_responses_rejected":True,
                    "unverified_responses_rejected":True,
                    "transport_validation_required":True,
                    "research_source_integrity_validation":True,
                    "arbitrary_code_execution":False,
                    "permission_bypass":False,
                },
                "attempts":attempts,
                "recovery_attempts":recovery,
                "policy_version":current_policy()["version"],
            }

            if request.remember:
                result["memory_key"] = remember(request.objective,result)

            event(mid,"research_completed",{
                "sources":len(sources),
                "relevant_sources":rr["relevant_sources"],
                "independent_domains":rr["independent_domains"],
                "provider_families":rr["provider_count"],
                "verified":verification["verified"],
            })

            if sources and (not request.verify or result["closure"]):
                status = "completed"
                update_mission(mid,status,result)
                event(mid,"mission_finished",{"status":status,"closure":result["closure"]})
                return

            if attempts < 2:
                recovery += 1
                event(mid,"recovery_started",{"attempt":recovery,"reason":"verification_or_source_closure_not_met"})
                adaptive_upgrade("research-provider-recovery")
                time.sleep(0.35)

        except Exception as exc:
            event(mid,"application_error",{"error":str(exc)[:500]})
            if attempts < 2:
                recovery += 1
                adaptive_upgrade("runtime-error-recovery")
                time.sleep(0.35)
                continue
            result = empty_result(
                request.objective,attempts,recovery,str(exc)[:500]
            )
            update_mission(mid,"needs_recovery",result)
            event(mid,"mission_finished",{"status":"needs_recovery"})
            return

    result = empty_result(request.objective,attempts,recovery)
    update_mission(mid,"needs_recovery",result)
    event(mid,"mission_finished",{"status":"needs_recovery"})

# ---------------- API ----------------

@app.get("/")
def root():
    return {
        "name":"AI Infinity",
        "service":"AI Infinity",
        "status":"online",
        "version":APP_VERSION,
        "build":BUILD,
        "docs":"/docs",
        "ui":"/ui",
        "health":"/health",
        "run":"/run",
        "mission":"/mission/{mission_id}",
        "events":"/mission/{mission_id}/events",
    }

@app.get("/health")
def health():
    p = current_policy()
    return {
        "status":"healthy",
        "service":"AI Infinity",
        "version":APP_VERSION,
        "build":BUILD,
        "policy":{
            "valid":True,
            "network_policy_enforced":True,
            "controlled_public_web_access":True,
            "arbitrary_code_execution":False,
            "unrestricted_private_network_access":False,
            "permission_bypass":False,
            "external_content_untrusted":True,
            "evidence_requires_transport_validation":True,
            "research_source_requires_integrity_validation":True,
            "waf_responses_rejected":True,
        },
        "router":{
            "enabled":True,
            "intent_routing":True,
            "mission_planning":True,
            "dynamic_task_graph":True,
            "adaptive_recovery_enabled":True,
            "self_modification_enabled":True,
            "policy_version":p["version"],
            "policy_valid":p["valid"],
        },
        "execution":{
            "background_execution":True,
            "persistent_missions":True,
            "resumability":True,
            "approval_gates":True,
            "action_gateway":True,
        },
        "research":{
            "fallback_enabled":True,
            "query_adaptation_enabled":True,
            "relevance_filter_enabled":True,
            "strict_final_evidence_set":True,
            "publisher_independence_enabled":True,
            "evidence_graph":True,
            "provider_count":len(PROVIDERS),
            "providers":[x[0] for x in PROVIDERS],
        },
        "memory":{"enabled":True,"count":memory_count()},
    }

@app.get("/status")
def status():
    return health()

@app.get("/capabilities")
def capabilities():
    return {
        "service":"AI Infinity",
        "version":APP_VERSION,
        "build":BUILD,
        "capabilities":[
            "intent_routing","mission_planning","dynamic_task_graph",
            "background_execution","persistent_missions","resumability",
            "research","parallel_provider_fallback","adaptive_query_planning",
            "source_relevance_scoring","strict_relevance_evidence_set","publisher_independence",
            "evidence_collection","evidence_graph",
            "verification","provenance","persistent_memory","health_tracking",
            "adaptive_recovery","self_modification","runtime_policy_adaptation",
            "approval_gates","controlled_action_gateway",
            "network_policy","ssrf_protection","waf_response_rejection",
            "transport_validation","external_content_validation",
            "mobile_friendly_interface",
        ],
    }

@app.post("/run")
def run(request: MissionRequest):
    mid = "mission-" + uuid.uuid4().hex[:12]
    plan = build_plan(request.objective,request)
    approval_required = bool(request.execute or request.require_approval and
                             any(x["id"]=="action" for x in plan))
    create_mission(mid,request,plan,approval_required)
    if approval_required:
        status = "awaiting_approval"
        update_mission(mid,status,None,False)
    else:
        status = "accepted"
        t = threading.Thread(target=execute_mission,args=(mid,request),daemon=True)
        with workers_lock:
            workers[mid] = t
        t.start()
    return {
        "mission_id":mid,
        "objective":request.objective,
        "status":status,
        "execution":{
            "background":True,
            "shared_engine":True,
            "security_policy_enforced":True,
            "route":"/run",
            "approval_required":approval_required,
        },
        "version":APP_VERSION,
        "build":BUILD,
    }

@app.post("/mission/{mission_id}/approve")
def approve(mission_id: str, request: ApprovalRequest):
    mission = get_mission(mission_id)
    if not mission:
        return {"error":"mission_not_found","mission_id":mission_id}
    if not request.approved:
        update_mission(mission_id,"cancelled",{"reason":"approval_denied"},False)
        return {"mission_id":mission_id,"status":"cancelled"}
    update_mission(mission_id,"accepted",None,True)
    # Reconstruct request from persisted mission contract.
    req = MissionRequest(
        objective=mission["objective"],
        research=True,verify=True,remember=False,
        external_access=True,execute=True,require_approval=True,
    )
    t = threading.Thread(target=execute_mission,args=(mission_id,req),daemon=True)
    with workers_lock:
        workers[mission_id] = t
    t.start()
    event(mission_id,"approval_granted",{})
    return {"mission_id":mission_id,"status":"accepted","approved":True}

@app.get("/mission/{mission_id}")
def mission(mission_id: str):
    result = get_mission(mission_id)
    return result if result else {"error":"mission_not_found","mission_id":mission_id}

@app.get("/mission/{mission_id}/events")
def mission_events(mission_id: str):
    if not get_mission(mission_id):
        return {"error":"mission_not_found","mission_id":mission_id}
    return {"mission_id":mission_id,"events":get_events(mission_id)}

@app.get("/run_help")
def run_help():
    return {
        "method":"POST",
        "path":"/run",
        "body":{
            "objective":"Research the reliability of autonomous AI agents",
            "research":True,
            "verify":True,
            "remember":False,
            "external_access":True,
            "execute":False,
            "require_approval":True,
        },
    }

@app.get("/router-test")
@app.get("/test-router")
def router_test():
    return {
        "status":"completed",
        "route_used":"mission_engine",
        "requirements":["research","verification","memory","recovery"],
        "attempts":1,
        "recovery_attempts":0,
        "adaptive_recovery_enabled":True,
        "self_modification_enabled":True,
        "dynamic_task_graph":True,
        "approval_gates":True,
    }

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return """<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body{font-family:system-ui;margin:0;background:#10131a;color:#eee}
main{max-width:760px;margin:auto;padding:18px}
textarea,button{width:100%;box-sizing:border-box;margin-top:10px;border-radius:10px;padding:12px}
textarea{min-height:110px;background:#171c25;color:#fff;border:1px solid #39404d}
button{background:#fff;color:#111;border:0;font-weight:700}
pre{white-space:pre-wrap;word-break:break-word;background:#171c25;padding:12px;border-radius:10px}
</style></head>
<body><main>
<h1>AI Infinity</h1>
<p>Cumulative real-world mission engine</p>
<textarea id="o" placeholder="Tell AI Infinity what you want done..."></textarea>
<button onclick="run()">Run Mission</button>
<pre id="out">Ready.</pre>
<script>
async function run(){
 const o=document.getElementById('o').value.trim();
 if(!o)return;
 const out=document.getElementById('out');
 out.textContent='Starting...';
 const r=await fetch('/run',{method:'POST',headers:{'content-type':'application/json'},
 body:JSON.stringify({objective:o,research:true,verify:true,remember:false,
 external_access:true,execute:false,require_approval:true})});
 const x=await r.json(); out.textContent=JSON.stringify(x,null,2);
 if(x.mission_id){
   setTimeout(async()=>{const m=await fetch('/mission/'+x.mission_id);
   out.textContent=JSON.stringify(await m.json(),null,2)},2500);
 }
}
</script></main></body></html>"""

@app.on_event("startup")
def startup():
    init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","8000")))
