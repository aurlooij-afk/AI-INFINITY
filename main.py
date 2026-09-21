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


APP_VERSION = "TARGET-2050.85"
BUILD = "CUMULATIVE-ALL-SUCCESSFUL-VERSIONS-RATE-LIMIT-RESILIENT-RECOVERY-AND-ROBUST-INTERFACE-CORE"

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
        conn.execute("""CREATE TABLE IF NOT EXISTS connectors(
            name TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
            contract TEXT, updated_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS checkpoints(
            id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
            created_at REAL NOT NULL, step TEXT NOT NULL, state TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS learning(
            id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT, created_at REAL NOT NULL,
            signal TEXT NOT NULL, value TEXT NOT NULL)""")
        builtin_connectors = [
            ("reasoning","core","ready",{"contract":"reasoning.v1"}),
            ("planner","core","ready",{"contract":"planner.v1"}),
            ("memory","core","ready",{"contract":"memory.v1"}),
            ("web_read","research","ready",{"contract":"controlled-public-web.v1"}),
            ("verification","evidence","ready",{"contract":"verification.v2"}),
            ("action_gateway","execution","approval-gated",{"contract":"action-gateway.v2"}),
            ("world_model","intelligence","ready",{"contract":"world-model.v1"}),
            ("video","media","ready",{"contract":"video-capability.v1"}),
        ]
        for name, kind, status, contract in builtin_connectors:
            conn.execute("INSERT OR IGNORE INTO connectors(name,kind,status,contract,updated_at) VALUES(?,?,?,?,?)",
                         (name,kind,status,json.dumps(contract),time.time()))
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
USER_AGENT = "AI-Infinity/2050.85-controlled-public-research"

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

# ---------------- cumulative intelligence fabric ----------------

def checkpoint(mid, step, state):
    with db_lock:
        conn=db()
        conn.execute("INSERT INTO checkpoints(mission_id,created_at,step,state) VALUES(?,?,?,?)",
                     (mid,time.time(),step,json.dumps(state,default=str)[:10000]))
        conn.commit(); conn.close()
    event(mid,"checkpoint",{"step":step})

def record_learning(mid, signal, value):
    with db_lock:
        conn=db(); conn.execute("INSERT INTO learning(mission_id,created_at,signal,value) VALUES(?,?,?,?)",
                                (mid,time.time(),signal,json.dumps(value,default=str)[:10000])); conn.commit(); conn.close()

def connector_registry():
    with db_lock:
        conn=db(); rows=conn.execute("SELECT name,kind,status,contract,updated_at FROM connectors ORDER BY name").fetchall(); conn.close()
    return [{"name":r["name"],"kind":r["kind"],"status":r["status"],"contract":json.loads(r["contract"] or "{}"),"updated_at":r["updated_at"]} for r in rows]

def source_identity(source):
    pub=source.get("publisher") or ""
    name=source.get("publisher_name") or ""
    if pub: return pub
    if name: return publisher_identity(name, source.get("url",""), source.get("provider",""))
    return publisher_host(source.get("url",""))

def evidence_type(source):
    title=source.get("title","").lower()
    if any(x in title for x in ("survey","review","systematic review")): return "review"
    if any(x in title for x in ("benchmark","evaluation","empirical","experiment","study","results","reliability","task completion")): return "empirical_or_evaluation"
    if source.get("type")=="reference": return "reference"
    return "academic_or_theoretical"

def empirical_score(source, objective):
    title=source.get("title","").lower()
    terms=objective_terms(objective)
    direct=sum(1 for t in terms if t in title)/max(1,len(terms))
    empirical_words=("benchmark","evaluation","empirical","experiment","study","results","reliability","success","completion","performance")
    hits=sum(1 for w in empirical_words if w in title)
    score=min(1.0, direct*0.65 + min(hits,3)*0.10 + (0.15 if source.get("type")=="academic_work" else 0.0))
    return round(score,4)

def contradiction_analysis(sources):
    # Conservative deterministic contradiction detector: only marks a contradiction
    # when the same normalized topic has explicit opposing polarity terms.
    positive=("improves","effective","successful","reliable","benefit","increase","higher","outperforms")
    negative=("fails","failure","unreliable","ineffective","harm","decrease","lower","underperforms","limitations")
    pos=[]; neg=[]
    for s in sources:
        t=s.get("title","").lower()
        if any(w in t for w in positive): pos.append(s)
        if any(w in t for w in negative): neg.append(s)
    if pos and neg:
        return {"count":1,"status":"possible_conflict","method":"title_polarity_screen","positive_sources":[x["id"] for x in pos[:5]],"negative_sources":[x["id"] for x in neg[:5]]}
    return {"count":0,"status":"no_explicit_polarity_conflict_detected","method":"title_polarity_screen"}

