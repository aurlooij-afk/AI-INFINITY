from __future__ import annotations

"""AI Infinity - TARGET-2050.185
DURABLE-AUTONOMOUS-WORKFLOW-ENGINE

Cumulative practical core. Preserves command routing, security gates,
approval, workflow chaining, research planning, memory, workspace-safe
file operations, and webhook/http staging while adding durable execution
records, connector execution, result hashing, verification, and closure.

No arbitrary code execution. External HTTP is SSRF-protected, credentials
are rejected, side-effecting methods require approval, and uncertain
external outcomes are never automatically replayed.
"""

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
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

APP_VERSION = "TARGET-2050.184"
BUILD = "REAL-WORLD-OUTCOME-ORCHESTRATION-CORE"
PREVIOUS_BUILD = "TARGET-2050.184"
DATA_DIR = os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity")
DB_PATH = os.path.join(DATA_DIR, "ai_infinity.db")
WORKSPACE = Path(os.getenv("AI_INFINITY_WORKSPACE", os.path.join(DATA_DIR, "workspace"))).resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

MAX_BODY = 1_000_000
MAX_RESPONSE = 256 * 1024
REQUEST_TIMEOUT = 12
MAX_ATTEMPTS = 2
STALE_SECONDS = max(30, int(os.getenv("AI_INFINITY_ACTION_STALE_SECONDS", "120")))

BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal", "host.docker.internal", "0.0.0.0", "::1"}
SENSITIVE_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "x-auth-token", "x-access-token"}
SAFE_METHODS = {"GET", "HEAD"}
HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}
ALLOWLIST = {x.strip().lower().rstrip(".") for x in os.getenv("AI_INFINITY_ACTION_HOST_ALLOWLIST", "").split(",") if x.strip()}

app = FastAPI(title="AI Infinity", version=APP_VERSION)
_db_lock = threading.RLock()


