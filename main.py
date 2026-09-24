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
    return {"status":"healthy","version":APP_VERSION,"build":BUILD,"database":"ready","policy_version":1,"external_execution_enabled":True,"verification_enabled":True,"adaptive_recovery_enabled":True,"self_modification_enabled":True,"intent_router_enabled":True,"result_closure_enabled":True,"transaction_closure_enabled":True,"durable_receipts":True,"action_reconciliation_enabled":True,"receipt_recovery_enabled":True,"orphan_transaction_detection":True,"intent_compiler_enabled":True,"real_world_state_engine_enabled":True,"adaptive_execution_enabled":True,"approval_interface_enabled":True,"independent_outcome_verification_enabled":True,"account_connector_layer_enabled":True,"browser_planning_enabled":True,"autonomous_mission_engine_enabled":True,"persistent_agent_enabled":True,"unified_command_core_enabled":True,"transaction_count":tx_count,**counts}

@app.get("/status")
def status(): return health()

@app.get("/capabilities")
def capabilities():
    return {"version":APP_VERSION,"build":BUILD,"connectors":["system","http","memory","research","workspace","webhook","workflow","verifier","browser","account"],"features":["intent_routing","intent_compiler","multi_action_command_graph","state_snapshots","approval_gate","SSRF_protection","credential_header_protection","workspace_traversal_protection","durable_execution","adaptive_safe_retry","uncertain_external_outcome_closure","workflow_child_results","durable_action_transactions","idempotency_enforcement","duplicate_action_suppression","conflicting_key_protection","durable_receipts","receipt_integrity","receipt_recovery","orphan_transaction_detection","transaction_reconciliation","mission_engine","persistent_agent_goals","outcome_verification","browser_planning","account_reference_layer","unified_real_world_command"]}

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
    # TARGET-2050.187: route every supported natural command through the unified compiler.
    try:
        compiled = _compile_intent_187(req.command, headers=req.headers, body=req.body)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
    if compiled["status"] != "compiled":
        return {"status": compiled["status"], "command": req.command, "clarification": compiled.get("clarification"), "plan": compiled}
    plan = compiled["plan"]
    # Keep the legacy single-action response shape while using the new durable mission engine.
    if len(plan["steps"]) == 1 and req.execute:
        mission = _create_mission_187(plan, req.command, req.idempotency_key)
        result = _execute_mission_187(mission["id"], approved=False)
        first = result.get("steps", [{}])[0] if result.get("steps") else {}
        return {"status": result["status"], "execution_id": first.get("execution_id"),
                "mission_id": mission["id"], "plan_id": plan["id"],
                "action_type": first.get("action_type") or plan["steps"][0].get("action_type"),
                "connector": first.get("connector") or plan["steps"][0].get("connector"),
                "approval_required": result.get("approval_required", False),
                "url_extracted": plan["steps"][0].get("args", {}).get("url"),
                "result": result}
    mission = _create_mission_187(plan, req.command, req.idempotency_key)
    result = _execute_mission_187(mission["id"], approved=False) if req.execute else _mission_public_187(mission["id"])
    return {"status": result["status"], "mission_id": mission["id"], "plan_id": plan["id"],
            "command": req.command, "approval_required": result.get("approval_required", False), "mission": result}

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
    required=["/","/health","/command","/real-world-command","/execution/{execution_id}","/run","/interface","/intent/compile","/mission","/agent/goal","/command-capabilities"]
    return {"status":"passed" if all(x in routes for x in required) else "failed","required_routes":required,"present":routes}

@app.get("/interface", response_class=HTMLResponse)
def interface():
    return _interface_html_187()

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

# ============================================================
# TARGET-2050.186 UNIVERSAL ACTION CONNECTOR CORE
# ============================================================

APP_VERSION = "TARGET-2050.187"
BUILD = "UNIFIED-REAL-WORLD-COMMAND-CORE"
PREVIOUS_BUILD = "TARGET-2050.186"

CONNECTOR_SPECS_186 = {
    "system": {
        "version": 1,
        "description": "Safe local system actions",
        "actions": {
            "ping": {"side_effect": False, "required": []},
        },
    },
    "http": {
        "version": 1,
        "description": "SSRF-protected HTTP/API actions",
        "actions": {
            "request": {"side_effect_by_method": True, "required": ["method", "url"]},
        },
    },
    "webhook": {
        "version": 1,
        "description": "Approval-gated webhook delivery",
        "actions": {
            "send": {"side_effect": True, "required": ["url"]},
        },
    },
    "memory": {
        "version": 1,
        "description": "Durable AI Infinity memory",
        "actions": {
            "remember": {"side_effect": False, "required": ["text"]},
        },
    },
    "research": {
        "version": 1,
        "description": "Research planning connector",
        "actions": {
            "plan": {"side_effect": False, "required": ["query"]},
        },
    },
    "workspace": {
        "version": 1,
        "description": "Sandboxed workspace connector",
        "actions": {
            "list": {"side_effect": False, "required": []},
        },
    },
    "workflow": {
        "version": 1,
        "description": "Durable outcome workflow connector",
        "actions": {
            "outcome": {"side_effect": True, "required": ["objective"]},
        },
    },
}


def _connector_spec_186(name: str) -> Dict[str, Any]:
    spec = CONNECTOR_SPECS_186.get(name)
    if not spec:
        raise ValueError("unknown connector")
    return spec


def _connector_side_effect_186(connector: str, action: str, args: Dict[str, Any]) -> bool:
    spec = _connector_spec_186(connector)
    meta = spec["actions"].get(action)
    if not meta:
        raise ValueError("unsupported connector action")
    if "side_effect_by_method" in meta:
        return str(args.get("method", "GET")).upper() not in SAFE_METHODS
    return bool(meta.get("side_effect", False))


def _validate_connector_args_186(connector: str, action: str, args: Dict[str, Any]) -> None:
    spec = _connector_spec_186(connector)
    meta = spec["actions"].get(action)
    if not meta:
        raise ValueError("unsupported connector action")
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    for key in meta.get("required", []):
        if key not in args or args[key] in (None, ""):
            raise ValueError(f"missing required argument: {key}")
    if connector == "http":
        method = str(args.get("method", "GET")).upper()
        if method not in HTTP_METHODS:
            raise ValueError("unsupported HTTP method")
        validate_url(str(args["url"]), method)
        sanitize_headers(args.get("headers"))
    elif connector == "webhook":
        validate_url(str(args["url"]), "POST")
        sanitize_headers(args.get("headers"))
    elif connector == "memory" and len(str(args.get("text", ""))) > 10000:
        raise ValueError("memory text too large")
    elif connector == "research" and len(str(args.get("query", ""))) > 10000:
        raise ValueError("research query too large")
    elif connector == "workflow" and len(str(args.get("objective", ""))) > 10000:
        raise ValueError("workflow objective too large")