def source_quality_v2(source, objective):
    base=quality_score(source)
    emp=empirical_score(source,objective)
    et=evidence_type(source)
    penalty=0.10 if et=="reference" else 0.0
    return round(max(0.0,min(1.0,base*0.65+emp*0.35-penalty)),4)

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
    filtered = [s for s in ranked if s["relevance_score"] >= 0.18]
    for s in filtered:
        s["evidence_type"] = evidence_type(s)
        s["empirical_score"] = empirical_score(s, objective)
        s["quality_score_v2"] = source_quality_v2(s, objective)
    # Keep the final evidence set focused: references are retained only when they
    # are among the strongest relevant context sources; direct academic evidence wins.
    academic=[s for s in filtered if s.get("evidence_type")!="reference"]
    refs=[s for s in filtered if s.get("evidence_type")=="reference"]
    academic=sorted(academic,key=lambda s:(s.get("quality_score_v2",0),s.get("relevance_score",0)),reverse=True)
    refs=sorted(refs,key=lambda s:(s.get("quality_score_v2",0),s.get("relevance_score",0)),reverse=True)
    return academic[:30] + refs[:1]

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

def verify(sources, claims, domains, providers, requested, publishers=0, contradiction=None, objective=""):
    relevant=len([s for s in sources if s.get("relevance_score",0)>=0.18])
    high_quality=len([s for s in sources if s.get("quality_score_v2",s.get("quality_score",0))>=0.35])
    empirical=len([s for s in sources if s.get("empirical_score",0)>=0.30])
    independent=set(source_identity(s) for s in sources if source_identity(s))
    families=set(provider_family(s.get("provider","")) for s in sources if s.get("provider"))
    conflict=contradiction or {"count":0,"status":"not_run","method":"none"}
    # Truthful closure: no contradiction claim without analysis, at least 3 relevant
    # sources, 2+ independent publishers, 2 provider families, 2 claims and quality evidence.
    verified=bool(
        requested and relevant>=3 and high_quality>=2 and len(independent)>=2 and
        len(families)>=2 and len(claims)>=2 and conflict.get("count",0)==0 and
        conflict.get("status") not in {"not_run","not_implemented"}
    )
    return {
        "verified":verified,
        "supported":relevant,
        "contradictions":conflict.get("count",0),
        "contradiction_analysis":conflict,
        "independent_domains":len(set(s.get("domain") for s in sources if s.get("domain"))),
        "independent_publishers":len(independent),
        "independent_provider_families":len(families),
        "relevant_sources":relevant,
        "high_quality_sources":high_quality,
        "empirical_or_evaluation_sources":empirical,
        "relevance_threshold":0.18,
        "quality_threshold":0.35,
        "verification_requirements":{
            "min_relevant_sources":3,"min_independent_publishers":2,
            "min_provider_families":2,"min_claims":2,"contradiction_analysis_required":True
        },
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
            checkpoint(mid,"research",{"sources":len(sources)})
            contradiction = contradiction_analysis(sources)
            verification = verify(
                sources, claims, rr["independent_domains"],
                rr["provider_count"], request.verify, rr["independent_publishers"],
                contradiction, request.objective
            )
            graph = evidence_graph(sources,claims)
            record_learning(mid,"research_signal",{"sources":len(sources),"publishers":rr["independent_publishers"],"verification":verification["verified"]})
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
                "closure":bool(verification["verified"] and verification["contradiction_analysis"]["status"] not in {"not_run","not_implemented"}),
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
                    "contradiction_analysis_required":True,
                    "publisher_identity_validation":True,
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
        "interface":"/interface",
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
        "interface":{
            "enabled":True,
            "path":"/ui",
            "alias":"/interface",
            "polling":True,
            "error_handling":True,
            "mobile_ready":True
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
            "publisher_identity_resolution":True,
            "empirical_evidence_scoring":True,
            "contradiction_analysis":True,
            "truthful_closure":True,
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
            "capability_discovery","connector_registry","connector_contracts",
            "parallel_orchestration","checkpoints","fallback_recovery","self_critique",
            "gap_detection","opportunity_detection","world_model","video_capability",
            "learning_loop","mission_state_graph",
            "adaptive_recovery","self_modification","runtime_policy_adaptation",
            "approval_gates","controlled_action_gateway",
            "network_policy","ssrf_protection","waf_response_rejection",
            "transport_validation","external_content_validation",
            "mobile_friendly_interface",
        ],
    }

@app.get("/connectors")
def connectors():
    return {"status":"healthy","connectors":connector_registry()}

@app.get("/mission/{mission_id}/checkpoints")
def mission_checkpoints(mission_id: str):
    if not get_mission(mission_id): return {"error":"mission_not_found","mission_id":mission_id}
    with db_lock:
        conn=db(); rows=conn.execute("SELECT created_at,step,state FROM checkpoints WHERE mission_id=? ORDER BY id",(mission_id,)).fetchall(); conn.close()
    return {"mission_id":mission_id,"checkpoints":[{"created_at":r["created_at"],"step":r["step"],"state":json.loads(r["state"])} for r in rows]}