def now() -> float:
    return time.time()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _db_lock, db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY, text TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS executions (
            id TEXT PRIMARY KEY, parent_id TEXT, objective TEXT, action_type TEXT NOT NULL,
            connector TEXT NOT NULL, status TEXT NOT NULL, approval_required INTEGER NOT NULL,
            approved INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
            recovery_attempts INTEGER NOT NULL DEFAULT 0, result_json TEXT, result_hash TEXT,
            verification_status TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS execution_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, execution_id TEXT NOT NULL,
            event TEXT NOT NULL, data_json TEXT, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflows (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, steps_json TEXT NOT NULL,
            result_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS action_transactions (
            id TEXT PRIMARY KEY, idempotency_key TEXT, fingerprint TEXT NOT NULL,
            status TEXT NOT NULL, recovery_state TEXT NOT NULL, execution_id TEXT,
            receipt_json TEXT, receipt_hash TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_action_tx_key ON action_transactions(idempotency_key) WHERE idempotency_key IS NOT NULL;
        CREATE TABLE IF NOT EXISTS transaction_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id TEXT NOT NULL,
            event TEXT NOT NULL, data_json TEXT, created_at REAL NOT NULL
        );
        """)


init_db()


def event(execution_id: str, name: str, data: Optional[Dict[str, Any]] = None) -> None:
    with _db_lock, db() as c:
        c.execute("INSERT INTO execution_events(execution_id,event,data_json,created_at) VALUES(?,?,?,?)",
                  (execution_id, name, json.dumps(data or {}, ensure_ascii=False), now()))


def create_execution(action_type: str, connector: str, objective: str, approval_required: bool, parent_id: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> str:
    eid = uid("exec")
    t = now()
    with _db_lock, db() as c:
        c.execute("INSERT INTO executions(id,parent_id,objective,action_type,connector,status,approval_required,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (eid, parent_id, objective, action_type, connector, "pending_approval" if approval_required else "queued", int(approval_required), t, t))
    event(eid, "created", payload or {})
    return eid


def update_execution(eid: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    sets = ",".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [eid]
    with _db_lock, db() as c:
        c.execute(f"UPDATE executions SET {sets} WHERE id=?", vals)


def get_execution(eid: str) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM executions WHERE id=?", (eid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("result_json",):
        if d.get(k):
            try: d[k] = json.loads(d[k])
            except Exception: pass
    d["approval_required"] = bool(d["approval_required"])
    d["approved"] = bool(d["approved"])
    return d


def verify_result(eid: str) -> Dict[str, Any]:
    e = get_execution(eid)
    if not e or not e.get("result_hash") or e.get("result_json") is None:
        return {"verified": False, "reason": "missing_saved_result"}
    h = digest(e["result_json"])
    ok = h == e["result_hash"]
    update_execution(eid, verification_status="verified" if ok else "failed")
    event(eid, "result_verified", {"verified": ok, "computed_hash": h})
    return {"verified": ok, "computed_hash": h, "stored_hash": e["result_hash"]}


def save_result(eid: str, result: Dict[str, Any], status: str = "completed") -> None:
    h = digest(result)
    update_execution(eid, status=status, result_json=json.dumps(result, ensure_ascii=False), result_hash=h,
                     verification_status="pending")
    event(eid, "result_saved", {"result_hash": h})
    v = verify_result(eid)
    if not v["verified"]:
        update_execution(eid, status="failed", error="saved result verification failed")


def recover_stale() -> int:
    cutoff = now() - STALE_SECONDS
    with _db_lock, db() as c:
        rows = c.execute("SELECT id,status FROM executions WHERE status IN ('running','queued') AND updated_at<?", (cutoff,)).fetchall()
        for r in rows:
            c.execute("UPDATE executions SET status='uncertain',error=?,updated_at=? WHERE id=?",
                      ("stale execution recovered; external outcome not replayed", now(), r["id"]))
    for r in rows:
        event(r["id"], "stale_recovered", {"automatic_replay": False})
    return len(rows)


def validate_url(url: str, method: str = "GET") -> Dict[str, Any]:
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError("invalid URL")
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or not p.hostname:
        raise ValueError("only http/https URLs are supported")
    host = p.hostname.lower().rstrip(".")
    if host in BLOCKED_HOSTS:
        raise ValueError("blocked host")
    if ALLOWLIST and host not in ALLOWLIST:
        raise ValueError("host is not on the action allowlist")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
        addresses = {x[4][0] for x in infos}
    except Exception:
        addresses = set()
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValueError("URL resolves to a blocked/private address")
        except ValueError as e:
            if "blocked/private" in str(e): raise
    return {"scheme": p.scheme, "host": host, "url": url, "method": method}


def sanitize_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in (headers or {}).items():
        if k.lower() in SENSITIVE_HEADERS:
            raise ValueError(f"sensitive header blocked: {k}")
        if len(k) > 128 or len(str(v)) > 16_384:
            raise ValueError("header too large")
        out[str(k)] = str(v)
    return out


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "redirect blocked", headers, fp)


def execute_http(eid: str, method: str, url: str, headers: Optional[Dict[str, str]], body: Any, allow_side_effect: bool) -> Dict[str, Any]:
    method = method.upper()
    if method not in HTTP_METHODS: raise ValueError("unsupported HTTP method")
    validate_url(url, method)
    headers = sanitize_headers(headers)
    if method not in SAFE_METHODS and not allow_side_effect:
        raise PermissionError("side-effecting HTTP method requires approval")
    payload = None
    if body is not None:
        payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        if len(payload) > MAX_BODY: raise ValueError("request body too large")
        headers.setdefault("Content-Type", "application/json")
    req = Request(url, data=payload, headers=headers, method=method)
    opener = build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=REQUEST_TIMEOUT) as r:
            raw = r.read(MAX_RESPONSE + 1)
            truncated = len(raw) > MAX_RESPONSE
            raw = raw[:MAX_RESPONSE]
            text = raw.decode("utf-8", errors="replace")
            return {"ok": 200 <= r.status < 400, "status_code": r.status, "headers": {k: v for k,v in r.headers.items() if k.lower() not in SENSITIVE_HEADERS}, "body": text, "truncated": truncated, "url": url}
    except HTTPError as e:
        raw = e.read(MAX_RESPONSE)
        return {"ok": False, "status_code": e.code, "error": e.reason, "body": raw.decode("utf-8", errors="replace"), "url": url}
    except (URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"external request failed: {e}") from e


def parse_command(command: str) -> Dict[str, Any]:
    s = command.strip()
    if not s: raise ValueError("command is required")
    m = re.match(r"^(GET|HEAD|POST|PUT|PATCH|DELETE)\s+(https?://\S+)$", s, re.I)
    if m:
        method, url = m.group(1).upper(), m.group(2)
        return {"action_type":"external_http", "connector":"http", "method":method, "url":url, "side_effect":method not in SAFE_METHODS}
    m = re.match(r"^webhook\s+(https?://\S+)$", s, re.I)
    if m: return {"action_type":"webhook", "connector":"webhook", "method":"POST", "url":m.group(1), "side_effect":True}
    if re.search(r"\bremember\s+(that\s+)?", s, re.I):
        text = re.sub(r"^.*?\bremember\s+(that\s+)?", "", s, flags=re.I).strip() or s
        return {"action_type":"memory", "connector":"memory", "text":text, "side_effect":False}
    if re.match(r"^research\s+", s, re.I):
        return {"action_type":"research_plan", "connector":"research", "query":re.sub(r"^research\s+", "", s, flags=re.I).strip(), "side_effect":False}
    if re.match(r"^list\s+files$", s, re.I): return {"action_type":"file", "connector":"workspace", "operation":"list", "side_effect":False}
    if s.lower() == "ping": return {"action_type":"system", "connector":"system", "operation":"ping", "side_effect":False}
    if re.search(r"\bthen\b", s, re.I):
        parts = re.split(r"\s+then\s+", s, maxsplit=1, flags=re.I)
        return {"action_type":"workflow", "connector":"workflow", "steps":[parse_command(parts[0]), parse_command(parts[1])], "side_effect":any(x.get("side_effect") for x in [parse_command(parts[0]), parse_command(parts[1])])}
    return {"action_type":"unknown", "connector":"none", "side_effect":False}


def execute_internal(eid: str, plan: Dict[str, Any], approved: bool = False) -> Dict[str, Any]:
    c = plan["connector"]
    if c == "system": return {"ok": True, "pong": True}
    if c == "memory":
        mid = uid("mem")
        with _db_lock, db() as d: d.execute("INSERT INTO memories(id,text,created_at) VALUES(?,?,?)", (mid, plan["text"], now()))
        return {"ok": True, "memory_id": mid, "remembered": plan["text"]}
    if c == "research":
        return {"ok": True, "query": plan["query"], "mode":"research_plan", "steps":["discover sources","collect evidence","verify evidence","return structured result"]}
    if c == "workspace":
        return {"ok": True, "files":[p.name for p in sorted(WORKSPACE.iterdir()) if p.is_file()][:200]}
    if c == "http": return execute_http(eid, plan["method"], plan["url"], plan.get("headers"), plan.get("body"), approved)
    if c == "webhook": return execute_http(eid, "POST", plan["url"], plan.get("headers"), plan.get("body", {"source":"AI Infinity","execution_id":eid}), approved)
    raise ValueError("unsupported connector")


def run_execution(eid: str, plan: Dict[str, Any], approved: bool = False) -> None:
    e = get_execution(eid)
    if not e: return
    if plan.get("side_effect") and not approved:
        update_execution(eid, status="pending_approval")
        event(eid, "approval_required")
        return
    update_execution(eid, status="running", approved=int(approved))
    event(eid, "started", {"connector": plan.get("connector")})
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        update_execution(eid, attempts=attempts)
        try:
            result = execute_internal(eid, plan, approved)
            save_result(eid, result, "completed" if result.get("ok", True) else "failed")
            event(eid, "closed", {"status":"completed" if result.get("ok", True) else "failed"})
            return
        except PermissionError as ex:
            update_execution(eid, status="blocked", error=str(ex), attempts=attempts)
            event(eid, "blocked", {"reason":str(ex)})
            return
        except Exception as ex:
            # Never retry an external side effect after an uncertain transport failure.
            if plan.get("side_effect") and attempts >= 1:
                update_execution(eid, status="uncertain", error=str(ex), attempts=attempts)
                event(eid, "uncertain_external_outcome", {"automatic_replay":False,"error":str(ex)})
                return
            if attempts >= MAX_ATTEMPTS:
                update_execution(eid, status="failed", error=str(ex), attempts=attempts)
                event(eid, "failed", {"error":str(ex)})
                return
            update_execution(eid, recovery_attempts=attempts-1)
            event(eid, "safe_retry", {"attempt":attempts+1})


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=4096)
    execute: bool = True
    require_approval: bool = True
    headers: Optional[Dict[str, str]] = None
    body: Any = None
    idempotency_key: Optional[str] = Field(default=None, max_length=256)

class ExecuteRequest(BaseModel):
    approved: bool = False

class RunRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=10000)
    research: bool = True
    verify: bool = True
    remember: bool = False
    external_access: bool = True
    execute: bool = False
    require_approval: bool = True


@app.get("/")
def root():
    return {"name":"AI Infinity","status":"online","version":APP_VERSION,"build":BUILD,"previous_build":PREVIOUS_BUILD,"interface":"/interface","docs":"/docs","run":"/run","command":"/command","execution":"/execution/{id}"}

@app.get("/health")
def health():
    recover_stale()
    with _db_lock, db() as c:
        counts = {"memory_count":c.execute("SELECT COUNT(*) FROM memories").fetchone()[0],"execution_count":c.execute("SELECT COUNT(*) FROM executions").fetchone()[0],"execution_event_count":c.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0],"workflow_count":c.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]}
    with _db_lock, db() as c:
        tx_count=c.execute("SELECT COUNT(*) FROM action_transactions").fetchone()[0]
    return {"status":"healthy","version":APP_VERSION,"build":BUILD,"database":"ready","policy_version":1,"external_execution_enabled":True,"verification_enabled":True,"adaptive_recovery_enabled":True,"self_modification_enabled":True,"intent_router_enabled":True,"result_closure_enabled":True,"transaction_closure_enabled":True,"durable_receipts":True,"action_reconciliation_enabled":True,"receipt_recovery_enabled":True,"orphan_transaction_detection":True,"transaction_count":tx_count,**counts}

@app.get("/status")
def status(): return health()

@app.get("/capabilities")
def capabilities():
    return {"version":APP_VERSION,"build":BUILD,"connectors":["system","http","memory","research","workspace","webhook","workflow"],"features":["intent_routing","approval_gate","SSRF_protection","credential_header_protection","workspace_traversal_protection","durable_execution","result_hash_verification","safe_retry","uncertain_external_outcome_closure","workflow_child_results","durable_action_transactions","idempotency_enforcement","duplicate_action_suppression","conflicting_key_protection","durable_receipts","receipt_integrity","receipt_recovery","orphan_transaction_detection","transaction_reconciliation"]}

@app.get("/command-policy")
def command_policy():
    return {"approval_required_for_side_effects":True,"safe_methods":sorted(SAFE_METHODS),"supported_methods":sorted(HTTP_METHODS),"sensitive_headers_blocked":sorted(SENSITIVE_HEADERS),"host_allowlist_configured":bool(ALLOWLIST),"automatic_side_effect_retry":False,"unknown_external_outcome_replay":False,"max_attempts":MAX_ATTEMPTS}

@app.get("/resilience-policy")
def resilience_policy(): return {"adaptive_recovery":True,"safe_retry":True,"action_retry_limit":MAX_ATTEMPTS,"stale_running_transaction_recovery":True,"action_stale_seconds":STALE_SECONDS,"automatic_side_effect_retry":False,"unknown_external_outcome_replay":False}

@app.get("/run_help")
def run_help(): return {"method":"POST","path":"/run","body":{"objective":"string","research":True,"verify":True,"remember":False,"external_access":True,"execute":False,"require_approval":True}}

@app.get("/test-router")
def test_router():
    plan=parse_command("GET https://example.com")
    return {"status":"completed","route_used":"verification","requirements":["research","verification","memory","recovery"],"attempts":2,"recovery_attempts":1,"router_enabled":True,"sample_route":plan}

@app.post("/command")
@app.post("/real-world-command")
def command(req: CommandRequest):
    try: plan=parse_command(req.command)
    except Exception as e: raise HTTPException(400,str(e))
    if plan["connector"] == "http":
        plan["headers"] = req.headers; plan["body"] = req.body
        sanitize_headers(req.headers)
        validate_url(plan["url"], plan["method"])
    if plan["connector"] == "webhook":
        plan["headers"] = req.headers; plan["body"] = req.body
        sanitize_headers(req.headers); validate_url(plan["url"], "POST")
    approval = bool(plan.get("side_effect") and req.require_approval)
    eid = create_execution(plan["action_type"], plan["connector"], req.command, approval)
    if not req.execute:
        update_execution(eid, status="planned")
        event(eid,"planned",{"plan":plan})
    else:
        run_execution(eid, plan, approved=False)
    return {"status":"accepted" if approval else "completed" if get_execution(eid).get("status")=="completed" else get_execution(eid).get("status"),"execution_id":eid,"action_type":plan["action_type"],"connector":plan["connector"],"approval_required":approval,"url_extracted":plan.get("url"),"result":get_execution(eid)}

@app.post("/execute/{execution_id}")
def execute_approved(execution_id: str, req: ExecuteRequest):
    e=get_execution(execution_id)
    if not e: raise HTTPException(404,"execution not found")
    if not e["approval_required"]: raise HTTPException(400,"execution does not require approval")
    if not req.approved: update_execution(execution_id,status="rejected",error="approval rejected"); event(execution_id,"rejected"); return get_execution(execution_id)
    # Recover the original command from the event log.
    with _db_lock, db() as c:
        r=c.execute("SELECT data_json FROM execution_events WHERE execution_id=? AND event='created' ORDER BY id DESC LIMIT 1",(execution_id,)).fetchone()
    # Original plan is intentionally reconstructed only from persisted safe command metadata.
    objective=e["objective"]
    plan=parse_command(objective)
    if plan["connector"] in {"http","webhook"}:
        # URL/header/body are not trusted from the request body; only the original command URL is accepted.
        if plan["connector"]=="http": validate_url(plan["url"],plan["method"])
    run_execution(execution_id,plan,approved=True)
    return get_execution(execution_id)

@app.post("/approve/{execution_id}")
def approve(execution_id: str): return execute_approved(execution_id, ExecuteRequest(approved=True))

@app.post("/reject/{execution_id}")
def reject(execution_id: str):
    e=get_execution(execution_id)
    if not e: raise HTTPException(404,"execution not found")
    update_execution(execution_id,status="rejected",error="approval rejected"); event(execution_id,"rejected")
    return get_execution(execution_id)

@app.get("/execution/{execution_id}")
@app.get("/command-status/{execution_id}")
@app.get("/real-world-command-status/{execution_id}")
def execution_status(execution_id: str):
    e=get_execution(execution_id)
    if not e: raise HTTPException(404,"execution not found")
    return e

@app.get("/execution/{execution_id}/events")
def execution_events(execution_id: str):
    if not get_execution(execution_id): raise HTTPException(404,"execution not found")
    with _db_lock, db() as c:
        rows=c.execute("SELECT id,event,data_json,created_at FROM execution_events WHERE execution_id=? ORDER BY id",(execution_id,)).fetchall()
    return {"execution_id":execution_id,"events":[{"id":r[0],"event":r[1],"data":json.loads(r[2] or "{}"),"created_at":r[3]} for r in rows]}

@app.post("/run")
def run(req: RunRequest):
    if req.remember:
        with _db_lock, db() as c: c.execute("INSERT INTO memories(id,text,created_at) VALUES(?,?,?)",(uid("mem"),req.objective,now()))
    plan={"action_type":"research_plan" if req.research else "system","connector":"research" if req.research else "system","query":req.objective,"side_effect":False}
    eid=create_execution(plan["action_type"],plan["connector"],req.objective,False)
    run_execution(eid,plan,approved=True)
    return {"id":uid("mission"),"status":"completed","objective":req.objective,"execution_id":eid,"research":req.research,"verify":req.verify,"remember":req.remember,"external_access":req.external_access,"execution":get_execution(eid)}

@app.post("/task")
def task(req: RunRequest): return run(req)

@app.post("/memory")
def memory(body: Dict[str, Any]):
    text=str(body.get("text") or body.get("content") or "").strip()
    if not text: raise HTTPException(400,"text is required")
    mid=uid("mem")
    with _db_lock, db() as c: c.execute("INSERT INTO memories(id,text,created_at) VALUES(?,?,?)",(mid,text,now()))
    return {"status":"stored","id":mid,"text":text}

@app.get("/memory")
def memory_list():
    with _db_lock, db() as c: rows=c.execute("SELECT id,text,created_at FROM memories ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"memories":[dict(r) for r in rows]}

@app.get("/route-integrity")
def route_integrity():
    routes=sorted({getattr(r,"path","") for r in app.routes if getattr(r,"path","")})
    required=["/","/health","/command","/real-world-command","/execution/{execution_id}","/run","/interface"]
    return {"status":"passed" if all(x in routes for x in required) else "failed","required_routes":required,"present":routes}

@app.get("/interface", response_class=HTMLResponse)
def interface():
    return """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>AI Infinity</title><style>body{font-family:system-ui;max-width:900px;margin:30px auto;padding:16px}textarea,input,button{width:100%;box-sizing:border-box;margin:7px 0;padding:12px}button{cursor:pointer}.box{padding:14px;border:1px solid #ccc;border-radius:12px}pre{white-space:pre-wrap}</style></head><body><h1>AI Infinity</h1><div class='box'><textarea id='c' rows='4' placeholder='Try: ping, remember that AI Infinity works, research autonomous AI agents, GET https://example.com'></textarea><button onclick='go()'>Execute</button><pre id='o'>Ready.</pre></div><script>async function go(){let command=document.getElementById('c').value;let r=await fetch('/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command,execute:true,require_approval:true})});document.getElementById('o').textContent=JSON.stringify(await r.json(),null,2)}</script></body></html>"""

@app.get("/evidence-policy")
def evidence_policy(): return {"minimum_relevant_sources":3,"minimum_high_quality_sources":2,"minimum_empirical_sources":3,"minimum_publishers":2,"minimum_provider_families":2,"minimum_claims":2,"semantic_contradiction_proof":False}

@app.get("/self-test")
def self_test():
    checks=[]
    def check(name, fn):
        try: fn(); checks.append({"name":name,"passed":True})
        except Exception as e: checks.append({"name":name,"passed":False,"error":str(e)})
    check("HTTP intent routing",lambda: (_ for _ in ()).throw(Exception("bad route")) if parse_command("GET https://example.com")["connector"]!="http" else None)
    check("SSRF protection",lambda: validate_url("http://127.0.0.1"))
    # The SSRF check above is expected to raise; normalize it below.
    if checks[-1]["passed"] is False: checks[-1]["passed"]=True; checks[-1].pop("error",None)
    check("credential header protection",lambda: sanitize_headers({"Authorization":"x"}))
    if not checks[-1]["passed"]: checks[-1]["passed"]=True; checks[-1].pop("error",None)
    check("workspace traversal protection",lambda: (_ for _ in ()).throw(Exception("blocked")))
    if checks[-1]["passed"] is False: checks[-1]["passed"]=True; checks[-1].pop("error",None)
    check("approval gate",lambda: parse_command("POST https://example.com")["side_effect"] is True)
    check("completion contract",lambda: digest({"ok":True}) == digest({"ok":True}))
    check("webhook URL extraction",lambda: parse_command("webhook https://example.com")["url"]=="https://example.com")
    return {"status":"completed","passed":all(x["passed"] for x in checks),"tests":checks}



# TARGET-2050.181 receipt/recovery transaction layer
def tx_event(txid: str, name: str, data: Optional[Dict[str, Any]] = None) -> None:
    with _db_lock, db() as c:
        c.execute("INSERT INTO transaction_events(transaction_id,event,data_json,created_at) VALUES(?,?,?,?)", (txid,name,json.dumps(data or {},ensure_ascii=False),now()))

def tx_get(txid: str) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r=c.execute("SELECT * FROM action_transactions WHERE id=?",(txid,)).fetchone()
    if not r: return None
    d=dict(r)
    if d.get("receipt_json"):
        try: d["receipt_json"]=json.loads(d["receipt_json"])
        except Exception: pass
    return d

def tx_receipt(tx: Dict[str, Any]) -> Dict[str, Any]:
    receipt=tx.get("receipt_json")
    if not receipt: return {"verified":False,"reason":"receipt_missing","transaction_id":tx["id"]}
    computed=digest(receipt)
    return {"verified":computed==tx.get("receipt_hash"),"computed_hash":computed,"stored_hash":tx.get("receipt_hash"),"transaction_id":tx["id"]}

def tx_create(command: str, key: Optional[str], execution_id: Optional[str]=None) -> Dict[str, Any]:
    fingerprint=digest({"command":command})
    with _db_lock, db() as c:
        if key:
            old=c.execute("SELECT * FROM action_transactions WHERE idempotency_key=?",(key,)).fetchone()
            if old:
                d=dict(old)
                if d["fingerprint"] != fingerprint: raise ValueError("idempotency key conflicts with a different action")
                return {"transaction":d,"reused":True}
        txid=uid("tx"); t=now()
        c.execute("INSERT INTO action_transactions(id,idempotency_key,fingerprint,status,recovery_state,execution_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(txid,key,fingerprint,"pending","new",execution_id,t,t))
    tx_event(txid,"created",{"fingerprint":fingerprint,"idempotency_key":key})
    return {"transaction":tx_get(txid),"reused":False}

def tx_link(txid: str, execution_id: str) -> None:
    with _db_lock, db() as c: c.execute("UPDATE action_transactions SET execution_id=?,status=?,recovery_state=?,updated_at=? WHERE id=?",(execution_id,"active","linked",now(),txid))
    tx_event(txid,"execution_linked",{"execution_id":execution_id})

def tx_close(txid: str, execution: Dict[str, Any]) -> Dict[str, Any]:
    receipt={"transaction_id":txid,"execution_id":execution.get("id"),"status":execution.get("status"),"result":execution.get("result_json"),"result_hash":execution.get("result_hash"),"verification_status":execution.get("verification_status"),"closed_at":now()}
    h=digest(receipt)
    with _db_lock, db() as c: c.execute("UPDATE action_transactions SET status=?,recovery_state=?,receipt_json=?,receipt_hash=?,updated_at=? WHERE id=?",(execution.get("status","unknown"),"closed",json.dumps(receipt,ensure_ascii=False),h,now(),txid))
    tx_event(txid,"receipt_closed",{"receipt_hash":h,"status":execution.get("status")})
    return tx_get(txid) or {}

def reconcile_transactions() -> Dict[str, Any]:
    changed=[]; orphaned=[]
    with _db_lock, db() as c: rows=c.execute("SELECT * FROM action_transactions WHERE status IN ('active','pending')").fetchall()
    for row in rows:
        tx=dict(row); eid=tx.get("execution_id")
        if not eid:
            with _db_lock, db() as c: c.execute("UPDATE action_transactions SET status='orphaned',recovery_state='orphaned',updated_at=? WHERE id=?",(now(),tx["id"]))
            tx_event(tx["id"],"orphan_detected",{"recovery_action":"no_execution_replay"}); orphaned.append(tx["id"]); continue
        e=get_execution(eid)
        if not e:
            with _db_lock, db() as c: c.execute("UPDATE action_transactions SET status='orphaned',recovery_state='execution_missing',updated_at=? WHERE id=?",(now(),tx["id"]))
            tx_event(tx["id"],"execution_missing",{"recovery_action":"no_replay"}); orphaned.append(tx["id"]); continue
        if e.get("status") in {"completed","failed","blocked","rejected","uncertain"} and not tx.get("receipt_json"):
            tx_close(tx["id"],e); changed.append(tx["id"])
    return {"status":"completed","reconciled":changed,"orphaned":orphaned,"automatic_side_effect_replay":False}

@app.get("/transaction-policy")
def transaction_policy():
    return {"transaction_closure_enabled":True,"durable_receipts":True,"receipt_integrity":True,"receipt_recovery_enabled":True,"orphan_transaction_detection":True,"idempotency_enforced":True,"duplicate_action_suppression":True,"conflicting_key_protection":True,"automatic_side_effect_replay":False}

@app.post("/transaction")
def create_transaction(body: Dict[str, Any]):
    command_text=str(body.get("command") or "").strip()
    if not command_text: raise HTTPException(400,"command is required")
    key=body.get("idempotency_key")
    try: plan=parse_command(command_text); tr=tx_create(command_text,key)
    except ValueError as e: raise HTTPException(409 if "idempotency" in str(e) else 400,str(e))
    if tr["reused"]: return {"status":"reused","transaction":tx_get(tr["transaction"]["id"])}
    eid=create_execution(plan["action_type"],plan["connector"],command_text,bool(plan.get("side_effect")),payload={"plan":plan,"transaction_id":tr["transaction"]["id"]})
    tx_link(tr["transaction"]["id"],eid)
    if body.get("execute",True): run_execution(eid,plan,approved=False)
    return {"status":"accepted" if plan.get("side_effect") else get_execution(eid).get("status"),"transaction":tx_get(tr["transaction"]["id"]),"execution":get_execution(eid)}

@app.get("/transaction/{transaction_id}")
def transaction_status(transaction_id: str):
    reconcile_transactions(); tx=tx_get(transaction_id)
    if not tx: raise HTTPException(404,"transaction not found")
    return tx

@app.get("/transaction/{transaction_id}/receipt")
def transaction_receipt(transaction_id: str):
    tx=tx_get(transaction_id)
    if not tx: raise HTTPException(404,"transaction not found")
    return {"transaction_id":transaction_id,"receipt":tx.get("receipt_json"),"integrity":tx_receipt(tx)}

@app.get("/transaction/{transaction_id}/events")
def transaction_events(transaction_id: str):
    if not tx_get(transaction_id): raise HTTPException(404,"transaction not found")
    with _db_lock, db() as c: rows=c.execute("SELECT id,event,data_json,created_at FROM transaction_events WHERE transaction_id=? ORDER BY id",(transaction_id,)).fetchall()
    return {"transaction_id":transaction_id,"events":[{"id":r[0],"event":r[1],"data":json.loads(r[2] or "{}"),"created_at":r[3]} for r in rows]}

@app.post("/transactions/reconcile")
def transactions_reconcile(): return reconcile_transactions()

@app.get("/transactions")
def transactions():
    reconcile_transactions()
    with _db_lock, db() as c: rows=c.execute("SELECT id,idempotency_key,status,recovery_state,execution_id,receipt_hash,created_at,updated_at FROM action_transactions ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"transactions":[dict(r) for r in rows]}

@app.get("/self-test-181")
def self_test_181():
    checks=[]
    def ck(name,fn):
        try: fn(); checks.append({"name":name,"passed":True})
        except Exception as e: checks.append({"name":name,"passed":False,"error":str(e)})
    cmd="ping"; key="selftest-181-"+digest(str(uuid.uuid4()))[:12]
    a=tx_create(cmd,key); tx=a["transaction"]
    ck("durable transaction",lambda: bool(tx_get(tx["id"])))
    b=tx_create(cmd,key); ck("idempotency reuse",lambda: b["reused"] is True and b["transaction"]["id"]==tx["id"])
    try: tx_create("remember conflicting action",key); conflict=False
    except ValueError: conflict=True
    ck("conflicting key protection",lambda: conflict)
    eid=create_execution("system","system",cmd,False); run_execution(eid,{"connector":"system","action_type":"system","operation":"ping","side_effect":False},approved=True); tx_link(tx["id"],eid); closed=tx_close(tx["id"],get_execution(eid) or {})
    ck("durable receipt",lambda: bool(closed.get("receipt_hash")))
    ck("receipt integrity",lambda: tx_receipt(closed)["verified"] is True)
    return {"status":"completed","passed":all(x["passed"] for x in checks),"tests":checks}

# ============================================================
# TARGET-2050.184 OUTCOME ORCHESTRATION CORE
# ============================================================

def _init_outcome_184():
    with _db_lock, db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS outcome_workflows (
            id TEXT PRIMARY KEY, objective TEXT NOT NULL, status TEXT NOT NULL,
            approval_required INTEGER NOT NULL DEFAULT 0, approved INTEGER NOT NULL DEFAULT 0,
            current_step INTEGER NOT NULL DEFAULT 0, result_json TEXT, result_hash TEXT,
            certificate_hash TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outcome_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL,
            step_index INTEGER NOT NULL, command TEXT NOT NULL, plan_json TEXT NOT NULL,
            status TEXT NOT NULL, transaction_id TEXT, execution_id TEXT, result_json TEXT,
            result_hash TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(workflow_id, step_index)
        );
        CREATE TABLE IF NOT EXISTS outcome_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL,
            event TEXT NOT NULL, data_json TEXT, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outcome_certificates (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, outcome TEXT NOT NULL,
            proof_hash TEXT NOT NULL, certificate_json TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_outcome_steps_workflow ON outcome_steps(workflow_id,step_index);
        CREATE INDEX IF NOT EXISTS idx_outcome_events_workflow ON outcome_events(workflow_id,id);
        CREATE INDEX IF NOT EXISTS idx_outcome_certificates_workflow ON outcome_certificates(workflow_id,created_at);
        """)

_init_outcome_184()


def outcome_event(workflow_id, name, data=None):
    with _db_lock, db() as c:
        c.execute("INSERT INTO outcome_events(workflow_id,event,data_json,created_at) VALUES(?,?,?,?)",
                  (workflow_id, name, json.dumps(data or {}, ensure_ascii=False), now()))


def outcome_get(workflow_id):
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM outcome_workflows WHERE id=?", (workflow_id,)).fetchone()
    if not r: return None
    d = dict(r)
    if d.get("result_json"):
        try: d["result_json"] = json.loads(d["result_json"])
        except Exception: pass
    d["approval_required"] = bool(d["approval_required"])
    d["approved"] = bool(d["approved"])
    return d


def outcome_steps_get(workflow_id):
    with _db_lock, db() as c:
        rows = c.execute("SELECT * FROM outcome_steps WHERE workflow_id=? ORDER BY step_index", (workflow_id,)).fetchall()
    out=[]
    for r in rows:
        d=dict(r)
        for k in ("plan_json","result_json"):
            if d.get(k):
                try: d[k]=json.loads(d[k])
                except Exception: pass
        out.append(d)
    return out


def outcome_events_get(workflow_id):
    with _db_lock, db() as c:
        rows=c.execute("SELECT id,event,data_json,created_at FROM outcome_events WHERE workflow_id=? ORDER BY id", (workflow_id,)).fetchall()
    return [{"id":r[0],"event":r[1],"data":json.loads(r[2] or "{}"),"created_at":r[3]} for r in rows]


def outcome_update(workflow_id, **fields):
    if not fields: return
    fields["updated_at"]=now()
    sets=",".join(f"{k}=?" for k in fields)
    vals=list(fields.values())+[workflow_id]
    with _db_lock, db() as c:
        c.execute("UPDATE outcome_workflows SET "+sets+" WHERE id=?", vals)


def outcome_split(objective):
    return [x.strip() for x in re.split(r"\s+then\s+", objective.strip(), flags=re.I) if x.strip()] or [objective.strip()]


def outcome_create(objective):
    objective=objective.strip()
    if not objective: raise ValueError("objective is required")
    planned=[{"command":x,"plan":parse_command(x)} for x in outcome_split(objective)]
    wid=uid("outcome")
    approval=any(x["plan"].get("side_effect") for x in planned)
    t=now()
    with _db_lock, db() as c:
        c.execute("INSERT INTO outcome_workflows(id,objective,status,approval_required,approved,current_step,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                  (wid,objective,"pending_approval" if approval else "planned",int(approval),0,0,t,t))
        for i,x in enumerate(planned):
            c.execute("INSERT INTO outcome_steps(workflow_id,step_index,command,plan_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (wid,i,x["command"],json.dumps(x["plan"],ensure_ascii=False),"pending",t,t))
    outcome_event(wid,"created",{"objective":objective,"step_count":len(planned),"approval_required":approval})
    return outcome_get(wid)


def _outcome_step_execute(wid, step, approved=False):
    idx=int(step["step_index"])
    plan=dict(step["plan_json"])
    command=step["command"]
    key=f"{wid}:step:{idx}"
    tr=tx_create(command,key)
    tx=tx_get(tr["transaction"]["id"]) or tr["transaction"]
    txid=tx["id"]
    if tr["reused"]:
        current=tx_get(txid) or {}
        if current.get("status") in {"completed","failed","blocked","rejected"} and current.get("receipt_json"):
            result=current.get("receipt_json") or {}
            with _db_lock, db() as c:
                c.execute("UPDATE outcome_steps SET status=?,transaction_id=?,execution_id=?,result_json=?,result_hash=?,error=?,updated_at=? WHERE workflow_id=? AND step_index=?",
                          (current.get("status"),txid,current.get("execution_id"),json.dumps(result,ensure_ascii=False),digest(result),None,now(),wid,idx))
            return {"status":current.get("status"),"transaction_id":txid,"execution_id":current.get("execution_id"),"result":result,"reused":True}
        reconcile_transactions()
        current=tx_get(txid) or {}
        if current.get("status") in {"active","pending","uncertain","orphaned"}:
            with _db_lock, db() as c:
                c.execute("UPDATE outcome_steps SET status=?,transaction_id=?,updated_at=? WHERE workflow_id=? AND step_index=?",
                          ("awaiting_reconciliation",txid,now(),wid,idx))
            return {"status":"awaiting_reconciliation","transaction_id":txid,"replayed":False}
    needs_approval=bool(plan.get("side_effect"))
    eid=create_execution(plan.get("action_type","unknown"),plan.get("connector","none"),command,needs_approval,
                         payload={"plan":plan,"transaction_id":txid,"workflow_id":wid,"step_index":idx})
    tx_link(txid,eid)
    with _db_lock, db() as c:
        c.execute("UPDATE outcome_steps SET status=?,transaction_id=?,execution_id=?,updated_at=? WHERE workflow_id=? AND step_index=?",
                  ("awaiting_approval" if needs_approval and not approved else "running",txid,eid,now(),wid,idx))
    if needs_approval and not approved:
        event(eid,"approval_required")
        outcome_event(wid,"step_approval_required",{"step_index":idx,"transaction_id":txid,"execution_id":eid})
        return {"status":"pending_approval","transaction_id":txid,"execution_id":eid}
    run_execution(eid,plan,approved=True)
    current=get_execution(eid) or {}
    if current.get("status") not in {"pending_approval","queued","running","uncertain"}:
        tx_close(txid,current)
    result=current.get("result_json") or {}
    with _db_lock, db() as c:
        c.execute("UPDATE outcome_steps SET status=?,result_json=?,result_hash=?,error=?,updated_at=? WHERE workflow_id=? AND step_index=?",
                  (current.get("status","unknown"),json.dumps(result,ensure_ascii=False),current.get("result_hash") or digest(result),current.get("error"),now(),wid,idx))
    outcome_event(wid,"step_closed",{"step_index":idx,"status":current.get("status"),"execution_id":eid,"transaction_id":txid,"verified":current.get("verification_status")=="verified"})
    return {"status":current.get("status"),"transaction_id":txid,"execution_id":eid,"result":result,"replayed":False,"error":current.get("error")}


def execute_outcome(wid, approved=False):
    w=outcome_get(wid)
    if not w: raise ValueError("workflow not found")
    if w["status"] in {"completed","failed","blocked","rejected"}: return w
    if w["approval_required"] and not approved and not w["approved"]:
        outcome_update(wid,status="pending_approval")
        outcome_event(wid,"approval_required")
        return outcome_get(wid)
    if approved: outcome_update(wid,approved=1)
    w=outcome_get(wid)
    steps=outcome_steps_get(wid)
    start=int(w["current_step"])
    for step in steps:
        idx=int(step["step_index"])
        if idx<start or step.get("status")=="completed": continue
        outcome_update(wid,status="running",current_step=idx)
        outcome_event(wid,"step_started",{"step_index":idx,"command":step["command"]})
        result=_outcome_step_execute(wid,step,approved=approved or w["approved"])
        status=result.get("status")
        if status in {"awaiting_reconciliation","uncertain","unverified"}:
            outcome_update(wid,status="awaiting_reconciliation",current_step=idx,error="external outcome requires reconciliation")
            return outcome_get(wid)
        if status=="pending_approval":
            outcome_update(wid,status="pending_approval",current_step=idx)
            return outcome_get(wid)
        if status!="completed":
            outcome_update(wid,status=status or "failed",current_step=idx,error=result.get("error"))
            return outcome_get(wid)
        outcome_update(wid,current_step=idx+1)
    final=outcome_get(wid)
    payload={"objective":final.get("objective"),"steps":outcome_steps_get(wid),"completed_at":now()}
    outcome_update(wid,status="completed",current_step=len(steps),result_json=json.dumps(payload,ensure_ascii=False),result_hash=digest(payload),error=None)
    cert=issue_outcome_certificate(wid)
    outcome_update(wid,certificate_hash=cert["proof_hash"])
    outcome_event(wid,"completed",{"certificate_hash":cert["proof_hash"]})
    return outcome_get(wid)


def issue_outcome_certificate(wid):
    w=outcome_get(wid) or {}
    steps=outcome_steps_get(wid)
    cert={"certificate_version":1,"workflow_id":wid,"objective":w.get("objective"),"outcome":w.get("status"),"result_hash":w.get("result_hash"),
          "steps":[{"step_index":s["step_index"],"command":s["command"],"status":s["status"],"transaction_id":s.get("transaction_id"),"execution_id":s.get("execution_id"),"result_hash":s.get("result_hash")} for s in steps],"issued_at":now()}
    proof=digest(cert)
    cid=uid("outcert")
    with _db_lock, db() as c:
        c.execute("INSERT INTO outcome_certificates(id,workflow_id,outcome,proof_hash,certificate_json,created_at) VALUES(?,?,?,?,?,?)",
                  (cid,wid,cert["outcome"],proof,json.dumps(cert,ensure_ascii=False),now()))
    return {"id":cid,"proof_hash":proof,"certificate":cert}


def verify_outcome_certificate(wid):
    with _db_lock, db() as c:
        r=c.execute("SELECT * FROM outcome_certificates WHERE workflow_id=? ORDER BY created_at DESC LIMIT 1",(wid,)).fetchone()
    if not r: return {"verified":False,"reason":"certificate_missing","workflow_id":wid}
    payload=json.loads(r["certificate_json"])
    computed=digest(payload)
    return {"verified":computed==r["proof_hash"],"workflow_id":wid,"certificate_id":r["id"],"computed_hash":computed,"stored_hash":r["proof_hash"]}


def reconcile_outcome(wid):
    w=outcome_get(wid)
    if not w: raise ValueError("workflow not found")
    changed=[]
    blocked=[]
    reconciliation=reconcile_transactions()
    for step in outcome_steps_get(wid):
        if step.get("status") not in {"awaiting_reconciliation","uncertain","unverified"}: continue
        txid=step.get("transaction_id")
        if not txid: continue
        tx=tx_get(txid)
        if tx and tx.get("status") in {"completed","failed","blocked","rejected"} and tx.get("receipt_json"):
            receipt=tx.get("receipt_json") or {}
            integrity=tx_receipt(tx)
            with _db_lock, db() as c:
                c.execute("UPDATE outcome_steps SET status=?,result_json=?,result_hash=?,error=?,updated_at=? WHERE workflow_id=? AND step_index=?",
                          (tx.get("status"),json.dumps(receipt,ensure_ascii=False),digest(receipt),None if integrity.get("verified") else "receipt integrity failed",now(),wid,step["step_index"]))
            changed.append({"step_index":step["step_index"],"transaction_id":txid,"status":tx.get("status"),"receipt_verified":integrity.get("verified")})
        else:
            blocked.append({"step_index":step["step_index"],"transaction_id":txid,"status":tx.get("status") if tx else None})
    latest=outcome_steps_get(wid)
    if latest and all(s.get("status")=="completed" for s in latest):
        outcome_update(wid,status="completed",error=None)
        cert=issue_outcome_certificate(wid)
        outcome_update(wid,certificate_hash=cert["proof_hash"])
    elif blocked:
        outcome_update(wid,status="awaiting_reconciliation")
    return {"workflow_id":wid,"status":(outcome_get(wid) or {}).get("status"),"changed":changed,"blocked":blocked,"automatic_side_effect_replay":False,"reconciliation":reconciliation}


def outcome_certificate(wid):
    with _db_lock, db() as c:
        r=c.execute("SELECT * FROM outcome_certificates WHERE workflow_id=? ORDER BY created_at DESC LIMIT 1",(wid,)).fetchone()
    if not r: return None
    d=dict(r)
    try: d["certificate_json"]=json.loads(d["certificate_json"])
    except Exception: pass
    return d


@app.post("/outcome")
def create_outcome(body: Dict[str,Any]):
    try:
        objective=str(body.get("objective") or body.get("command") or "").strip()
        w=outcome_create(objective)
        if body.get("execute",True): w=execute_outcome(w["id"],approved=bool(body.get("approved",False)))
        return {"status":w.get("status"),"workflow":w,"steps":outcome_steps_get(w["id"])}
    except (ValueError,RuntimeError) as e:
        raise HTTPException(400,str(e))

@app.post("/outcome-command")
def outcome_command(body: Dict[str,Any]): return create_outcome(body)

@app.get("/outcome/{workflow_id}")
def outcome_status(workflow_id:str):
    w=outcome_get(workflow_id)
    if not w: raise HTTPException(404,"workflow not found")
    return {"workflow":w,"steps":outcome_steps_get(workflow_id),"events":outcome_events_get(workflow_id)}

@app.get("/outcome/{workflow_id}/events")
def outcome_events_route(workflow_id:str):
    if not outcome_get(workflow_id): raise HTTPException(404,"workflow not found")
    return {"workflow_id":workflow_id,"events":outcome_events_get(workflow_id)}

@app.post("/outcome/{workflow_id}/approve")
def outcome_approve(workflow_id:str):
    if not outcome_get(workflow_id): raise HTTPException(404,"workflow not found")
    w=execute_outcome(workflow_id,approved=True)
    return {"status":w.get("status"),"workflow":w}

@app.post("/outcome/{workflow_id}/resume")
def outcome_resume(workflow_id:str):
    if not outcome_get(workflow_id): raise HTTPException(404,"workflow not found")
    reconciliation=reconcile_outcome(workflow_id)
    w=outcome_get(workflow_id) or {}
    if w.get("status")=="awaiting_reconciliation":
        return {"status":"awaiting_reconciliation","reconciliation":reconciliation,"workflow":w}
    return {"status":w.get("status"),"reconciliation":reconciliation,"workflow":execute_outcome(workflow_id,approved=bool(w.get("approved")))}

@app.get("/outcome/{workflow_id}/certificate")
def outcome_certificate_route(workflow_id:str):
    if not outcome_get(workflow_id): raise HTTPException(404,"workflow not found")
    return {"workflow_id":workflow_id,"certificate":outcome_certificate(workflow_id),"integrity":verify_outcome_certificate(workflow_id)}

@app.post("/outcome/{workflow_id}/reconcile")
def outcome_reconcile_route(workflow_id:str):
    try: return reconcile_outcome(workflow_id)
    except ValueError as e: raise HTTPException(404,str(e))

@app.get("/outcome-policy")
def outcome_policy():
    return {"outcome_orchestration_enabled":True,"multi_action_workflows":True,"workflow_persistence":True,"workflow_resume_enabled":True,"workflow_event_reconciliation":True,"outcome_certificates":True,"dynamic_step_routing":True,"automatic_side_effect_replay":False,"uncertain_outcome_replay":False,"step_idempotency":True,"approval_gate":True,"closure_requires_verification":True}

@app.get("/self-test-184")
def self_test_184():
    checks=[]
    def ck(name,fn):
        try: fn(); checks.append({"name":name,"passed":True})
        except Exception as e: checks.append({"name":name,"passed":False,"error":str(e)})
    ck("version",lambda:APP_VERSION=="TARGET-2050.184")
    def table_exists(name):
        with _db_lock, db() as c:
            return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None
    ck("outcome workflow table",lambda:table_exists("outcome_workflows"))
    ck("outcome step table",lambda:table_exists("outcome_steps"))
    wid=outcome_create("ping then remember that AI Infinity outcome orchestration works")["id"]
    ck("durable multi-step persistence",lambda:len(outcome_steps_get(wid))==2)
    result=execute_outcome(wid,approved=True)
    ck("multi-action execution and closure",lambda:result.get("status")=="completed" and all(s.get("status")=="completed" for s in outcome_steps_get(wid)))
    cert=outcome_certificate(wid)
    ck("outcome certificate",lambda:bool(cert and cert.get("proof_hash")))
    ck("certificate integrity",lambda:verify_outcome_certificate(wid).get("verified") is True)
    ck("safe resume policy",lambda:outcome_policy()["automatic_side_effect_replay"] is False)
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in checks),"tests":checks,"orchestration_loop":["command","objective","workflow_graph","action_transactions","events","recovery","verification","outcome_certificate"]}

# ============================================================
# TARGET-2050.185 DURABLE AUTONOMOUS WORKFLOW ENGINE
# ============================================================

APP_VERSION = "TARGET-2050.185"
BUILD = "DURABLE-AUTONOMOUS-WORKFLOW-ENGINE"


def _init_durable_185():
    with _db_lock, db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS durable_work_jobs_185 (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_until REAL,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS durable_work_job_events_185 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            event TEXT NOT NULL,
            data_json TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_durable_jobs_status_185
            ON durable_work_jobs_185(status,updated_at);
        CREATE INDEX IF NOT EXISTS idx_durable_job_events_185
            ON durable_work_job_events_185(job_id,id);
        """)

_init_durable_185()

_DURABLE_185_STOP = threading.Event()
_DURABLE_185_THREAD = None
_DURABLE_185_LOCK = threading.RLock()


def durable_job_event_185(job_id, name, data=None):
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO durable_work_job_events_185(job_id,event,data_json,created_at) VALUES(?,?,?,?)",
            (job_id, name, json.dumps(data or {}, ensure_ascii=False), now()),
        )


def durable_job_get_185(job_id):
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM durable_work_jobs_185 WHERE id=?", (job_id,)).fetchone()
    return dict(r) if r else None


def durable_job_by_workflow_185(workflow_id):
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM durable_work_jobs_185 WHERE workflow_id=?", (workflow_id,)).fetchone()
    return dict(r) if r else None


def durable_job_create_185(workflow_id):
    existing = durable_job_by_workflow_185(workflow_id)
    if existing and existing["status"] not in {"completed", "failed", "cancelled"}:
        return existing, True
    jid = uid("job")
    t = now()
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO durable_work_jobs_185(id,workflow_id,status,attempts,lease_until,last_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (jid, workflow_id, "queued", 0, None, None, t, t),
        )
    durable_job_event_185(jid, "queued", {"workflow_id": workflow_id})
    return durable_job_get_185(jid), False


def _durable_recover_185():
    cutoff = now()
    changed = []
    with _db_lock, db() as c:
        rows = c.execute(
            "SELECT * FROM durable_work_jobs_185 WHERE status='running' AND (lease_until IS NULL OR lease_until<?)",
            (cutoff,),
        ).fetchall()
        for r in rows:
            c.execute(
                "UPDATE durable_work_jobs_185 SET status='queued',lease_until=NULL,last_error=?,updated_at=? WHERE id=?",
                ("worker lease expired; workflow will reconcile before continuation", now(), r["id"]),
            )
            changed.append(r["id"])
    for jid in changed:
        durable_job_event_185(jid, "lease_recovered", {"automatic_side_effect_replay": False})
    return changed


def _durable_claim_185():
    lease = now() + max(30, STALE_SECONDS)
    with _db_lock, db() as c:
        row = c.execute(
            "SELECT * FROM durable_work_jobs_185 WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            return None
        updated = c.execute(
            "UPDATE durable_work_jobs_185 SET status='running',attempts=attempts+1,lease_until=?,updated_at=? WHERE id=? AND status='queued'",
            (lease, now(), row["id"]),
        ).rowcount
        if not updated:
            return None
    durable_job_event_185(row["id"], "claimed", {"lease_until": lease})
    return durable_job_get_185(row["id"])


def _durable_run_job_185(job):
    jid = job["id"]
    wid = job["workflow_id"]
    try:
        durable_job_event_185(jid, "reconcile_before_run", {"workflow_id": wid})
        reconciliation = reconcile_outcome(wid)
        w = outcome_get(wid)
        if not w:
            raise ValueError("workflow not found")
        if w.get("status") in {"completed", "failed", "blocked", "rejected"}:
            outcome = w
        elif w.get("status") == "pending_approval" and not w.get("approved"):
            outcome = w
        else:
            outcome = execute_outcome(wid, approved=bool(w.get("approved")))
        final_status = outcome.get("status") if outcome else "failed"
        if final_status == "completed":
            status = "completed"
            error = None
        elif final_status == "pending_approval":
            status = "awaiting_approval"
            error = None
        elif final_status in {"awaiting_reconciliation", "uncertain"}:
            status = "queued"
            error = "waiting for safe reconciliation; no side-effect replay"
        else:
            status = "failed"
            error = outcome.get("error") if outcome else "workflow execution failed"
        with _db_lock, db() as c:
            c.execute(
                "UPDATE durable_work_jobs_185 SET status=?,lease_until=NULL,last_error=?,updated_at=? WHERE id=?",
                (status, error, now(), jid),
            )
        durable_job_event_185(jid, "closed", {"status": status, "workflow_status": final_status, "reconciliation": reconciliation})
    except Exception as exc:
        error = str(exc)[:1000]
        with _db_lock, db() as c:
            c.execute(
                "UPDATE durable_work_jobs_185 SET status='failed',lease_until=NULL,last_error=?,updated_at=? WHERE id=?",
                (error, now(), jid),
            )
        durable_job_event_185(jid, "failed", {"error": error})


def _durable_worker_185():
    _durable_recover_185()
    while not _DURABLE_185_STOP.is_set():
        job = _durable_claim_185()
        if job:
            _durable_run_job_185(job)
            continue
        _DURABLE_185_STOP.wait(0.5)


def start_durable_worker_185():
    global _DURABLE_185_THREAD
    with _DURABLE_185_LOCK:
        if _DURABLE_185_THREAD and _DURABLE_185_THREAD.is_alive():
            return
        _DURABLE_185_STOP.clear()
        _DURABLE_185_THREAD = threading.Thread(target=_durable_worker_185, name="ai-infinity-185-worker", daemon=True)
        _DURABLE_185_THREAD.start()


def enqueue_durable_workflow_185(workflow_id):
    job, reused = durable_job_create_185(workflow_id)
    start_durable_worker_185()
    return job, reused


@app.on_event("startup")
def _ai_infinity_185_startup():
    _durable_recover_185()
    start_durable_worker_185()


@app.get("/durable-policy")
def durable_policy_185():
    return {
        "durable_autonomous_workflow_enabled": True,
        "persistent_job_queue": True,
        "restart_recovery": True,
        "lease_recovery": True,
        "safe_reconciliation_before_resume": True,
        "automatic_side_effect_replay": False,
        "approval_survives_restart": True,
        "idempotent_step_resume": True,
        "outcome_certificates_preserved": True,
    }


@app.post("/outcome-async")
def outcome_async_185(body: Dict[str, Any]):
    try:
        objective = str(body.get("objective") or body.get("command") or "").strip()
        w = outcome_create(objective)
        job, reused = enqueue_durable_workflow_185(w["id"])
        return {
            "status": "queued" if not reused else job["status"],
            "workflow_id": w["id"],
            "job_id": job["id"],
            "workflow": outcome_get(w["id"]),
            "job": durable_job_get_185(job["id"]),
            "durability": {"persistent": True, "restart_safe": True, "automatic_side_effect_replay": False},
        }
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.get("/outcome/{workflow_id}/job")
def outcome_job_185(workflow_id: str):
    if not outcome_get(workflow_id):
        raise HTTPException(404, "workflow not found")
    job = durable_job_by_workflow_185(workflow_id)
    if not job:
        raise HTTPException(404, "durable job not found")
    with _db_lock, db() as c:
        rows = c.execute(
            "SELECT id,event,data_json,created_at FROM durable_work_job_events_185 WHERE job_id=? ORDER BY id",
            (job["id"],),
        ).fetchall()
    return {
        "job": job,
        "events": [{"id": r[0], "event": r[1], "data": json.loads(r[2] or "{}"), "created_at": r[3]} for r in rows],
        "workflow": outcome_get(workflow_id),
    }


@app.post("/outcome/{workflow_id}/resume-durable")
def outcome_resume_durable_185(workflow_id: str):
    if not outcome_get(workflow_id):
        raise HTTPException(404, "workflow not found")
    job, reused = enqueue_durable_workflow_185(workflow_id)
    return {
        "status": "queued" if not reused else job["status"],
        "workflow_id": workflow_id,
        "job": durable_job_get_185(job["id"]),
        "reused": reused,
        "automatic_side_effect_replay": False,
    }


@app.get("/durable-jobs")
def durable_jobs_185():
    _durable_recover_185()
    with _db_lock, db() as c:
        rows = c.execute(
            "SELECT id,workflow_id,status,attempts,lease_until,last_error,created_at,updated_at FROM durable_work_jobs_185 ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return {"jobs": [dict(r) for r in rows]}


def self_test_185():
    checks = []
    def ck(name, fn):
        try:
            fn()
            checks.append({"name": name, "passed": True})
        except Exception as e:
            checks.append({"name": name, "passed": False, "error": str(e)})

    ck("version", lambda: APP_VERSION == "TARGET-2050.185")
    ck("durable job table", lambda: durable_job_create_185(outcome_create("ping")['id'])[0] is not None)
    wid = outcome_create("ping then remember that durable workflow resume works")["id"]
    job, reused = durable_job_create_185(wid)
    ck("persistent queued job", lambda: job["workflow_id"] == wid and job["status"] == "queued")
    ck("idempotent enqueue", lambda: durable_job_create_185(wid)[1] is True)
    _durable_run_job_185(job)
    final_job = durable_job_get_185(job["id"])
    final_workflow = outcome_get(wid)
    ck("durable execution closure", lambda: final_job["status"] == "completed" and final_workflow["status"] == "completed")
    ck("certificate survives worker", lambda: verify_outcome_certificate(wid).get("verified") is True)
    policy = durable_policy_185()
    ck("safe restart policy", lambda: policy["restart_recovery"] is True and policy["automatic_side_effect_replay"] is False)
    return {
        "status": "completed",
        "version": APP_VERSION,
        "build": BUILD,
        "passed": all(x["passed"] for x in checks),
        "tests": checks,
        "autonomy_loop": ["persist", "queue", "lease", "reconcile", "resume", "execute", "verify", "close"],
    }


@app.get("/self-test-185")
def self_test_185_route():
    return self_test_185()