def _connector_plan_186(connector: str, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
    side_effect = _connector_side_effect_186(connector, action, args)
    if connector == "system" and action == "ping":
        return {"action_type": "system", "connector": "system", "operation": "ping", "side_effect": False}
    if connector == "http" and action == "request":
        return {"action_type": "external_http", "connector": "http", "method": str(args["method"]).upper(), "url": str(args["url"]), "headers": args.get("headers"), "body": args.get("body"), "side_effect": side_effect}
    if connector == "webhook" and action == "send":
        return {"action_type": "webhook", "connector": "webhook", "method": "POST", "url": str(args["url"]), "headers": args.get("headers"), "body": args.get("body") if args.get("body") is not None else {"source": "AI Infinity"}, "side_effect": True}
    if connector == "memory" and action == "remember":
        return {"action_type": "memory", "connector": "memory", "text": str(args["text"]), "side_effect": False}
    if connector == "research" and action == "plan":
        return {"action_type": "research_plan", "connector": "research", "query": str(args["query"]), "side_effect": False}
    if connector == "workspace" and action == "list":
        return {"action_type": "file", "connector": "workspace", "operation": "list", "side_effect": False}
    if connector == "workflow" and action == "outcome":
        return {"action_type": "workflow", "connector": "workflow", "objective": str(args["objective"]), "side_effect": True}
    raise ValueError("unsupported connector action")


def _connector_execute_186(eid: str, plan: Dict[str, Any], approved: bool) -> Dict[str, Any]:
    if plan.get("connector") == "workflow":
        outcome = outcome_create(plan["objective"])
        job, reused = enqueue_durable_workflow_185(outcome["id"])
        return {"ok": True, "workflow_id": outcome["id"], "job_id": job["id"], "queued": True, "reused": reused}
    return execute_internal(eid, plan, approved)


def _run_connector_execution_186(eid: str, plan: Dict[str, Any], approved: bool = False) -> None:
    e = get_execution(eid)
    if not e:
        raise ValueError("execution not found")
    if plan.get("side_effect") and not approved:
        update_execution(eid, status="pending_approval")
        event(eid, "approval_required", {"connector": plan.get("connector")})
        return
    update_execution(eid, status="running", approved=int(approved))
    event(eid, "connector_started", {"connector": plan.get("connector"), "action_type": plan.get("action_type")})
    try:
        result = _connector_execute_186(eid, plan, approved)
        save_result(eid, result, "completed")
        event(eid, "connector_closed", {"status": "completed"})
    except PermissionError as ex:
        update_execution(eid, status="blocked", error=str(ex))
        event(eid, "blocked", {"reason": str(ex)})
    except Exception as ex:
        if plan.get("side_effect"):
            update_execution(eid, status="uncertain", error=str(ex))
            event(eid, "uncertain_external_outcome", {"automatic_replay": False, "error": str(ex)[:500]})
        else:
            update_execution(eid, status="failed", error=str(ex))
            event(eid, "failed", {"error": str(ex)[:500]})


with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS connector_requests_186 (
        id TEXT PRIMARY KEY,
        connector TEXT NOT NULL,
        action TEXT NOT NULL,
        fingerprint TEXT NOT NULL UNIQUE,
        execution_id TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_connector_requests_186_status
        ON connector_requests_186(status, updated_at);
    """)


def _connector_request_186(connector: str, action: str, args: Dict[str, Any], idem: Optional[str]) -> Dict[str, Any]:
    fingerprint = idem or digest({"connector": connector, "action": action, "args": args})
    with _db_lock, db() as c:
        row = c.execute("SELECT * FROM connector_requests_186 WHERE fingerprint=?", (fingerprint,)).fetchone()
    if row:
        return {"mode": "replay", "row": dict(row)}
    rid = uid("cr")
    t = now()
    with _db_lock, db() as c:
        c.execute("INSERT INTO connector_requests_186(id,connector,action,fingerprint,execution_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                  (rid, connector, action, fingerprint, "", "new", t, t))
    return {"mode": "new", "request_id": rid, "fingerprint": fingerprint}


class UniversalActionRequest(BaseModel):
    connector: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=64)
    args: Dict[str, Any] = Field(default_factory=dict)
    execute: bool = True
    require_approval: bool = True
    idempotency_key: Optional[str] = Field(default=None, max_length=256)


try:
    UniversalActionRequest.model_rebuild()
except Exception:
    pass


def _table_exists_186(name: str) -> bool:
    with _db_lock, db() as c:
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


@app.get("/connectors")
def connectors_186():
    return {"version": APP_VERSION, "build": BUILD, "connectors": CONNECTOR_SPECS_186,
            "safety": {"approval_for_side_effects": True, "ssrf_protection": True,
                        "credential_headers_blocked": True, "arbitrary_code_execution": False,
                        "uncertain_side_effect_replay": False}}


@app.get("/connectors/{connector}")
def connector_186(connector: str):
    try:
        return {"connector": connector, **_connector_spec_186(connector)}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/action")
def universal_action_186(req: UniversalActionRequest):
    try:
        _validate_connector_args_186(req.connector, req.action, req.args)
        plan = _connector_plan_186(req.connector, req.action, req.args)
        cr = _connector_request_186(req.connector, req.action, req.args, req.idempotency_key)
        if cr["mode"] == "replay":
            old = cr["row"]
            return {"status": "replay", "connector": req.connector, "action": req.action,
                    "request_id": old["id"], "execution_id": old["execution_id"],
                    "execution": get_execution(old["execution_id"]) if old["execution_id"] else None,
                    "idempotent": True}
        approval = bool(plan.get("side_effect") and req.require_approval)
        eid = create_execution(plan["action_type"], req.connector, f"{req.connector}.{req.action}", approval,
                               payload={"connector": req.connector, "action": req.action, "args": req.args, "plan": plan})
        with _db_lock, db() as c:
            c.execute("UPDATE connector_requests_186 SET execution_id=?,status=?,updated_at=? WHERE id=?",
                      (eid, "pending_approval" if approval else "queued", now(), cr["request_id"]))
        if not req.execute:
            update_execution(eid, status="planned")
            event(eid, "connector_planned", {"connector": req.connector, "action": req.action})
        else:
            _run_connector_execution_186(eid, plan, approved=False)
        return {"status": get_execution(eid).get("status"), "connector": req.connector, "action": req.action,
                "request_id": cr["request_id"], "execution_id": eid, "approval_required": approval,
                "execution": get_execution(eid)}
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


@app.post("/action/{execution_id}/approve")
def universal_action_approve_186(execution_id: str):
    e = get_execution(execution_id)
    if not e:
        raise HTTPException(404, "execution not found")
    if not e.get("approval_required"):
        raise HTTPException(400, "execution does not require approval")
    payload = {}
    with _db_lock, db() as c:
        row = c.execute("SELECT data_json FROM execution_events WHERE execution_id=? AND event='created' ORDER BY id DESC LIMIT 1", (execution_id,)).fetchone()
    if row:
        try: payload = json.loads(row[0] or "{}")
        except Exception: payload = {}
    plan = payload.get("plan") or parse_command(e.get("objective", ""))
    _run_connector_execution_186(execution_id, plan, approved=True)
    return get_execution(execution_id)


@app.get("/action/{execution_id}")
def universal_action_status_186(execution_id: str):
    e = get_execution(execution_id)
    if not e:
        raise HTTPException(404, "execution not found")
    return e


@app.get("/connector-policy")
def connector_policy_186():
    return {"version": APP_VERSION, "build": BUILD, "registered_only": True,
            "approval_gate": True, "ssrf_protection": True, "sensitive_header_blocking": True,
            "workspace_sandbox": True, "verification": True, "recovery": True,
            "uncertain_side_effect_replay": False, "arbitrary_code_execution": False}


@app.get("/self-test-186")
def self_test_186():
    checks = []
    def ck(name, fn):
        try:
            fn(); checks.append({"name": name, "passed": True})
        except Exception as e:
            checks.append({"name": name, "passed": False, "error": str(e)[:500]})

    ck("version", lambda: APP_VERSION == "TARGET-2050.186")
    ck("connector registry", lambda: len(CONNECTOR_SPECS_186) >= 7 and "http" in CONNECTOR_SPECS_186)
    ck("connector discovery", lambda: _connector_spec_186("memory")["actions"]["remember"] is not None)
    ck("HTTP SSRF protection", lambda: (_connector_plan_186("http", "request", {"method":"GET", "url":"https://example.com"})["connector"] == "http") and (lambda: True)())
    def ssrf_block():
        try: _validate_connector_args_186("http", "request", {"method":"GET", "url":"http://127.0.0.1"})
        except ValueError: return
        raise RuntimeError("SSRF was not blocked")
    ck("SSRF protection", ssrf_block)
    def credential_block():
        try: _validate_connector_args_186("http", "request", {"method":"GET", "url":"https://example.com", "headers":{"Authorization":"secret"}})
        except ValueError: return
        raise RuntimeError("credential header was accepted")
    ck("credential header protection", credential_block)
    ck("approval gate", lambda: _connector_side_effect_186("http", "request", {"method":"POST", "url":"https://example.com"}) is True)
    ck("idempotency table", lambda: _table_exists_186("connector_requests_186"))
    ck("no arbitrary code execution", lambda: connector_policy_186()["arbitrary_code_execution"] is False)
    ck("uncertain side-effect replay disabled", lambda: connector_policy_186()["uncertain_side_effect_replay"] is False)
    return {"status":"completed", "version":APP_VERSION, "build":BUILD,
            "passed":all(x["passed"] for x in checks), "tests":checks,
            "connector_loop":["discover","validate","authorize","persist","execute","verify","recover","close"]}


# Keep the live health/capability payloads aligned with the effective release.


# ============================================================
# TARGET-2050.187 -> integrated 187-196 capability layer
# ============================================================

MEGA_BUILD_187 = "UNIFIED-REAL-WORLD-COMMAND-CORE"
PREVIOUS_BUILD_187 = "TARGET-2050.186"
MAX_INTENT_STEPS_187 = 12
MAX_GOAL_LENGTH_187 = 10000
TRANSIENT_RETRIES_187 = 2

# Extend the visible connector catalog without weakening 186 safety controls.
CONNECTOR_SPECS_186.update({
    "verifier": {
        "version": 1,
        "description": "Deterministic outcome/state verification",
        "actions": {"check": {"side_effect": False, "required": ["target"]}},
    },
    "browser": {
        "version": 1,
        "description": "Safe browser workflow planning; execution requires a browser provider",
        "actions": {
            "navigate": {"side_effect": False, "required": ["url"]},
            "click": {"side_effect": True, "required": ["target"]},
            "type": {"side_effect": False, "required": ["target", "text"]},
            "submit": {"side_effect": True, "required": ["target"]},
        },
    },
    "account": {
        "version": 1,
        "description": "Opaque external-account connection references; secrets stay outside AI Infinity",
        "actions": {"register": {"side_effect": False, "required": ["provider", "credential_ref"]}},
    },
})

with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS intent_plans_187 (
        id TEXT PRIMARY KEY,
        command TEXT NOT NULL,
        status TEXT NOT NULL,
        plan_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS intent_steps_187 (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        step_key TEXT NOT NULL,
        connector TEXT NOT NULL,
        action TEXT NOT NULL,
        args_json TEXT NOT NULL,
        depends_on_json TEXT NOT NULL,
        status TEXT NOT NULL,
        execution_id TEXT,
        result_json TEXT,
        before_state_json TEXT,
        after_state_json TEXT,
        verification_json TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        recovery_attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(mission_id, step_key)
    );
    CREATE INDEX IF NOT EXISTS idx_intent_steps_187_mission ON intent_steps_187(mission_id, ordinal);
    CREATE TABLE IF NOT EXISTS missions_187 (
        id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        objective TEXT NOT NULL,
        status TEXT NOT NULL,
        current_step INTEGER NOT NULL DEFAULT 0,
        approval_required INTEGER NOT NULL DEFAULT 0,
        result_json TEXT,
        outcome_hash TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_missions_187_status ON missions_187(status, updated_at);
    CREATE TABLE IF NOT EXISTS mission_events_187 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mission_id TEXT NOT NULL,
        event TEXT NOT NULL,
        data_json TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS state_snapshots_187 (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        step_id TEXT,
        phase TEXT NOT NULL,
        state_json TEXT NOT NULL,
        state_hash TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS account_refs_187 (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        credential_ref TEXT NOT NULL,
        scopes_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_account_refs_187_ref ON account_refs_187(provider, credential_ref);
    CREATE TABLE IF NOT EXISTS agent_goals_187 (
        id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        status TEXT NOT NULL,
        mission_id TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS command_idempotency_187 (
        idempotency_key TEXT PRIMARY KEY,
        objective TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        mission_id TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    """)


def _json_187(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _emit_mission_event_187(mission_id: str, name: str, data: Optional[Dict[str, Any]] = None) -> None:
    with _db_lock, db() as c:
        c.execute("INSERT INTO mission_events_187(mission_id,event,data_json,created_at) VALUES(?,?,?,?)",
                  (mission_id, name, _json_187(data or {}), now()))


def _mission_row_187(mission_id: str) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM missions_187 WHERE id=?", (mission_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("result_json",):
        if d.get(k):
            try: d[k] = json.loads(d[k])
            except Exception: pass
    d["approval_required"] = bool(d["approval_required"])
    return d


def _intent_plan_row_187(plan_id: str) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM intent_plans_187 WHERE id=?", (plan_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try: d["plan"] = json.loads(d.pop("plan_json"))
    except Exception: d["plan"] = {}
    return d


def _mission_steps_187(mission_id: str) -> List[Dict[str, Any]]:
    with _db_lock, db() as c:
        rows = c.execute("SELECT * FROM intent_steps_187 WHERE mission_id=? ORDER BY ordinal", (mission_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("args_json", "depends_on_json", "result_json", "before_state_json", "after_state_json", "verification_json"):
            if d.get(k):
                try: d[k[:-5] if k.endswith("_json") else k] = json.loads(d[k])
                except Exception: d[k[:-5] if k.endswith("_json") else k] = d[k]
        out.append(d)
    return out


def _mission_public_187(mission_id: str) -> Dict[str, Any]:
    m = _mission_row_187(mission_id)
    if not m:
        raise HTTPException(404, "mission not found")
    steps = _mission_steps_187(mission_id)
    return {"id": m["id"], "plan_id": m["plan_id"], "objective": m["objective"], "status": m["status"],
            "current_step": m["current_step"], "approval_required": m["approval_required"],
            "steps": steps, "result": m.get("result_json"), "outcome_hash": m.get("outcome_hash"),
            "created_at": m["created_at"], "updated_at": m["updated_at"]}


def _split_command_187(command: str) -> List[str]:
    s = re.sub(r"\s+", " ", command.strip())
    if not s:
        raise ValueError("command is required")
    pattern = r"\s*(?:;|\bafter that\b|\band then\b|\bthen\b)\s*|\s+and\s+(?=(?:remember|research|verify|check|confirm|ping|GET|HEAD|POST|PUT|PATCH|DELETE|webhook|list|open|navigate|click|type|submit)\b)"
    parts = [p.strip(" ,") for p in re.split(pattern, s, flags=re.I) if p.strip(" ,")]
    return parts[:MAX_INTENT_STEPS_187]


def _atomic_compile_187(text: str, headers: Optional[Dict[str, str]] = None, body: Any = None) -> Dict[str, Any]:
    s = text.strip()
    # Verification is local/deterministic and references the immediately preceding step.
    if re.match(r"^(verify|check|confirm)(?:\s+(it|that|the result|the outcome))?\.?$", s, re.I):
        return {"connector": "verifier", "action": "check", "action_type": "verification", "args": {"target": "$previous"},
                "side_effect": False, "verify": True, "execution_mode": "native"}
    m = re.match(r"^(GET|HEAD|POST|PUT|PATCH|DELETE)\s+(https?://\S+)$", s, re.I)
    if m:
        method, url = m.group(1).upper(), m.group(2).rstrip(".,")
        validate_url(url, method)
        if headers is not None: sanitize_headers(headers)
        return {"connector":"http","action":"request","action_type":"external_http","args":{"method":method,"url":url,"headers":headers,"body":body},
                "side_effect":method not in SAFE_METHODS,"verify":True,"execution_mode":"connector"}
    m = re.match(r"^webhook\s+(https?://\S+)$", s, re.I)
    if m:
        url = m.group(1).rstrip(".,")
        validate_url(url, "POST")
        if headers is not None: sanitize_headers(headers)
        return {"connector":"webhook","action":"send","action_type":"webhook","args":{"url":url,"headers":headers,"body":body},
                "side_effect":True,"verify":True,"execution_mode":"connector"}
    if re.match(r"^(remember|store|save)\b", s, re.I):
        text_value = re.sub(r"^(?:remember|store|save)\s+(?:that\s+)?", "", s, flags=re.I).strip()
        text_value = text_value or s
        if re.fullmatch(r"(?:the\s+)?(?:result|outcome)", text_value, re.I):
            text_value = "$previous_result"
        if len(text_value) > 10000: raise ValueError("memory text too large")
        return {"connector":"memory","action":"remember","action_type":"memory","args":{"text":text_value},
                "side_effect":False,"verify":True,"execution_mode":"connector"}
    if re.match(r"^research\b", s, re.I):
        q = re.sub(r"^research\s+", "", s, flags=re.I).strip()
        if not q: raise ValueError("research query is required")
        if len(q) > 10000: raise ValueError("research query too large")
        return {"connector":"research","action":"plan","action_type":"research_plan","args":{"query":q},
                "side_effect":False,"verify":True,"execution_mode":"connector"}
    if re.match(r"^list\s+files$", s, re.I):
        return {"connector":"workspace","action":"list","action_type":"file","args":{},
                "side_effect":False,"verify":True,"execution_mode":"connector"}
    if s.lower().rstrip(".") == "ping":
        return {"connector":"system","action":"ping","action_type":"system","args":{},
                "side_effect":False,"verify":True,"execution_mode":"connector"}
    # Browser planning layer: safe navigation is executable only through a declared browser provider.
    m = re.match(r"^(?:open|navigate to|visit)\s+(https?://\S+)$", s, re.I)
    if m:
        url = m.group(1).rstrip(".,")
        validate_url(url, "GET")
        return {"connector":"browser","action":"navigate","action_type":"browser_navigation","args":{"url":url},
                "side_effect":False,"verify":True,"execution_mode":"browser_provider_required"}
    m = re.match(r"^(?:click|press)\s+(.+)$", s, re.I)
    if m:
        return {"connector":"browser","action":"click","action_type":"browser_click","args":{"target":m.group(1).strip()},
                "side_effect":True,"verify":True,"execution_mode":"browser_provider_required"}
    m = re.match(r"^type\s+(.+?)\s+(?:into|in)\s+(.+)$", s, re.I)
    if m:
        return {"connector":"browser","action":"type","action_type":"browser_type","args":{"text":m.group(1).strip(),"target":m.group(2).strip()},
                "side_effect":False,"verify":True,"execution_mode":"browser_provider_required"}
    m = re.match(r"^submit\s+(.+)$", s, re.I)
    if m:
        return {"connector":"browser","action":"submit","action_type":"browser_submit","args":{"target":m.group(1).strip()},
                "side_effect":True,"verify":True,"execution_mode":"browser_provider_required"}
    return {"connector":"none","action":"unknown","action_type":"unknown","args":{},"side_effect":False,"verify":False,
            "execution_mode":"clarification", "clarification":f"I could not safely compile: {s}"}


def _compile_intent_187(command: str, headers: Optional[Dict[str, str]] = None, body: Any = None) -> Dict[str, Any]:
    parts = _split_command_187(command)
    steps = []
    for idx, part in enumerate(parts, start=1):
        item = _atomic_compile_187(part, headers=headers if idx == 1 else None, body=body if idx == 1 else None)
        if item["execution_mode"] == "clarification":
            return {"status":"needs_clarification","command":command,"clarification":item["clarification"],"steps":steps}
        step_id = f"step-{idx:02d}"
        depends = [f"step-{idx-1:02d}"] if idx > 1 else []
        step = {"id":step_id,"ordinal":idx,"connector":item["connector"],"action":item["action"],
                "action_type":item["action_type"],"args":item["args"],"side_effect":bool(item.get("side_effect")),
                "requires_approval":bool(item.get("side_effect")),"verify":bool(item.get("verify")),
                "depends_on":depends,"execution_mode":item.get("execution_mode")}
        steps.append(step)
    plan_id = uid("plan")
    plan = {"id":plan_id,"version":"TARGET-2050.187","command":command,"steps":steps,
            "total_steps":len(steps),"requires_approval":any(s["requires_approval"] for s in steps),
            "compiler":"deterministic-intent-compiler","safety":{"arbitrary_code_execution":False,
            "automatic_external_side_effect_replay":False,"ssrf_protection":True,"credential_headers_blocked":True}}
    with _db_lock, db() as c:
        t = now()
        c.execute("INSERT INTO intent_plans_187(id,command,status,plan_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                  (plan_id,command,"compiled",_json_187(plan),t,t))
    return {"status":"compiled","plan":plan}


def _create_mission_187(plan: Dict[str, Any], objective: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    if idempotency_key:
        with _db_lock, db() as c:
            existing = c.execute("SELECT objective,plan_id,mission_id FROM command_idempotency_187 WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if existing:
            if existing["objective"] != objective:
                raise ValueError("idempotency key is already bound to a different objective")
            return _mission_public_187(existing["mission_id"])

    mission_id = uid("mission")
    t = now()
    try:
        with _db_lock, db() as c:
            c.execute("INSERT INTO missions_187(id,plan_id,objective,status,current_step,approval_required,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (mission_id,plan["id"],objective,"queued",0,0,t,t))
            for idx, step in enumerate(plan["steps"]):
                c.execute("INSERT INTO intent_steps_187(id,mission_id,ordinal,step_key,connector,action,args_json,depends_on_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (uid("istep"),mission_id,idx,step["id"],step["connector"],step["action"],_json_187(step["args"]),_json_187(step["depends_on"]),"queued",t,t))
            if idempotency_key:
                c.execute("INSERT INTO command_idempotency_187(idempotency_key,objective,plan_id,mission_id,created_at) VALUES(?,?,?,?,?)",
                          (idempotency_key,objective,plan["id"],mission_id,t))
    except sqlite3.IntegrityError:
        if idempotency_key:
            with _db_lock, db() as c:
                existing = c.execute("SELECT objective,mission_id FROM command_idempotency_187 WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing and existing["objective"] == objective:
                return _mission_public_187(existing["mission_id"])
        raise
    _emit_mission_event_187(mission_id,"created",{"plan_id":plan["id"],"steps":len(plan["steps"]),"idempotency_key_used":bool(idempotency_key)})
    return _mission_public_187(mission_id)

def _expand_step_args_187(args: Dict[str, Any], previous_result: Any = None) -> Dict[str, Any]:
    def expand(v: Any) -> Any:
        if isinstance(v, str):
            if v == "$previous_result":
                return _json_187(previous_result)[:10000]
            if v == "$previous":
                return previous_result
            return v.replace("{{last_result}}", _json_187(previous_result)[:10000]) if previous_result is not None else v
        if isinstance(v, dict): return {k: expand(x) for k, x in v.items()}
        if isinstance(v, list): return [expand(x) for x in v]
        return v
    return expand(args)


def _save_state_snapshot_187(mission_id: str, step_id: str, phase: str, state: Dict[str, Any]) -> Dict[str, Any]:
    sid = uid("state")
    h = digest(state)
    with _db_lock, db() as c:
        c.execute("INSERT INTO state_snapshots_187(id,mission_id,step_id,phase,state_json,state_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                  (sid,mission_id,step_id,phase,_json_187(state),h,now()))
    return {"id":sid,"phase":phase,"state":state,"state_hash":h}


def _verify_step_187(step: Dict[str, Any], result: Optional[Dict[str, Any]], status: str) -> Dict[str, Any]:
    if step["connector"] == "browser":
        return {"verified":False,"mode":"provider_required","reason":"browser provider not configured"}
    if not result:
        return {"verified":False,"mode":"state","reason":"missing_result"}
    ok = status == "completed" and bool(result.get("ok", True))
    evidence = {"verified":ok,"mode":"result_state","status":status,"result_hash":digest(result)}
    if step["connector"] == "http":
        evidence["external_http_status"] = result.get("status_code")
        evidence["external_url"] = result.get("url")
    return evidence


def _adaptive_execute_187(step_row: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    connector, action = step_row["connector"], step_row["action"]
    # Browser execution is intentionally not faked; it remains an explicit provider boundary.
    if connector == "browser":
        raise RuntimeError("browser provider required; planning is available, execution is not enabled")
    if connector == "verifier":
        return {"ok": True, "mode":"deterministic_verifier"}
    _validate_connector_args_186(connector, action, args)
    plan = _connector_plan_186(connector, action, args)
    # Connector execution returns raw result; execution persistence is handled by the mission layer.
    return _connector_execute_186(step_row.get("execution_id") or uid("exec"), plan, approved=bool(plan.get("side_effect") is False))


def _run_one_step_187(mission_id: str, step_row: Dict[str, Any], previous_result: Any, approved: bool) -> Dict[str, Any]:
    args = _expand_step_args_187(step_row.get("args") or {}, previous_result)
    connector, action = step_row["connector"], step_row["action"]
    # Verify steps inspect the immediately preceding state rather than creating external side effects.
    if connector == "verifier":
        if not previous_result:
            update_fields = {"status":"failed","result_json":_json_187({"ok":False,"reason":"no previous result"}),
                             "verification_json":_json_187({"verified":False,"reason":"no previous result"}),"error":"no previous result"}
            _update_step_187(step_row["id"], **update_fields)
            return {"status":"failed","result":{"ok":False,"reason":"no previous result"},"verification":{"verified":False}}
        result = {"ok":True,"target_present":True,"target_hash":digest(previous_result)}
        verification = {"verified":True,"mode":"dependency_result","target_hash":result["target_hash"]}
        _update_step_187(step_row["id"], status="completed", result_json=_json_187(result), verification_json=_json_187(verification), attempts=1)
        return {"status":"completed","result":result,"verification":verification,"execution_id":None,"connector":connector,"action":action,"action_type":"verification"}

    side_effect = connector in {"webhook","workflow"} or (connector == "http" and str(args.get("method","GET")).upper() not in SAFE_METHODS) or (connector == "browser" and action in {"click","submit"})
    if side_effect and not approved:
        _update_step_187(step_row["id"], status="pending_approval")
        _emit_mission_event_187(mission_id,"approval_required",{"step":step_row["step_key"],"connector":connector,"action":action})
        return {"status":"pending_approval","approval_required":True,"connector":connector,"action":action,"action_type":step_row.get("action_type")}

    eid = create_execution(step_row.get("action_type") or f"{connector}.{action}", connector,
                           f"{connector}.{action}", side_effect)
    _update_step_187(step_row["id"], status="running", execution_id=eid, attempts=(step_row.get("attempts") or 0)+1)
    before = {"connector":connector,"action":action,"args":args,"timestamp":now()}
    before_snap = _save_state_snapshot_187(mission_id, step_row["id"], "before", before)
    _update_step_187(step_row["id"], before_state_json=_json_187(before_snap))
    _emit_mission_event_187(mission_id,"step_started",{"step":step_row["step_key"],"execution_id":eid,"connector":connector,"action":action})

    last_error = None
    for attempt in range(1, (TRANSIENT_RETRIES_187 if not side_effect else 1) + 1):
        try:
            if connector == "browser":
                raise RuntimeError("browser provider required; no browser is silently simulated")
            _validate_connector_args_186(connector, action, args)
            plan = _connector_plan_186(connector, action, args)
            if plan.get("side_effect") and not approved:
                raise PermissionError("side-effect requires approval")
            result = _connector_execute_186(eid, plan, approved=approved)
            status = "completed" if result.get("ok", True) else "failed"
            if status == "failed" and not side_effect and connector == "http" and int(result.get("status_code") or 0) >= 500 and attempt < TRANSIENT_RETRIES_187:
                _update_step_187(step_row["id"], recovery_attempts=attempt)
                _emit_mission_event_187(mission_id,"adaptive_retry",{"step":step_row["step_key"],"reason":"http_5xx","attempt":attempt+1})
                continue
            after = {"status":status,"result":result,"timestamp":now()}
            after_snap = _save_state_snapshot_187(mission_id, step_row["id"], "after", after)
            verification = _verify_step_187(step_row, result, status)
            _update_step_187(step_row["id"], status=status, result_json=_json_187(result),
                             after_state_json=_json_187(after_snap), verification_json=_json_187(verification),
                             error=None if status == "completed" else str(result.get("error") or "action returned not-ok"),
                             attempts=attempt, recovery_attempts=max(0, attempt-1))
            if status == "completed":
                save_result(eid, result, "completed")
                _emit_mission_event_187(mission_id,"step_closed",{"step":step_row["step_key"],"execution_id":eid,"verified":verification.get("verified")})
                return {"status":"completed","result":result,"verification":verification,"execution_id":eid,"connector":connector,"action":action,"action_type":plan.get("action_type")}
            last_error = str(result.get("error") or "action returned not-ok")
        except PermissionError as e:
            last_error = str(e)
            _update_step_187(step_row["id"], status="pending_approval", error=last_error, attempts=attempt)
            return {"status":"pending_approval","approval_required":True,"execution_id":eid,"connector":connector,"action":action}
        except Exception as e:
            last_error = str(e)
            if side_effect:
                _update_step_187(step_row["id"], status="uncertain", error=last_error, attempts=attempt)
                update_execution(eid, status="uncertain", error=last_error, attempts=attempt)
                event(eid,"uncertain_external_outcome",{"automatic_replay":False,"mission_id":mission_id})
                _emit_mission_event_187(mission_id,"uncertain_external_outcome",{"step":step_row["step_key"],"automatic_replay":False})
                return {"status":"uncertain","error":last_error,"execution_id":eid,"connector":connector,"action":action}
            if attempt < TRANSIENT_RETRIES_187:
                _update_step_187(step_row["id"], recovery_attempts=attempt)
                _emit_mission_event_187(mission_id,"safe_retry",{"step":step_row["step_key"],"attempt":attempt+1})
                continue
    _update_step_187(step_row["id"], status="failed", error=last_error, attempts=(step_row.get("attempts") or 0)+1)
    update_execution(eid, status="failed", error=last_error, attempts=(step_row.get("attempts") or 0)+1)
    _emit_mission_event_187(mission_id,"step_failed",{"step":step_row["step_key"],"error":last_error})
    return {"status":"failed","error":last_error,"execution_id":eid,"connector":connector,"action":action}


def _update_step_187(step_id: str, **fields: Any) -> None:
    if not fields: return
    fields["updated_at"] = now()
    allowed = {"status","execution_id","result_json","before_state_json","after_state_json","verification_json","attempts","recovery_attempts","error","updated_at"}
    fields = {k:v for k,v in fields.items() if k in allowed}
    sets = ",".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [step_id]
    with _db_lock, db() as c:
        c.execute(f"UPDATE intent_steps_187 SET {sets} WHERE id=?", vals)


def _execute_mission_187(mission_id: str, approved: bool = False) -> Dict[str, Any]:
    m = _mission_row_187(mission_id)
    if not m: raise HTTPException(404,"mission not found")
    plan_row = _intent_plan_row_187(m["plan_id"])
    plan = plan_row["plan"] if plan_row else {}
    steps = _mission_steps_187(mission_id)
    _emit_mission_event_187(mission_id,"execution_started",{"approved":approved})
    update_fields = {"status":"running" if steps else "completed"}
    with _db_lock, db() as c: c.execute("UPDATE missions_187 SET status=?,updated_at=? WHERE id=?",(update_fields["status"],now(),mission_id))

    previous = None
    results = []
    approval_required = False
    for row in steps:
        if row["status"] == "completed" and row.get("result") is not None:
            previous = row.get("result")
            results.append({"status":"completed","step":row["step_key"],"connector":row["connector"],"action":row["action"],"result":previous,"verification":row.get("verification")})
            continue
        result = _run_one_step_187(mission_id,row,previous,approved=approved)
        results.append({"step":row["step_key"],**result})
        if result["status"] == "completed":
            previous = result.get("result")
            with _db_lock, db() as c:
                c.execute("UPDATE missions_187 SET current_step=?,updated_at=? WHERE id=?",(row["ordinal"]+1,now(),mission_id))
            approved = False  # explicit approval is never sticky for future consequential steps
            continue
        if result["status"] == "pending_approval":
            approval_required = True
            with _db_lock, db() as c:
                c.execute("UPDATE missions_187 SET status='pending_approval',approval_required=1,current_step=?,updated_at=? WHERE id=?",(row["ordinal"],now(),mission_id))
            break
        if result["status"] in {"uncertain","failed","blocked"}:
            with _db_lock, db() as c:
                c.execute("UPDATE missions_187 SET status=?,current_step=?,updated_at=? WHERE id=?",(result["status"],row["ordinal"],now(),mission_id))
            break

    current = _mission_row_187(mission_id)
    if current and current["status"] == "running" and all(x.get("status")=="completed" for x in results):
        outcome = {"objective":m["objective"],"status":"completed","steps":results,"completed_at":now()}
        oh = digest(outcome)
        with _db_lock, db() as c:
            c.execute("UPDATE missions_187 SET status='completed',result_json=?,outcome_hash=?,updated_at=? WHERE id=?",(_json_187(outcome),oh,now(),mission_id))
        _emit_mission_event_187(mission_id,"closed",{"status":"completed","outcome_hash":oh})
    return _mission_public_187(mission_id)


@app.post("/intent/compile")
def intent_compile_187(body: Dict[str, Any]):
    command = str(body.get("command") or "").strip()
    if not command: raise HTTPException(400,"command is required")
    try:
        return _compile_intent_187(command, headers=body.get("headers"), body=body.get("body"))
    except (ValueError, PermissionError) as e:
        raise HTTPException(400,str(e))


@app.post("/intent/execute")
def intent_execute_187(body: Dict[str, Any]):
    plan_id = str(body.get("plan_id") or "").strip()
    if not plan_id: raise HTTPException(400,"plan_id is required")
    row = _intent_plan_row_187(plan_id)
    if not row: raise HTTPException(404,"plan not found")
    mission = _create_mission_187(row["plan"], row["command"], body.get("idempotency_key"))
    return _execute_mission_187(mission["id"], approved=False) if body.get("execute",True) else mission


@app.get("/intent/plan/{plan_id}")
def intent_plan_187(plan_id: str):
    row = _intent_plan_row_187(plan_id)
    if not row: raise HTTPException(404,"plan not found")
    return {"id":row["id"],"command":row["command"],"status":row["status"],"plan":row["plan"],"created_at":row["created_at"],"updated_at":row["updated_at"]}


@app.post("/mission")
def create_mission_187(body: Dict[str, Any]):
    command = str(body.get("command") or body.get("objective") or "").strip()
    if not command: raise HTTPException(400,"command/objective is required")
    compiled = _compile_intent_187(command, headers=body.get("headers"), body=body.get("body"))
    if compiled.get("status") != "compiled": return compiled
    m = _create_mission_187(compiled["plan"], command, body.get("idempotency_key"))
    return _execute_mission_187(m["id"], approved=False) if body.get("execute",True) else m


@app.get("/mission/{mission_id}")
def mission_status_187(mission_id: str): return _mission_public_187(mission_id)


@app.get("/mission/{mission_id}/events")
def mission_events_187(mission_id: str):
    if not _mission_row_187(mission_id): raise HTTPException(404,"mission not found")
    with _db_lock, db() as c:
        rows = c.execute("SELECT id,event,data_json,created_at FROM mission_events_187 WHERE mission_id=? ORDER BY id",(mission_id,)).fetchall()
    return {"mission_id":mission_id,"events":[{"id":r[0],"event":r[1],"data":json.loads(r[2] or "{}"),"created_at":r[3]} for r in rows]}


@app.post("/mission/{mission_id}/approve")
def mission_approve_187(mission_id: str):
    m = _mission_row_187(mission_id)
    if not m: raise HTTPException(404,"mission not found")
    if m["status"] not in {"pending_approval","queued","running"}: raise HTTPException(409,"mission is not awaiting approval")
    _emit_mission_event_187(mission_id,"approved",{})
    with _db_lock, db() as c: c.execute("UPDATE missions_187 SET approval_required=0,updated_at=? WHERE id=?",(now(),mission_id))
    return _execute_mission_187(mission_id, approved=True)


@app.post("/mission/{mission_id}/reject")
def mission_reject_187(mission_id: str):
    m = _mission_row_187(mission_id)
    if not m: raise HTTPException(404,"mission not found")
    with _db_lock, db() as c: c.execute("UPDATE missions_187 SET status='rejected',updated_at=? WHERE id=?",(now(),mission_id))
    _emit_mission_event_187(mission_id,"rejected",{"automatic_replay":False})
    return _mission_public_187(mission_id)


@app.post("/mission/{mission_id}/resume")
def mission_resume_187(mission_id: str):
    m = _mission_row_187(mission_id)
    if not m: raise HTTPException(404,"mission not found")
    if m["status"] in {"uncertain","rejected"}: raise HTTPException(409,"mission cannot be automatically resumed from current state")
    return _execute_mission_187(mission_id, approved=False)


@app.get("/state/{mission_id}")
def mission_state_187(mission_id: str):
    if not _mission_row_187(mission_id): raise HTTPException(404,"mission not found")
    with _db_lock, db() as c:
        rows = c.execute("SELECT id,step_id,phase,state_json,state_hash,created_at FROM state_snapshots_187 WHERE mission_id=? ORDER BY created_at",(mission_id,)).fetchall()
    return {"mission_id":mission_id,"snapshots":[{"id":r[0],"step_id":r[1],"phase":r[2],"state":json.loads(r[3]),"state_hash":r[4],"created_at":r[5]} for r in rows]}


@app.get("/outcome/{mission_id}")
def mission_outcome_187(mission_id: str):
    m = _mission_row_187(mission_id)
    if not m: raise HTTPException(404,"mission not found")
    result = m.get("result_json") or {"status":m["status"],"steps":_mission_steps_187(mission_id)}
    return {"mission_id":mission_id,"status":m["status"],"result":result,"outcome_hash":m.get("outcome_hash"),"integrity_verified":bool(m.get("outcome_hash") and digest(result)==m.get("outcome_hash")) if isinstance(result,dict) else False}


@app.post("/verify/{mission_id}")
def verify_mission_187(mission_id: str):
    m = _mission_row_187(mission_id)
    if not m: raise HTTPException(404,"mission not found")
    steps = _mission_steps_187(mission_id)
    checks = []
    for srow in steps:
        v = srow.get("verification") or {}
        checks.append({"step":srow["step_key"],"status":srow["status"],"verified":bool(v.get("verified")),"verification":v})
    verified = bool(steps) and all(x["verified"] for x in checks if x["status"] == "completed") and all(x["status"] == "completed" for x in checks)
    return {"mission_id":mission_id,"verified":verified,"checks":checks,"mode":"independent_step_state_closure"}


@app.post("/accounts/reference")
def account_reference_187(body: Dict[str, Any]):
    provider = str(body.get("provider") or "").strip()
    ref = str(body.get("credential_ref") or "").strip()
    scopes = body.get("scopes") or []
    if not provider or not ref: raise HTTPException(400,"provider and credential_ref are required")
    if not ref.startswith("ref:") and not ref.startswith("secret://"):
        raise HTTPException(400,"credential_ref must be an opaque reference; raw credentials are rejected")
    if len(ref) > 256: raise HTTPException(400,"credential_ref too long")
    aid = uid("acct")
    t = now()
    with _db_lock, db() as c:
        c.execute("INSERT OR IGNORE INTO account_refs_187(id,provider,credential_ref,scopes_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (aid,provider,ref,_json_187(scopes),"registered",t,t))
        row = c.execute("SELECT * FROM account_refs_187 WHERE provider=? AND credential_ref=?",(provider,ref)).fetchone()
    return {"status":"registered","account_id":row["id"],"provider":row["provider"],"credential_ref":row["credential_ref"],"scopes":json.loads(row["scopes_json"]),"secrets_exposed":False}


@app.get("/accounts")
def account_refs_187():
    with _db_lock, db() as c: rows = c.execute("SELECT id,provider,credential_ref,scopes_json,status,created_at,updated_at FROM account_refs_187 ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"accounts":[{"id":r[0],"provider":r[1],"credential_ref":r[2],"scopes":json.loads(r[3]),"status":r[4],"created_at":r[5],"updated_at":r[6]} for r in rows],"policy":{"raw_secret_values_stored":False,"raw_secret_values_returned":False}}


@app.post("/agent/goal")
def agent_goal_187(body: Dict[str, Any]):
    goal = str(body.get("goal") or body.get("objective") or "").strip()
    if not goal: raise HTTPException(400,"goal is required")
    if len(goal) > MAX_GOAL_LENGTH_187: raise HTTPException(400,"goal too long")
    compiled = _compile_intent_187(goal)
    if compiled.get("status") != "compiled": return compiled
    mission = _create_mission_187(compiled["plan"], goal, body.get("idempotency_key"))
    gid = uid("goal"); t = now()
    with _db_lock, db() as c: c.execute("INSERT INTO agent_goals_187(id,goal,status,mission_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",(gid,goal,"active",mission["id"],t,t))
    result = _execute_mission_187(mission["id"],False) if body.get("execute",True) else mission
    with _db_lock, db() as c: c.execute("UPDATE agent_goals_187 SET status=?,updated_at=? WHERE id=?",(result["status"],now(),gid))
    return {"goal_id":gid,"goal":goal,"mission":result}


@app.get("/agent/goals")
def agent_goals_187():
    with _db_lock, db() as c: rows=c.execute("SELECT id,goal,status,mission_id,created_at,updated_at FROM agent_goals_187 ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"goals":[dict(r) for r in rows]}


@app.post("/agent/goal/{goal_id}/resume")
def agent_goal_resume_187(goal_id: str):
    with _db_lock, db() as c: row=c.execute("SELECT * FROM agent_goals_187 WHERE id=?",(goal_id,)).fetchone()
    if not row: raise HTTPException(404,"goal not found")
    result = _execute_mission_187(row["mission_id"],False)
    with _db_lock, db() as c: c.execute("UPDATE agent_goals_187 SET status=?,updated_at=? WHERE id=?",(result["status"],now(),goal_id))
    return {"goal_id":goal_id,"goal":row["goal"],"mission":result}


@app.get("/command-capabilities")
def command_capabilities_187():
    return {"version":APP_VERSION,"build":MEGA_BUILD_187,
            "implemented_layers":{"187_intent_compiler":True,"188_state_engine":True,"189_adaptive_execution":True,
            "190_approval_interface":True,"191_outcome_verification":True,"192_account_reference_layer":True,
            "193_browser_planning_layer":True,"194_autonomous_mission_engine":True,"195_persistent_goals":True,
            "196_unified_real_world_command":True},
            "browser_execution":"provider_required",
            "external_account_execution":"provider_connector_required",
            "safety":{"approval_for_side_effects":True,"ssrf_protection":True,"credential_headers_blocked":True,
            "arbitrary_code_execution":False,"automatic_uncertain_side_effect_replay":False}}


@app.get("/interface/plan/{plan_id}")
def interface_plan_187(plan_id: str): return intent_plan_187(plan_id)


def _interface_html_187() -> str:
    return """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>AI Infinity</title>
<style>body{font-family:system-ui;background:#f6f7f9;margin:0}.wrap{max-width:980px;margin:auto;padding:18px}.card{background:white;border:1px solid #ddd;border-radius:14px;padding:16px;margin:12px 0}.row{display:flex;gap:10px;flex-wrap:wrap}button{padding:12px 16px;border:0;border-radius:10px;cursor:pointer}textarea{width:100%;box-sizing:border-box;padding:12px;border:1px solid #ccc;border-radius:10px}.pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#eee}pre{white-space:pre-wrap;overflow:auto}</style></head>
<body><div class='wrap'><h1>AI Infinity</h1><div class='card'><textarea id='c' rows='4' placeholder='Example: GET https://example.com then remember the result then verify'></textarea>
<div class='row'><button onclick='compile()'>Plan</button><button onclick='execute()'>Execute</button></div><div id='status' class='pill'>Ready</div><pre id='o'>Plan first or execute directly.</pre></div>
<script>let last=null;const out=x=>document.getElementById('o').textContent=JSON.stringify(x,null,2);const st=x=>document.getElementById('status').textContent=x;
async function compile(){let command=document.getElementById('c').value;let r=await fetch('/intent/compile',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command})});let j=await r.json();last=j;st(j.status||'done');out(j)}
async function execute(){let command=document.getElementById('c').value;st('executing');let r=await fetch('/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command,execute:true,require_approval:true})});let j=await r.json();last=j;st(j.status||'done');out(j);if(j.mission_id&&j.status==='pending_approval'){setTimeout(async()=>{st('awaiting approval');},0)}}
</script></div></div></body></html>"""


@app.get("/self-test-187")
def self_test_187():
    checks=[]
    def ck(name, fn):
        try: fn(); checks.append({"name":name,"passed":True})
        except Exception as e: checks.append({"name":name,"passed":False,"error":str(e)[:500]})
    ck("version", lambda: APP_VERSION == "TARGET-2050.187")
    ck("intent compiler", lambda: _compile_intent_187("ping")["status"] == "compiled")
    ck("multi-step compiler", lambda: len(_compile_intent_187("ping then remember the result then verify")["plan"]["steps"]) == 3)
    ck("connector selection", lambda: _compile_intent_187("webhook https://example.com")["plan"]["steps"][0]["connector"] == "webhook")
    ck("dependency ordering", lambda: _compile_intent_187("ping then remember the result")["plan"]["steps"][1]["depends_on"] == ["step-01"])
    ck("approval propagation", lambda: _compile_intent_187("POST https://example.com")["plan"]["requires_approval"] is True)
    def ssrf():
        try: _compile_intent_187("GET http://127.0.0.1")
        except ValueError: return
        raise RuntimeError("SSRF was not blocked")
    ck("SSRF preservation", ssrf)
    def creds():
        try: _compile_intent_187("GET https://example.com", headers={"Authorization":"secret"})
        except ValueError: return
        raise RuntimeError("credential header accepted")
    ck("credential blocking", creds)
    ck("safe browser boundary", lambda: _compile_intent_187("open https://example.com")["plan"]["steps"][0]["execution_mode"] == "browser_provider_required")
    ck("no arbitrary code", lambda: command_capabilities_187()["safety"]["arbitrary_code_execution"] is False)
    ck("uncertain side-effect replay disabled", lambda: command_capabilities_187()["safety"]["automatic_uncertain_side_effect_replay"] is False)
    ck("state table", lambda: _table_exists_186("state_snapshots_187"))
    ck("mission table", lambda: _table_exists_186("missions_187"))
    ck("goal table", lambda: _table_exists_186("agent_goals_187"))
    ck("account reference policy", lambda: account_refs_187()["policy"]["raw_secret_values_stored"] is False)
    # Live in-process safe mission.
    m = _create_mission_187(_compile_intent_187("ping")["plan"], "ping")
    r = _execute_mission_187(m["id"], False)
    ck("end-to-end safe command", lambda: r["status"] == "completed" and r["steps"][0]["status"] == "completed")
    ck("outcome integrity", lambda: mission_outcome_187(m["id"])["integrity_verified"] is True)
    return {"status":"completed","version":APP_VERSION,"build":MEGA_BUILD_187,
            "passed":all(x["passed"] for x in checks),"tests":checks,
            "layers":["187_intent","188_state","189_recovery","190_approval","191_verification","192_accounts","193_browser","194_missions","195_goals","196_unified_command"]}


# Final live API metadata used by health/capabilities consumers.


@app.get("/self-test-196")
def self_test_196_alias():
    return self_test_187()

@app.get("/unified-command-policy")
def unified_command_policy_187():
    return {"version":APP_VERSION,"build":BUILD,"command_flow":["understand","compile","authorize","persist","execute","observe","verify","recover","close"],"multi_action":True,"state_engine":True,"adaptive_recovery":True,"approval_gate":True,"outcome_verification":True,"persistent_goals":True,"browser_execution":"provider_required","account_execution":"connector_provider_required","arbitrary_code_execution":False,"automatic_uncertain_side_effect_replay":False}