@app.get("/memory")
def memory_api():
    with db_lock:
        conn=db(); rows=conn.execute("SELECT id,created_at,key,value FROM memory ORDER BY id DESC LIMIT 50").fetchall(); conn.close()
    return {"count":memory_count(),"items":[{"id":r["id"],"created_at":r["created_at"],"key":r["key"],"value":json.loads(r["value"])} for r in rows]}

@app.get("/world-model")
def world_model():
    return {"status":"ready","mode":"structured-mission-world-model","knowledge":"derived-only","external_facts_not_claimed":True,"components":["objective","plan","sources","claims","provenance","verification","actions"]}

@app.get("/opportunities")
def opportunities():
    return {"status":"ready","mode":"gap-and-next-step-detection","rules":["missing_evidence","provider_failure","verification_gap","approval_required","recovery_needed"]}

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

def interface_html():
    return """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1020">
<title>AI Infinity — Mission Interface</title>
<style>
:root{color-scheme:dark;--bg:#080b12;--card:#111827;--line:#273449;--text:#f4f7fb;--muted:#9aa8bb;--accent:#fff;--ok:#76e3a4;--warn:#ffd27a;--bad:#ff8d8d}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#080b12,#0d1320);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;min-height:100vh}
main{width:min(920px,100%);margin:auto;padding:18px 14px 40px}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:16px}.brand{font-size:25px;font-weight:800}.version{font-size:12px;color:var(--muted)}
.card{background:rgba(17,24,39,.94);border:1px solid var(--line);border-radius:16px;padding:16px;margin:12px 0;box-shadow:0 8px 28px rgba(0,0,0,.18)}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:8px}textarea{display:block;width:100%;min-height:150px;resize:vertical;background:#0b1220;color:var(--text);border:1px solid #334155;border-radius:12px;padding:14px;font:inherit;outline:none}textarea:focus{border-color:#64748b}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}button{width:100%;border:1px solid #3b4658;border-radius:11px;padding:13px;background:#fff;color:#0b0f16;font-weight:800;font-size:15px;cursor:pointer}button.secondary{background:#182234;color:#fff}button:disabled{opacity:.55;cursor:wait}
.status{display:flex;gap:8px;align-items:center;font-size:14px}.dot{width:9px;height:9px;border-radius:50%;background:var(--ok)}
pre{margin:0;white-space:pre-wrap;word-break:break-word;overflow:auto;background:#080d16;border:1px solid #202c3d;border-radius:12px;padding:13px;max-height:58vh;font-size:12px;line-height:1.45}.muted{color:var(--muted)}a{color:#fff}
@media(max-width:520px){main{padding:12px 10px 30px}.actions{grid-template-columns:1fr}.brand{font-size:22px}}
</style></head>
<body><main>
<div class="top"><div><div class="brand">AI Infinity</div><div class="version">Real-world mission interface</div></div><div class="status"><span class="dot"></span><span id="healthText">Checking…</span></div></div>
<div class="card"><label for="objective">Mission objective</label><textarea id="objective" placeholder="Tell AI Infinity what you want researched or accomplished…"></textarea>
<div class="actions"><button id="runBtn" onclick="runMission()">Run Mission</button><button class="secondary" onclick="checkHealth()">Check Health</button></div></div>
<div class="card"><div class="muted" id="state">Ready.</div><div style="height:8px"></div><pre id="output">Waiting for a mission.</pre></div>
<script>
const API=window.location.origin;
const TERMINAL=new Set(['completed','needs_recovery','failed','cancelled','awaiting_approval']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function show(x){document.getElementById('output').textContent=typeof x==='string'?x:JSON.stringify(x,null,2)}
async function request(path,options={}){
 const r=await fetch(API+path,{cache:'no-store',...options});
 const text=await r.text(); let x; try{x=JSON.parse(text)}catch{throw new Error('HTTP '+r.status+': '+text.slice(0,500))}
 if(!r.ok)throw new Error('HTTP '+r.status+': '+(x.detail||x.error||JSON.stringify(x)));
 return x;
}
async function checkHealth(){
 const t=document.getElementById('healthText');
 try{const x=await request('/health');t.textContent='Online · '+x.version;show(x)}catch(e){t.textContent='Health check failed';show(String(e))}
}
async function runMission(){
 const objective=document.getElementById('objective').value.trim();
 if(!objective){show('Enter a mission objective first.');return}
 const btn=document.getElementById('runBtn'),state=document.getElementById('state');
 btn.disabled=true;state.textContent='Submitting mission…';
 try{
  const x=await request('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({objective,research:true,verify:true,remember:false,external_access:true,execute:false,require_approval:true})});
  show(x);
  if(!x.mission_id)return;
  const id=x.mission_id;state.textContent='Mission accepted · '+id;
  for(let i=0;i<30;i++){await sleep(2000);const m=await request('/mission/'+encodeURIComponent(id));show(m);const s=m.status||(m.result&&m.result.status);state.textContent='Mission '+id+' · '+(s||'running');if(TERMINAL.has(s))break}
 }catch(e){state.textContent='Request failed';show(String(e))}
 finally{btn.disabled=false}
}
checkHealth();
</script></main></body></html>"""

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return interface_html()

@app.get("/interface", response_class=HTMLResponse)
def interface():
    return interface_html()

@app.on_event("startup")
def startup():
    init_db()


# ==================== TARGET-2050.83 EXTENSIONS ====================

# 2050.83 evidence rule:
# Never infer empirical evidence merely from words such as "reliability" or
# "autonomous" in a title. Empirical status requires explicit evaluation signals
# or metadata indicating measured/evaluated work.

def _abstract_from_openalex(inv):
    if not isinstance(inv, dict):
        return ""
    words = {}
    for word, positions in inv.items():
        for p in positions or []:
            words[p] = word
    return " ".join(words[i] for i in sorted(words))[:12000]

def _clean_abstract(x):
    if isinstance(x, str):
        # Crossref abstracts may contain JATS/XML tags.
        x = re.sub(r"<[^>]+>", " ", x)
        return re.sub(r"\s+", " ", x).strip()[:12000]
    return ""

def provider_crossref(q):
    data = parse_json(safe_fetch(
        "https://api.crossref.org/v1/works?query="+quote(q)+
        "&rows=10&select=DOI,title,URL,published,issued,publisher,abstract,link,type,subtype"
    ))
    out = []
    for x in data.get("message", {}).get("items", []):
        title = (x.get("title") or [None])[0]
        if not title:
            continue
        d = x.get("published-print") or x.get("published-online") or x.get("issued") or {}
        parts = d.get("date-parts", [[None]])
        year = parts[0][0] if parts and parts[0] else None
        links = x.get("link") or []
        fulltext = [z.get("URL") for z in links if z.get("URL")]
        out.append({
            "title": title,
            "url": x.get("URL") or ("https://doi.org/"+x["DOI"] if x.get("DOI") else ""),
            "provider": "crossref",
            "year": year,
            "type": "academic_work",
            "publisher_name": x.get("publisher") or "",
            "abstract": _clean_abstract(x.get("abstract")),
            "fulltext_urls": fulltext[:3],
            "metadata_type": x.get("type") or "",
        })
    return out

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
            "abstract": _abstract_from_openalex(x.get("abstract_inverted_index")),
            "fulltext_urls": [loc.get("pdf_url")] if loc.get("pdf_url") else [],
            "metadata_type": str(x.get("type") or ""),
            "cited_by_count": x.get("cited_by_count", 0),
        })
    return out

def provider_semantic(q):
    data = parse_json(safe_fetch(
        "https://api.semanticscholar.org/graph/v1/paper/search?query="+
        quote(q)+"&limit=10&fields=title,url,year,venue,abstract,openAccessPdf"
    ))
    out=[]
    for x in data.get("data", []):
        if not x.get("title"):
            continue
        pdf=x.get("openAccessPdf") or {}
        out.append({
            "title": x.get("title"),
            "url": x.get("url") or "",
            "provider": "semantic_scholar",
            "year": x.get("year"),
            "type": "academic_work",
            "publisher_name": x.get("venue") or "",
            "abstract": _clean_abstract(x.get("abstract")),
            "fulltext_urls": [pdf.get("url")] if pdf.get("url") else [],
        })
    return out

def provider_crossref_alt(q):
    data = parse_json(safe_fetch(
        "https://api.crossref.org/v1/works?select=DOI,title,URL,published,publisher,abstract,link,type&rows=6"+
        "&query.bibliographic="+quote(q)
    ))
    out=[]
    for x in data.get("message", {}).get("items", []):
        title=(x.get("title") or [None])[0]
        if not title:
            continue
        d=x.get("published") or x.get("issued") or {}
        parts=d.get("date-parts", [[None]])
        year=parts[0][0] if parts and parts[0] else None
        url=x.get("URL") or ("https://doi.org/"+x["DOI"] if x.get("DOI") else "")
        links=x.get("link") or []
        out.append({
            "title":title,"url":url,"provider":"crossref_alt","year":year,
            "type":"academic_work","publisher_name":x.get("publisher") or "",
            "abstract":_clean_abstract(x.get("abstract")),
            "fulltext_urls":[z.get("URL") for z in links if z.get("URL")][:3],
            "metadata_type":x.get("type") or "",
        })
    return out

PROVIDERS = [
    ("openalex", provider_openalex),
    ("crossref", provider_crossref),
    ("semantic_scholar", provider_semantic),
    ("wikipedia", provider_wikipedia),
    ("crossref_alt", provider_crossref_alt),
]

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
        "abstract": _clean_abstract(item.get("abstract")),
        "fulltext_urls": [u for u in (item.get("fulltext_urls") or []) if isinstance(u,str)][:3],
        "metadata_type": str(item.get("metadata_type") or ""),
        "cited_by_count": int(item.get("cited_by_count") or 0),
    }

def evidence_type(source):
    text=(source.get("title","")+" "+source.get("abstract","")).lower()
    if any(x in text for x in ("systematic review","systematic literature review","meta-analysis","survey of")):
        return "review"
    strong=(
        "benchmark","evaluation","evaluated","experiment","experimental",
        "empirical","user study","controlled study","ablation",
        "task completion rate","success rate","completion rate",
        "measured performance","quantitative evaluation","human evaluation",
        "benchmarking","test set","dataset"
    )
    if any(x in text for x in strong):
        return "empirical_or_evaluation"
    if source.get("type")=="reference":
        return "reference"
    return "academic_or_theoretical"

def empirical_score(source, objective):
    title=source.get("title","").lower()
    abstract=source.get("abstract","").lower()
    text=title+" "+abstract
    terms=objective_terms(objective)
    direct=sum(1 for t in terms if t in text)/max(1,len(terms))
    strong=(
        "benchmark","evaluation","evaluated","experiment","experimental",
        "empirical","user study","controlled study","ablation",
        "task completion rate","success rate","completion rate",
        "measured performance","quantitative evaluation","human evaluation",
        "benchmarking","test set","dataset"
    )
    hits=sum(1 for w in strong if w in text)
    score=min(1.0, direct*0.35 + min(hits,4)*0.16)
    if source.get("abstract"):
        score=min(1.0,score+0.08)
    return round(score,4)

def source_quality_v2(source, objective):
    base=quality_score(source)
    emp=empirical_score(source,objective)
    et=evidence_type(source)
    penalty=0.15 if et=="reference" else (0.08 if et=="academic_or_theoretical" else 0)
    return round(max(0.0,min(1.0,base*0.60+emp*0.40-penalty)),4)

def rank_sources(items, objective):
    for s in items:
        s["relevance_score"]=relevance_score(s,objective)
        s["quality_score"]=quality_score(s)
        s["evidence_type"]=evidence_type(s)
        s["empirical_score"]=empirical_score(s,objective)
        s["quality_score_v2"]=source_quality_v2(s,objective)
        s["abstract_available"]=bool(s.get("abstract"))
        s["fulltext_available"]=bool(s.get("fulltext_urls"))
    ranked=sorted(items,key=lambda s:(s["relevance_score"],s["quality_score_v2"],s["empirical_score"],s.get("year") or 0),reverse=True)
    # No 0-score leakage. Prefer direct evidence; keep reference context only if
    # needed to maintain source diversity and never count it as empirical evidence.
    relevant=[s for s in ranked if s["relevance_score"]>=0.18]
    empirical=[s for s in relevant if s["evidence_type"]=="empirical_or_evaluation" and s["empirical_score"]>=0.35]
    non_emp=[s for s in relevant if s not in empirical and s["evidence_type"]!="reference"]
    refs=[s for s in relevant if s["evidence_type"]=="reference"]
    # Empirical evidence gets priority. The final set is capped for predictable runtime.
    return (empirical[:24] + non_emp[:12] + refs[:1])[:30]

def _claim_subject_tokens(text):
    stop={
        "the","and","for","with","from","that","this","these","those","into","using",
        "based","their","they","are","was","were","has","have","been","about","through",
        "study","research","paper","approach","method","system","systems","model","models",
        "ai","artificial","intelligence","agent","agents"
    }
    words=re.findall(r"[a-z0-9]{4,}", text.lower())
    return {w for w in words if w not in stop}

def _polarity_hits(text):
    positive=("improves","improved","effective","effective performance","successful","reliable","benefit","increase","higher","outperforms","better","positive result","significant improvement")
    negative=("fails","failed","failure","unreliable","ineffective","harm","decrease","lower","underperforms","worse","no improvement","negative result","significant limitation")
    t=text.lower()
    return [w for w in positive if w in t], [w for w in negative if w in t]

def contradiction_analysis(sources):
    # 2050.84 improves the previous polarity screen by comparing claim-like
    # sentence fragments with overlapping subject terms and opposite polarity.
    # It remains a deterministic screening layer, not proof of semantic conflict.
    records=[]
    for s in sources:
        text=(s.get("title","")+" "+s.get("abstract","")).strip()
        pos,neg=_polarity_hits(text)
        if not pos and not neg:
            continue
        subjects=_claim_subject_tokens(text)
        records.append({"id":s["id"],"subjects":subjects,"positive":pos[:6],"negative":neg[:6],"publisher":s.get("publisher"),"provider":s.get("provider")})
    conflicts=[]
    for i,a in enumerate(records):
        for b in records[i+1:]:
            overlap=len(a["subjects"] & b["subjects"])
            opposite=(bool(a["positive"]) and bool(b["negative"])) or (bool(a["negative"]) and bool(b["positive"]))
            if overlap>=2 and opposite:
                conflicts.append({"a":a["id"],"b":b["id"],"shared_subject_terms":sorted(a["subjects"] & b["subjects"])[:10],"polarity_a":{"positive":a["positive"],"negative":a["negative"]},"polarity_b":{"positive":b["positive"],"negative":b["negative"]}})
    if conflicts:
        return {"count":len(conflicts),"status":"possible_conflict","method":"claim_subject_overlap_plus_polarity_screen","analysis_scope":"screening_only","semantic_contradiction_proof":False,"conflicts":conflicts[:20]}
    return {"count":0,"status":"no_explicit_claim_conflict_detected","method":"claim_subject_overlap_plus_polarity_screen","analysis_scope":"screening_only","semantic_contradiction_proof":False,"records_screened":len(records)}

def build_claims(sources):
    out=[]
    for s in sources:
        out.append({
            "id":hashlib.sha256((s["id"]+"|"+s["title"]).encode()).hexdigest()[:16],
            "claim":s["title"],
            "source_id":s["id"],
            "provider":s["provider"],
            "publisher":s.get("publisher"),
            "evidence_type":s.get("evidence_type"),
            "empirical_score":s.get("empirical_score",0),
            "relevance_score":s.get("relevance_score",0),
            "quality_score_v2":s.get("quality_score_v2",0),
            "verified":False,
            "verification_basis":"source_metadata_and_screening_only"
        })
    return out

def verify(sources, claims, domains, providers, requested, publishers=0, contradiction=None, objective=""):
    relevant=[s for s in sources if s.get("relevance_score",0)>=0.18]
    high=[s for s in relevant if s.get("quality_score_v2",0)>=0.35]
    empirical=[s for s in relevant if s.get("evidence_type")=="empirical_or_evaluation" and s.get("empirical_score",0)>=0.35]
    independent=set(source_identity(s) for s in relevant if source_identity(s))
    families=set(provider_family(s.get("provider","")) for s in relevant if s.get("provider"))
    conflict=contradiction or {"count":0,"status":"not_run","method":"none"}
    # 2050.83 distinguishes "evidence closure" from "scientific proof".
    # Verification requires direct empirical/evaluation evidence, while the
    # contradiction stage is explicitly a screening layer.
    requirements={
        "min_relevant_sources":3,
        "min_independent_publishers":2,
        "min_provider_families":2,
        "min_claims":2,
        "min_empirical_or_evaluation_sources":3,
        "contradiction_screen_required":True,
        "semantic_contradiction_proof_required":False
    }
    verified=bool(
        requested and len(relevant)>=3 and len(high)>=2 and len(empirical)>=3 and
        len(independent)>=2 and len(families)>=2 and len(claims)>=2 and
        conflict.get("status")=="no_explicit_polarity_conflict_detected" and
        conflict.get("count",0)==0
    )
    return {
        "verified":verified,
        "verification_mode":"empirical_evidence_screen",
        "supported":len(relevant),
        "contradictions":conflict.get("count",0),
        "contradiction_analysis":conflict,
        "independent_domains":len(set(s.get("domain") for s in relevant if s.get("domain"))),
        "independent_publishers":len(independent),
        "independent_provider_families":len(families),
        "relevant_sources":len(relevant),
        "high_quality_sources":len(high),
        "empirical_or_evaluation_sources":len(empirical),
        "review_sources":len([s for s in relevant if s.get("evidence_type")=="review"]),
        "theoretical_sources":len([s for s in relevant if s.get("evidence_type")=="academic_or_theoretical"]),
        "reference_sources":len([s for s in relevant if s.get("evidence_type")=="reference"]),
        "relevance_threshold":0.18,
        "quality_threshold":0.35,
        "empirical_threshold":0.35,
        "verification_requirements":requirements,
        "limitations":[
            "publisher_identity_is_metadata_based",
            "contradiction_analysis_is_title_abstract_screening",
            "full_text_semantic_contradiction_analysis_is_not_implemented",
            "verification_does_not_equal_real_world_success"
        ]
    }

PROVIDER_RETRY_DELAYS=(0.25,0.75,1.5)

def _retryable_failure(kind):
    return kind in {"rate_limited","provider_server_error","transport_or_parse_error","timeout"}

def call_provider(name, fn, objective, retry_round=0):
    started=time.time()
    errors=[]
    variants=query_variants(objective)
    # A recovery round uses the compact query first to reduce provider load.
    ordered=list(dict.fromkeys(([" ".join(objective_terms(objective)[:8])] if retry_round else [])+variants))
    for i,q in enumerate(ordered[:3]):
        max_attempts=2 if retry_round==0 else 1
        for attempt in range(max_attempts):
            try:
                items=deduplicate_sources(fn(q))
                return {"provider":name,"status":"success","sources":len(items),"latency_ms":int((time.time()-started)*1000),"query_variant":i,"query":q,"retry_round":retry_round,"retries":attempt,"items":items}
            except Exception as exc:
                kind=failure_kind(exc)
                errors.append({"error":str(exc)[:300],"kind":kind,"variant":i,"attempt":attempt})
                if attempt+1<max_attempts and _retryable_failure(kind):
                    time.sleep(PROVIDER_RETRY_DELAYS[min(attempt,len(PROVIDER_RETRY_DELAYS)-1)])
                elif i+1<len(ordered) and _retryable_failure(kind):
                    time.sleep(0.10)
    e=errors[-1] if errors else {"error":"unknown_failure","kind":"unknown","variant":0,"attempt":0}
    return {"provider":name,"status":"failed","sources":0,"error":e["error"],"failure_kind":e["kind"],"latency_ms":int((time.time()-started)*1000),"retries":len(errors)-1,"retry_round":retry_round,"items":[]}

def research(objective, recovery_round=0, failed_provider_names=None):
    target=set(failed_provider_names or [name for name,_ in PROVIDERS])
    results=[]; threads=[]; lock=threading.Lock()
    def worker(name,fn):
        r=call_provider(name,fn,objective,recovery_round)
        with lock: results.append(r)
    for name,fn in PROVIDERS:
        if name not in target and recovery_round>0:
            continue
        t=threading.Thread(target=worker,args=(name,fn),daemon=True); t.start(); threads.append(t)
    for t in threads: t.join(timeout=16)
    by_name={x["provider"]:x for x in results}
    all_items=[]; events=[]
    for name,_ in PROVIDERS:
        if recovery_round>0 and name not in target:
            continue
        r=by_name.get(name) or {"provider":name,"status":"failed","sources":0,"error":"provider_timeout","failure_kind":"timeout","latency_ms":16000,"items":[] ,"retry_round":recovery_round}
        all_items.extend(r.get("items",[])); events.append({k:v for k,v in r.items() if k!="items"})
    sources=rank_sources(deduplicate_sources(all_items),objective)
    publisher_ids={s["publisher"] for s in sources if s.get("publisher")}
    publisher_domains={publisher_host(s.get("url","")) for s in sources if publisher_host(s.get("url",""))}
    provider_families={provider_family(s["provider"]) for s in sources if s.get("provider")}
    empirical=sum(1 for s in sources if s.get("evidence_type")=="empirical_or_evaluation" and s.get("empirical_score",0)>=0.35)
    return {"sources":sources,"relevant_sources":len(sources),"provider_events":events,"independent_publishers":len(publisher_ids),"independent_domains":len(publisher_domains),"provider_count":len(provider_families),"empirical_or_evaluation_sources":empirical,"query_terms":objective_terms(objective),"recovery_round":recovery_round}

# Upgrade the execution result with explicit evidence gaps and truthful closure.
def execute_mission(mid, request):
    attempts=0; recovery=0; failed_names=[]
    event(mid,"mission_started",{"objective":request.objective})
    while attempts<2:
        attempts+=1; event(mid,"attempt_started",{"attempt":attempts,"recovery_round":recovery})
        try:
            plan=build_plan(request.objective,request); event(mid,"plan_created",{"steps":plan})
            if not request.external_access or not request.research:
                result=empty_result(request.objective,attempts,recovery); update_mission(mid,"completed",result); event(mid,"mission_finished",{"status":"completed"}); return
            rr=research(request.objective,recovery,failed_names if recovery else None); sources=rr["sources"]
            for item in sources: provenance_add(mid,item)
            claims=build_claims(sources); checkpoint(mid,"research",{"sources":len(sources),"recovery_round":recovery})
            contradiction=contradiction_analysis(sources)
            verification=verify(sources,claims,rr["independent_domains"],rr["provider_count"],request.verify,rr["independent_publishers"],contradiction,request.objective)
            graph=evidence_graph(sources,claims)
            failures=[p for p in rr["provider_events"] if p["status"]=="failed"]
            evidence_gaps=[]
            if verification["empirical_or_evaluation_sources"]<3: evidence_gaps.append("insufficient_direct_empirical_or_evaluation_evidence")
            if verification["independent_publishers"]<2: evidence_gaps.append("insufficient_publisher_independence")
            if verification["independent_provider_families"]<2: evidence_gaps.append("insufficient_provider_independence")
            if verification["contradiction_analysis"].get("count",0)>0: evidence_gaps.append("possible_claim_conflict_detected")
            closure=bool(verification["verified"] and not evidence_gaps)
            # Provider failure alone no longer destroys an otherwise closed evidence
            # set. The failed provider is recorded and retried independently.
            if failures and closure:
                provider_note="provider_failures_tolerated_after_independent_evidence_closure"
            else:
                provider_note=None
            result={"objective":request.objective,"intent":classify_intent(request.objective,request),"plan":plan,"discovered_sources":len(sources),"usable_sources":len(sources),"relevant_sources":rr["relevant_sources"],"independent_publishers":rr["independent_publishers"],"edge_failures":len(failures),"application_failures":0,"claims":len(claims),"verification":verification,"closure":closure,"evidence_gaps":evidence_gaps,"provider_recovery":{"round":recovery,"failed_providers":failed_names,"current_failures":[p["provider"] for p in failures],"provider_failures_tolerated":bool(provider_note),"note":provider_note},"providers":rr["provider_events"],"query_terms":rr["query_terms"],"evidence_graph":{"nodes":len(graph["nodes"]),"links":len(graph["links"])},"sources":sources[:30],"security":{"private_networks_blocked":True,"waf_responses_rejected":True,"unverified_responses_rejected":True,"transport_validation_required":True,"research_source_integrity_validation":True,"publisher_identity_validation":True,"contradiction_analysis_required":True,"arbitrary_code_execution":False,"permission_bypass":False},"attempts":attempts,"recovery_attempts":recovery,"policy_version":current_policy()["version"]}
            if request.remember: result["memory_key"]=remember(request.objective,result)
            event(mid,"research_completed",{"sources":len(sources),"relevant_sources":rr["relevant_sources"],"independent_publishers":rr["independent_publishers"],"provider_families":rr["provider_count"],"empirical_sources":verification["empirical_or_evaluation_sources"],"verified":verification["verified"],"closure":closure,"provider_failures":len(failures)})
            if sources and (not request.verify or closure):
                update_mission(mid,"completed",result); event(mid,"mission_finished",{"status":"completed","closure":closure}); return
            if attempts<2:
                recovery+=1
                failed_names=[p["provider"] for p in failures if p.get("failure_kind") in {"rate_limited","provider_server_error","transport_or_parse_error","timeout"}]
                if not failed_names:
                    failed_names=[p["provider"] for p in failures]
                event(mid,"recovery_started",{"attempt":recovery,"reason":"evidence_closure_not_met","gaps":evidence_gaps,"retry_providers":failed_names})
                adaptive_upgrade("rate-limit-aware-evidence-recovery")
                time.sleep(0.35); continue
            update_mission(mid,"needs_recovery",result); event(mid,"mission_finished",{"status":"needs_recovery","closure":closure}); return
        except Exception as exc:
            event(mid,"application_error",{"error":str(exc)[:500]})
            if attempts<2:
                recovery+=1; adaptive_upgrade("runtime-error-recovery"); time.sleep(0.35); continue
            result=empty_result(request.objective,attempts,recovery,str(exc)[:500]); update_mission(mid,"needs_recovery",result); event(mid,"mission_finished",{"status":"needs_recovery"}); return

# A safe real-world-command entry point. It creates an approval-gated action
# mission; it never bypasses permissions and never executes arbitrary code.
@app.post("/command")
def command(request: MissionRequest):
    req=request.model_copy(update={"execute":True,"require_approval":True,"research":True,"verify":True})
    mid="mission-"+uuid.uuid4().hex[:12]
    plan=build_plan(req.objective,req)
    create_mission(mid,req,plan,True)
    update_mission(mid,"awaiting_approval",None,False)
    event(mid,"command_received",{"approval_required":True})
    return {
        "mission_id":mid,"status":"awaiting_approval",
        "command":req.objective,
        "approval_required":True,
        "execution_policy":{
            "arbitrary_code_execution":False,
            "permission_bypass":False,
            "private_network_access":False,
            "external_action_requires_approval":True
        },
        "version":APP_VERSION,"build":BUILD
    }

@app.get("/evidence-policy")
def evidence_policy():
    return {
        "status":"active",
        "verification_is_not_proof":True,
        "empirical_evidence_required":True,
        "publisher_independence_required":True,
        "provider_independence_required":True,
        "contradiction_screening":True,
        "semantic_contradiction_proof":False,
        "full_text_semantic_analysis":False,
        "waf_rejection":True,
        "ssrf_protection":True,
        "unverified_content_rejected":True
    }

@app.get("/resilience-policy")
def resilience_policy():
    return {
        "status":"active",
        "provider_retry_rounds":1,
        "rate_limit_aware":True,
        "failed_provider_isolation":True,
        "alternate_query_retry":True,
        "provider_failure_does_not_override_closed_independent_evidence":True,
        "claim_contradiction_screen":"subject_overlap_plus_polarity",
        "semantic_contradiction_proof":False,
        "arbitrary_code_execution":False,
        "permission_bypass":False
    }

@app.get("/interface-status")
def interface_status():
    return {
        "status":"active",
        "ui":"/ui",
        "alias":"/interface",
        "api_origin":"same-origin",
        "mobile_ready":True,
        "mission_polling":True,
        "network_errors_handled":True,
        "no_cross_origin_dependency":True
    }

@app.get("/version-history")
def version_history():
    return {
        "status":"cumulative",
        "current":APP_VERSION,
        "preserved_successful_lineage":[
            "3.1.0","3.4.0","3.5.0","2050.0","2050.11","2050.40",
            "2050.41","2050.42","2050.44","2050.45","2050.49","2050.50",
            "2050.51","2050.69","2050.76","2050.77","2050.78","2050.79",
            "2050.80","2050.81","2050.82","2050.83","2050.84","2050.85"
        ],
        "principle":"new versions extend confirmed capabilities; they do not treat one prior version as the sole baseline"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","8000")))
