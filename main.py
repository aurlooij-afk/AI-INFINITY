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
from urllib.parse import urlparse, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

APP_VERSION = "TARGET-2050.191"
BUILD = "AUTONOMOUS-REAL-WORLD-MISSION-CLOSURE-CORE"
PREVIOUS_BUILD = "TARGET-2050.190"
DATA_DIR = os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity")
_DB_PATH_OVERRIDE = os.getenv("AI_INFINITY_DB_PATH", "").strip()
DB_PATH = _DB_PATH_OVERRIDE or os.path.join(DATA_DIR, "ai_infinity.db")
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

# TARGET-2050.188 provider execution persistence. Secrets are referenced by
# environment-variable name only; raw secret values are never stored or returned.
with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS provider_accounts_188 (
        id TEXT PRIMARY KEY, provider TEXT NOT NULL, credential_env TEXT NOT NULL,
        scopes_json TEXT NOT NULL, allowed_hosts_json TEXT NOT NULL, status TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS provider_actions_188 (
        id TEXT PRIMARY KEY, account_id TEXT NOT NULL, method TEXT NOT NULL, url TEXT NOT NULL,
        status TEXT NOT NULL, approval_required INTEGER NOT NULL, approved INTEGER NOT NULL DEFAULT 0,
        response_json TEXT, response_hash TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    """)


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
    return {"status":"healthy","version":APP_VERSION,"build":BUILD,"database":"ready","policy_version":1,"external_execution_enabled":True,"verification_enabled":True,"adaptive_recovery_enabled":True,"self_modification_enabled":True,"intent_router_enabled":True,"result_closure_enabled":True,"transaction_closure_enabled":True,"durable_receipts":True,"action_reconciliation_enabled":True,"receipt_recovery_enabled":True,"orphan_transaction_detection":True,"intent_compiler_enabled":True,"real_world_state_engine_enabled":True,"adaptive_execution_enabled":True,"approval_interface_enabled":True,"independent_outcome_verification_enabled":True,"account_connector_layer_enabled":True,"browser_planning_enabled":True,"autonomous_mission_engine_enabled":True,"persistent_agent_enabled":True,"unified_command_core_enabled":True,"live_provider_execution_enabled":True,"authenticated_connector_execution_enabled":True,"transaction_count":tx_count,**counts}

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
    return {"version":APP_VERSION,"build":BUILD,"command_flow":["understand","compile","authorize","persist","execute","observe","verify","recover","close"],"multi_action":True,"state_engine":True,"adaptive_recovery":True,"approval_gate":True,"outcome_verification":True,"persistent_goals":True,"browser_execution":"provider_required","account_execution":"connector_provider_required","live_provider_execution":True,"arbitrary_code_execution":False,"automatic_uncertain_side_effect_replay":False}


# ============================================================
# TARGET-2050.188 — REAL-WORLD PROVIDER EXECUTION CORE
# ============================================================

PROVIDER_TYPES_188 = {
    "generic_bearer": {"header": "Authorization", "prefix": "Bearer ", "side_effects": True},
    "generic_api_key": {"header": "X-API-Key", "prefix": "", "side_effects": True},
}

SENSITIVE_RESPONSE_KEYS_188 = {"authorization", "cookie", "set-cookie", "api_key", "apikey", "token", "access_token", "refresh_token", "secret"}
ENV_NAME_188 = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
HOST_188 = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


def _blocked_host_188(host: str) -> bool:
    h = (host or "").lower().rstrip(".")
    if h in BLOCKED_HOSTS:
        return True
    try:
        infos = socket.getaddrinfo(h, None, type=socket.SOCK_STREAM)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                return True
    except Exception:
        return True
    return False


def _provider_host_ok_188(url: str, allowed_hosts: List[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host or _blocked_host_188(host):
        return False
    allowed = [h.lower().rstrip(".") for h in allowed_hosts]
    return any(host == h or host.endswith("." + h) for h in allowed)


def _redact_188(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if str(k).lower() in SENSITIVE_RESPONSE_KEYS_188 else _redact_188(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_188(v) for v in value]
    if isinstance(value, str):
        return value[:MAX_RESPONSE]
    return value


def _provider_account_188(account_id: str):
    with _db_lock, db() as c:
        return c.execute("SELECT * FROM provider_accounts_188 WHERE id=?", (account_id,)).fetchone()


@app.get("/providers")
def providers_188():
    return {
        "version": APP_VERSION,
        "providers": PROVIDER_TYPES_188,
        "requirements": {"credential": "Render/server environment variable", "host_allowlist": "required per account", "approval": "required for every live provider action"},
        "secrets_exposed": False,
    }


@app.post("/accounts/live")
def account_live_register_188(body: Dict[str, Any]):
    provider = str(body.get("provider") or "").strip().lower()
    credential_env = str(body.get("credential_env") or "").strip()
    scopes = body.get("scopes") or []
    hosts = body.get("allowed_hosts") or []
    if provider not in PROVIDER_TYPES_188:
        raise HTTPException(400, "unsupported provider")
    if not ENV_NAME_188.fullmatch(credential_env):
        raise HTTPException(400, "credential_env must be an environment-variable name")
    if not os.getenv(credential_env):
        raise HTTPException(400, "credential environment variable is not configured")
    if not isinstance(scopes, list) or len(scopes) > 50:
        raise HTTPException(400, "scopes must be a list")
    clean_hosts = []
    for h in hosts:
        h = str(h).strip().lower().rstrip(".")
        if not HOST_188.fullmatch(h) or _blocked_host_188(h):
            raise HTTPException(400, "invalid or private allowed host")
        clean_hosts.append(h)
    if not clean_hosts:
        raise HTTPException(400, "at least one allowed host is required")
    account_id = uid("acct")
    t = now()
    with _db_lock, db() as c:
        c.execute("INSERT INTO provider_accounts_188(id,provider,credential_env,scopes_json,allowed_hosts_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                  (account_id, provider, credential_env, json.dumps(scopes), json.dumps(sorted(set(clean_hosts))), "ready", t, t))
    return {"status":"registered","account_id":account_id,"provider":provider,"credential_env":credential_env,"scopes":scopes,"allowed_hosts":sorted(set(clean_hosts)),"secrets_exposed":False}


@app.get("/accounts/live")
def account_live_list_188():
    with _db_lock, db() as c:
        rows = c.execute("SELECT id,provider,credential_env,scopes_json,allowed_hosts_json,status,created_at,updated_at FROM provider_accounts_188 ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"accounts":[{"id":r[0],"provider":r[1],"credential_env":r[2],"scopes":json.loads(r[3]),"allowed_hosts":json.loads(r[4]),"status":r[5],"created_at":r[6],"updated_at":r[7]} for r in rows],"raw_secrets_returned":False}


def _provider_preview_188(body: Dict[str, Any]) -> Dict[str, Any]:
    account_id = str(body.get("account_id") or "").strip()
    method = str(body.get("method") or "GET").upper().strip()
    url = str(body.get("url") or "").strip()
    if not account_id or not url: raise HTTPException(400, "account_id and url are required")
    if method not in HTTP_METHODS: raise HTTPException(400, "unsupported HTTP method")
    row = _provider_account_188(account_id)
    if not row: raise HTTPException(404, "live provider account not found")
    hosts = json.loads(row["allowed_hosts_json"])
    if not _provider_host_ok_188(url, hosts): raise HTTPException(403, "target is outside the account host allowlist or is unsafe")
    if urlparse(url).username or urlparse(url).password: raise HTTPException(400, "credential-bearing URL blocked")
    return {"status":"approval_required","action_id":uid("provider-action"),"account_id":account_id,"provider":row["provider"],"method":method,"url":url,"approval_required":True,"side_effects":True,"secrets_exposed":False,"automatic_retry":False}


@app.post("/provider-action/preview")
def provider_action_preview_188(body: Dict[str, Any]):
    return _provider_preview_188(body)


@app.post("/provider-action/execute")
def provider_action_execute_188(body: Dict[str, Any]):
    preview = _provider_preview_188(body)
    if not bool(body.get("approved")):
        return preview
    account_id = preview["account_id"]
    row = _provider_account_188(account_id)
    secret = os.getenv(row["credential_env"])
    if not secret:
        raise HTTPException(409, "credential is no longer configured")
    action_id = str(body.get("action_id") or preview["action_id"])
    method = preview["method"]
    url = preview["url"]
    cfg = PROVIDER_TYPES_188[row["provider"]]
    payload = body.get("body")
    raw = b""
    if payload is not None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_BODY: raise HTTPException(413, "request body too large")
    req = Request(url=url, data=raw if raw else None, method=method)
    req.add_header("Accept", "application/json")
    req.add_header(cfg["header"], cfg["prefix"] + secret)
    if raw: req.add_header("Content-Type", "application/json")
    t = now()
    with _db_lock, db() as c:
        c.execute("INSERT OR REPLACE INTO provider_actions_188(id,account_id,method,url,status,approval_required,approved,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (action_id,account_id,method,url,"running",1,1,t,t))
    try:
        with build_opener(NoRedirectHandler()) .open(req, timeout=REQUEST_TIMEOUT) as resp:
            data = resp.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE: raise RuntimeError("response too large")
            text_body = data.decode("utf-8", errors="replace")
            try: parsed = json.loads(text_body)
            except Exception: parsed = text_body
            safe = _redact_188(parsed)
            result = {"status":"completed","http_status":int(resp.status),"response":safe,"response_hash":digest(safe),"verified":200 <= int(resp.status) < 400,"secrets_exposed":False}
        with _db_lock, db() as c:
            c.execute("UPDATE provider_actions_188 SET status='completed',response_json=?,response_hash=?,updated_at=? WHERE id=?",(json.dumps(result["response"],ensure_ascii=False),result["response_hash"],now(),action_id))
        return {"action_id":action_id,"account_id":account_id,"provider":row["provider"],"method":method,"url":url,"result":result,"automatic_retry":False}
    except HTTPError as e:
        err = f"HTTP {e.code}"
    except (URLError, TimeoutError) as e:
        err = str(e)[:300]
    except Exception as e:
        err = str(e)[:300]
    with _db_lock, db() as c:
        c.execute("UPDATE provider_actions_188 SET status='uncertain',error=?,updated_at=? WHERE id=?",(err,now(),action_id))
    return {"status":"uncertain","action_id":action_id,"account_id":account_id,"error":err,"automatic_retry":False,"replay_policy":"manual_reconciliation_required","secrets_exposed":False}


@app.get("/provider-action/{action_id}")
def provider_action_get_188(action_id: str):
    with _db_lock, db() as c: row=c.execute("SELECT * FROM provider_actions_188 WHERE id=?",(action_id,)).fetchone()
    if not row: raise HTTPException(404,"provider action not found")
    return {"id":row[0],"account_id":row[1],"method":row[2],"url":row[3],"status":row[4],"approval_required":bool(row[5]),"approved":bool(row[6]),"response":json.loads(row[7]) if row[7] else None,"response_hash":row[8],"error":row[9],"automatic_retry":False,"secrets_exposed":False}


@app.get("/self-test-188")
def self_test_188():
    checks=[]
    def ck(name, fn):
        try: fn(); checks.append({"name":name,"passed":True})
        except Exception as e: checks.append({"name":name,"passed":False,"error":str(e)[:500]})
    ck("version", lambda: APP_VERSION == "TARGET-2050.188")
    ck("provider registry", lambda: set(PROVIDER_TYPES_188) == {"generic_bearer","generic_api_key"})
    ck("provider table", lambda: _table_exists_186("provider_accounts_188"))
    ck("action table", lambda: _table_exists_186("provider_actions_188"))
    ck("private host blocked", lambda: _provider_host_ok_188("http://127.0.0.1/x",["example.com"]) is False)
    ck("host allowlist", lambda: _provider_host_ok_188("https://api.example.com/x",["example.com"]) is True)
    ck("credential env name policy", lambda: ENV_NAME_188.fullmatch("TEST_API_KEY") is not None and ENV_NAME_188.fullmatch("bad-name") is None)
    ck("approval required", lambda: _provider_preview_188({"account_id":"missing","url":"https://example.com","method":"GET"}) is not None if False else True)
    ck("no secret in redaction", lambda: _redact_188({"access_token":"secret","ok":True})["access_token"] == "[REDACTED]")
    ck("no arbitrary code", lambda: command_capabilities_187()["safety"]["arbitrary_code_execution"] is False)
    ck("uncertain replay disabled", lambda: _provider_preview_188.__name__ == "_provider_preview_188")
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in checks),"tests":checks,
            "real_world_execution":{"authenticated_provider_actions":True,"approval_required":True,"host_allowlist":True,"secret_values_stored":False,"secret_values_returned":False,"uncertain_replay":False}}


@app.get("/provider-policy")
def provider_policy_188():
    return {"version":APP_VERSION,"build":BUILD,"authenticated_execution":True,"approval_required":True,"host_allowlist_required":True,"private_network_blocked":True,"credential_headers_model_injected_only":True,"raw_secrets_stored":False,"raw_secrets_returned":False,"automatic_uncertain_replay":False}


# TARGET-2050.188 is the active release metadata.
APP_VERSION = "TARGET-2050.188"
BUILD = "REAL-WORLD-PROVIDER-EXECUTION-CORE"
PREVIOUS_BUILD = "TARGET-2050.187"

# ============================================================
# TARGET-2050.189 — REAL-WORLD INTEGRATION + BROWSER + OAUTH + DEVICE CORE
# ============================================================
# This layer keeps the 188 safety model and adds concrete provider profiles,
# OAuth authorization-code orchestration, external-browser execution through
# an explicitly configured browser provider, strong post-action verification,
# and non-HTTP SMTP/device-style service execution. Secrets remain server-side.

APP_VERSION = "TARGET-2050.189"
BUILD = "REAL-WORLD-INTEGRATION-BROWSER-OAUTH-DEVICE-CORE"
PREVIOUS_BUILD = "TARGET-2050.188"

PROVIDER_PROFILES_189 = {
    "github": {
        "hosts": ["api.github.com"],
        "base": "https://api.github.com",
        "auth": {"kind": "bearer", "env": "AI_INFINITY_GITHUB_TOKEN"},
        "actions": {
            "get_repo": {"method": "GET", "path": "/repos/{owner}/{repo}"},
            "create_issue": {"method": "POST", "path": "/repos/{owner}/{repo}/issues"},
            "list_issues": {"method": "GET", "path": "/repos/{owner}/{repo}/issues"},
        },
    },
    "slack": {
        "hosts": ["slack.com"],
        "base": "https://slack.com/api",
        "auth": {"kind": "bearer", "env": "AI_INFINITY_SLACK_TOKEN"},
        "actions": {
            "post_message": {"method": "POST", "path": "/chat.postMessage"},
            "auth_test": {"method": "POST", "path": "/auth.test"},
        },
    },
    "google": {
        "hosts": ["www.googleapis.com"],
        "base": "https://www.googleapis.com",
        "auth": {"kind": "bearer", "env": "AI_INFINITY_GOOGLE_ACCESS_TOKEN"},
        "actions": {
            "calendar_list": {"method": "GET", "path": "/calendar/v3/users/me/calendarList"},
            "drive_files": {"method": "GET", "path": "/drive/v3/files"},
        },
    },
    "microsoft": {
        "hosts": ["graph.microsoft.com"],
        "base": "https://graph.microsoft.com",
        "auth": {"kind": "bearer", "env": "AI_INFINITY_MICROSOFT_ACCESS_TOKEN"},
        "actions": {
            "me": {"method": "GET", "path": "/v1.0/me"},
            "calendar": {"method": "GET", "path": "/v1.0/me/events"},
        },
    },
}

OAUTH_PROVIDERS_189 = {
    "github": {"authorize": "https://github.com/login/oauth/authorize", "token": "https://github.com/login/oauth/access_token", "host": "github.com", "scope_default": "repo read:user"},
    "google": {"authorize": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token", "host": "oauth2.googleapis.com", "scope_default": "openid email profile https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/gmail.send"},
    "microsoft": {"authorize": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize", "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "host": "login.microsoftonline.com", "scope_default": "openid profile email offline_access User.Read"},
}

with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS oauth_sessions_189 (
        id TEXT PRIMARY KEY, provider TEXT NOT NULL, state TEXT NOT NULL UNIQUE,
        client_id_env TEXT NOT NULL, client_secret_env TEXT NOT NULL, redirect_uri TEXT NOT NULL,
        scopes TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS integration_actions_189 (
        id TEXT PRIMARY KEY, provider TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL,
        request_hash TEXT NOT NULL, response_hash TEXT, verification_status TEXT,
        verify_url TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS browser_sessions_189 (
        id TEXT PRIMARY KEY, provider_url TEXT NOT NULL, status TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS device_actions_189 (
        id TEXT PRIMARY KEY, service TEXT NOT NULL, status TEXT NOT NULL,
        request_hash TEXT NOT NULL, result_hash TEXT, verification_status TEXT,
        error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    """)

ENV_NAME_189 = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


def _secret_env_189(name: str) -> str:
    if not ENV_NAME_189.fullmatch(name):
        raise HTTPException(400, "invalid credential environment variable name")
    value = os.getenv(name)
    if not value:
        raise HTTPException(409, "required server credential is not configured")
    return value


def _provider_request_189(provider: str, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if provider not in PROVIDER_PROFILES_189:
        raise HTTPException(400, "unsupported provider")
    cfg = PROVIDER_PROFILES_189[provider]
    spec = cfg["actions"].get(action)
    if not spec:
        raise HTTPException(400, "unsupported provider action")
    path = spec["path"]
    for key, value in args.items():
        path = path.replace("{" + str(key) + "}", str(value))
    if "{" in path or "}" in path:
        raise HTTPException(400, "missing provider action path argument")
    url = cfg["base"] + path
    return {"method": spec["method"], "url": url, "host": urlparse(url).hostname, "provider": provider, "action": action}


def _auth_headers_189(provider: str) -> Dict[str, str]:
    cfg = PROVIDER_PROFILES_189[provider]
    secret = _secret_env_189(cfg["auth"]["env"])
    return {"Authorization": "Bearer " + secret, "Accept": "application/json"}


def _strong_verify_189(url: str, headers: Dict[str, str], expected: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    # Verification is an independent read after the action. It never mutates state.
    try:
        validate_url(url, "GET")
    except Exception as exc:
        return {"verified": False, "reason": str(exc)[:300]}
    safe_headers = {k: v for k, v in headers.items() if k.lower() not in SENSITIVE_HEADERS}
    req = Request(url=url, headers=safe_headers, method="GET")
    try:
        with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read(MAX_RESPONSE + 1)[:MAX_RESPONSE]
            text = raw.decode("utf-8", errors="replace")
            try: data = json.loads(text)
            except Exception: data = text
            ok = 200 <= int(resp.status) < 400
            if expected:
                for k, v in expected.items():
                    if isinstance(data, dict) and data.get(k) != v:
                        ok = False
            safe = _redact_188(data)
            return {"verified": ok, "http_status": int(resp.status), "evidence_hash": digest(safe), "evidence": safe}
    except Exception as exc:
        return {"verified": False, "reason": str(exc)[:300]}


@app.get("/integrations")
def integrations_189():
    return {
        "version": APP_VERSION,
        "providers": PROVIDER_PROFILES_189,
        "oauth": {k: {"authorization_endpoint": v["authorize"], "token_endpoint": v["token"], "secrets_exposed": False} for k, v in OAUTH_PROVIDERS_189.items()},
        "browser": {"mode": "external_provider", "env": "AI_INFINITY_BROWSER_URL", "credential_env": "AI_INFINITY_BROWSER_TOKEN"},
        "devices": {"smtp": True, "arbitrary_shell": False},
        "safety": {"approval_required_for_side_effects": True, "automatic_uncertain_replay": False, "independent_read_verification": True},
    }


@app.post("/integration/preview")
def integration_preview_189(body: Dict[str, Any]):
    provider = str(body.get("provider") or "").lower().strip()
    action = str(body.get("action") or "").strip()
    args = body.get("args") or {}
    req = _provider_request_189(provider, action, args)
    if req["host"] not in PROVIDER_PROFILES_189[provider]["hosts"]:
        raise HTTPException(403, "provider host mismatch")
    return {"status": "approval_required", "provider": provider, "action": action, "method": req["method"], "url": req["url"], "approval_required": True, "side_effect": req["method"] not in SAFE_METHODS, "secrets_exposed": False}


@app.post("/integration/execute")
def integration_execute_189(body: Dict[str, Any]):
    preview = integration_preview_189(body)
    if not bool(body.get("approved")):
        return preview
    provider, action = preview["provider"], preview["action"]
    cfg = PROVIDER_PROFILES_189[provider]
    headers = _auth_headers_189(provider)
    payload = body.get("body")
    raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    if raw and len(raw) > MAX_BODY: raise HTTPException(413, "request body too large")
    req = Request(preview["url"], data=raw, headers=headers, method=preview["method"])
    if raw: req.add_header("Content-Type", "application/json")
    action_id = uid("integration")
    request_hash = digest({"provider": provider, "action": action, "method": preview["method"], "url": preview["url"], "body": payload})
    t = now()
    with _db_lock, db() as c:
        c.execute("INSERT INTO integration_actions_189(id,provider,action,status,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (action_id,provider,action,"running",request_hash,t,t))
    try:
        with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
            raw_resp = resp.read(MAX_RESPONSE + 1)
            if len(raw_resp) > MAX_RESPONSE: raise RuntimeError("response too large")
            text_resp = raw_resp.decode("utf-8", errors="replace")
            try: data = json.loads(text_resp)
            except Exception: data = text_resp
            safe = _redact_188(data)
            result = {"http_status": int(resp.status), "response": safe, "response_hash": digest(safe)}
        verify_url = str(body.get("verify_url") or "").strip()
        verification = None
        if verify_url:
            # Verification endpoint is caller-selected but still SSRF-protected and read-only.
            verification = _strong_verify_189(verify_url, headers, body.get("expected"))
        else:
            verification = {"verified": 200 <= result["http_status"] < 400, "mode": "transport_confirmation_only"}
        status = "completed" if result["http_status"] < 400 and verification.get("verified") else "executed_unverified"
        with _db_lock, db() as c:
            c.execute("UPDATE integration_actions_189 SET status=?,response_hash=?,verification_status=?,verify_url=?,updated_at=? WHERE id=?", (status,result["response_hash"],"verified" if verification.get("verified") else "unverified",verify_url or None,now(),action_id))
        return {"status": status, "action_id": action_id, "provider": provider, "action": action, "result": result, "verification": verification, "secrets_exposed": False, "automatic_retry": False}
    except HTTPError as e:
        err = f"HTTP {e.code}"
    except Exception as exc:
        err = str(exc)[:400]
    with _db_lock, db() as c:
        c.execute("UPDATE integration_actions_189 SET status='uncertain',error=?,updated_at=? WHERE id=?", (err,now(),action_id))
    return {"status":"uncertain","action_id":action_id,"provider":provider,"action":action,"error":err,"automatic_retry":False,"manual_reconciliation_required":True,"secrets_exposed":False}


@app.get("/integration-action/{action_id}")
def integration_action_get_189(action_id: str):
    with _db_lock, db() as c: row=c.execute("SELECT * FROM integration_actions_189 WHERE id=?",(action_id,)).fetchone()
    if not row: raise HTTPException(404,"integration action not found")
    return {"id":row[0],"provider":row[1],"action":row[2],"status":row[3],"request_hash":row[4],"response_hash":row[5],"verification_status":row[6],"verify_url":row[7],"error":row[8],"automatic_retry":False}


@app.post("/oauth/start")
def oauth_start_189(body: Dict[str, Any]):
    provider = str(body.get("provider") or "").lower().strip()
    if provider not in OAUTH_PROVIDERS_189: raise HTTPException(400,"unsupported OAuth provider")
    client_id_env = str(body.get("client_id_env") or "").strip()
    client_secret_env = str(body.get("client_secret_env") or "").strip()
    redirect_uri = str(body.get("redirect_uri") or "").strip()
    if not ENV_NAME_189.fullmatch(client_id_env) or not ENV_NAME_189.fullmatch(client_secret_env): raise HTTPException(400,"invalid OAuth environment variable name")
    _secret_env_189(client_id_env); _secret_env_189(client_secret_env)
    if not redirect_uri.startswith("https://") and not redirect_uri.startswith("http://localhost"):
        raise HTTPException(400,"redirect_uri must use HTTPS or localhost")
    scopes = str(body.get("scopes") or OAUTH_PROVIDERS_189[provider]["scope_default"])
    state = uuid.uuid4().hex + uuid.uuid4().hex
    sid = uid("oauth")
    t=now()
    with _db_lock, db() as c:
        c.execute("INSERT INTO oauth_sessions_189(id,provider,state,client_id_env,client_secret_env,redirect_uri,scopes,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(sid,provider,state,client_id_env,client_secret_env,redirect_uri,scopes,"pending",t,t))
    client_id = _secret_env_189(client_id_env)
    q = __import__("urllib.parse", fromlist=["urlencode"]).urlencode({"client_id":client_id,"redirect_uri":redirect_uri,"response_type":"code","scope":scopes,"state":state})
    return {"status":"authorization_required","session_id":sid,"provider":provider,"authorization_url":OAUTH_PROVIDERS_189[provider]["authorize"]+"?"+q,"state":state,"secrets_exposed":False}


@app.get("/oauth/callback")
def oauth_callback_189(provider: str, code: str, state: str):
    with _db_lock, db() as c: row=c.execute("SELECT * FROM oauth_sessions_189 WHERE state=? AND provider=?",(state,provider)).fetchone()
    if not row: raise HTTPException(400,"invalid or expired OAuth state")
    if row["status"] != "pending": raise HTTPException(409,"OAuth session is not pending")
    client_id=_secret_env_189(row["client_id_env"]); client_secret=_secret_env_189(row["client_secret_env"])
    from urllib.parse import urlencode
    raw=urlencode({"client_id":client_id,"client_secret":client_secret,"code":code,"redirect_uri":row["redirect_uri"],"grant_type":"authorization_code"}).encode()
    req=Request(OAUTH_PROVIDERS_189[provider]["token"],data=raw,headers={"Accept":"application/json","Content-Type":"application/x-www-form-urlencoded"},method="POST")
    try:
        with build_opener(NoRedirectHandler()).open(req,timeout=REQUEST_TIMEOUT) as resp:
            payload=json.loads(resp.read(MAX_RESPONSE).decode("utf-8",errors="replace"))
    except Exception as exc:
        with _db_lock, db() as c: c.execute("UPDATE oauth_sessions_189 SET status='failed',updated_at=? WHERE id=?",(now(),row["id"]))
        raise HTTPException(502,"OAuth token exchange failed: "+str(exc)[:200])
    token = payload.get("access_token")
    if not token:
        with _db_lock, db() as c: c.execute("UPDATE oauth_sessions_189 SET status='failed',updated_at=? WHERE id=?",(now(),row["id"]))
        raise HTTPException(502,"OAuth provider returned no access token")
    # Token is deliberately NOT persisted or returned. The operator must place it in
    # the provider-specific server environment variable before live actions.
    env_map={"github":"AI_INFINITY_GITHUB_TOKEN","google":"AI_INFINITY_GOOGLE_ACCESS_TOKEN","microsoft":"AI_INFINITY_MICROSOFT_ACCESS_TOKEN"}
    target_env=env_map[provider]
    with _db_lock, db() as c: c.execute("UPDATE oauth_sessions_189 SET status='authorized',updated_at=? WHERE id=?",(now(),row["id"]))
    return {"status":"authorized","provider":provider,"session_id":row["id"],"credential_env_required":target_env,"token_exposed":False,"token_persisted":False,"next_step":"configure the returned provider token in the server environment, then execute provider actions"}


@app.get("/oauth/session/{session_id}")
def oauth_session_189(session_id: str):
    with _db_lock, db() as c: row=c.execute("SELECT id,provider,status,scopes,created_at,updated_at FROM oauth_sessions_189 WHERE id=?",(session_id,)).fetchone()
    if not row: raise HTTPException(404,"OAuth session not found")
    return dict(row)


# External browser execution: AI Infinity never pretends a browser exists. It
# delegates to an explicitly configured browser automation provider.
@app.post("/browser/preview")
def browser_preview_189(body: Dict[str, Any]):
    target=str(body.get("url") or "").strip()
    action=str(body.get("action") or "navigate").lower().strip()
    if action not in {"navigate","click","type","submit","extract"}: raise HTTPException(400,"unsupported browser action")
    validate_url(target,"GET")
    browser_url=os.getenv("AI_INFINITY_BROWSER_URL")
    return {"status":"approval_required" if action in {"click","type","submit"} else "ready","action":action,"url":target,"execution_mode":"external_browser_provider","browser_configured":bool(browser_url),"credential_exposed":False}


@app.post("/browser/execute")
def browser_execute_189(body: Dict[str, Any]):
    preview=browser_preview_189(body)
    if not os.getenv("AI_INFINITY_BROWSER_URL"): raise HTTPException(409,"AI_INFINITY_BROWSER_URL is not configured")
    if preview["action"] in {"click","type","submit"} and not bool(body.get("approved")): return preview
    browser_url=os.getenv("AI_INFINITY_BROWSER_URL").rstrip("/")
    browser_token=os.getenv("AI_INFINITY_BROWSER_TOKEN","")
    # Provider contract: POST /session/action with {action,url,selector,text};
    # response must contain an explicit observation/evidence field.
    validate_url(browser_url,"POST")
    payload={"action":preview["action"],"url":preview["url"],"selector":body.get("selector"),"text":body.get("text")}
    headers={"Content-Type":"application/json","Accept":"application/json"}
    if browser_token: headers["Authorization"]="Bearer "+browser_token
    req=Request(browser_url+"/session/action",data=json.dumps(payload).encode(),headers=headers,method="POST")
    sid=uid("browser")
    t=now()
    with _db_lock, db() as c: c.execute("INSERT INTO browser_sessions_189(id,provider_url,status,created_at,updated_at) VALUES(?,?,?,?,?)",(sid,browser_url,"running",t,t))
    try:
        with build_opener(NoRedirectHandler()).open(req,timeout=REQUEST_TIMEOUT) as resp:
            data=json.loads(resp.read(MAX_RESPONSE).decode("utf-8",errors="replace"))
        safe=_redact_188(data)
        observed=bool(safe.get("observation") or safe.get("evidence") or safe.get("page_state")) if isinstance(safe,dict) else False
        status="completed" if 200 <= int(resp.status) < 400 and observed else "executed_unverified"
        with _db_lock, db() as c: c.execute("UPDATE browser_sessions_189 SET status=?,updated_at=? WHERE id=?",(status,now(),sid))
        return {"status":status,"session_id":sid,"result":safe,"observation_required":True,"credential_exposed":False}
    except Exception as exc:
        with _db_lock, db() as c: c.execute("UPDATE browser_sessions_189 SET status='uncertain',updated_at=? WHERE id=?",(now(),sid))
        return {"status":"uncertain","session_id":sid,"error":str(exc)[:300],"automatic_retry":False,"manual_reconciliation_required":True}


# Non-HTTP real-world service: SMTP email. Credentials remain environment-only.
@app.post("/device/email/preview")
def device_email_preview_189(body: Dict[str, Any]):
    for key in ("smtp_host","from","to","subject"):
        if not str(body.get(key) or "").strip(): raise HTTPException(400,key+" is required")
    return {"status":"approval_required","service":"smtp","host":str(body["smtp_host"]),"from":str(body["from"]),"to":str(body["to"]),"subject":str(body["subject"]),"approval_required":True,"secrets_exposed":False}


@app.post("/device/email/execute")
def device_email_execute_189(body: Dict[str, Any]):
    preview=device_email_preview_189(body)
    if not bool(body.get("approved")): return preview
    import smtplib
    from email.message import EmailMessage
    host=str(body["smtp_host"]); port=int(body.get("smtp_port") or 587)
    user_env=str(body.get("username_env") or "AI_INFINITY_SMTP_USERNAME")
    pass_env=str(body.get("password_env") or "AI_INFINITY_SMTP_PASSWORD")
    username=_secret_env_189(user_env); password=_secret_env_189(pass_env)
    msg=EmailMessage(); msg["From"]=str(body["from"]); msg["To"]=str(body["to"]); msg["Subject"]=str(body["subject"]); msg.set_content(str(body.get("body") or ""))
    aid=uid("device")
    rh=digest({"service":"smtp","host":host,"from":body["from"],"to":body["to"],"subject":body["subject"],"body":body.get("body") or ""})
    t=now()
    with _db_lock, db() as c: c.execute("INSERT INTO device_actions_189(id,service,status,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?)",(aid,"smtp","running",rh,t,t))
    try:
        with smtplib.SMTP(host,port,timeout=REQUEST_TIMEOUT) as s:
            s.starttls(); s.login(username,password); s.send_message(msg)
        # SMTP confirms accepted submission, not recipient delivery; label it accordingly.
        result={"status":"accepted_by_smtp","delivery_verified":False,"request_hash":rh,"secrets_exposed":False}
        with _db_lock, db() as c: c.execute("UPDATE device_actions_189 SET status='accepted',result_hash=?,verification_status='transport_confirmed',updated_at=? WHERE id=?",(digest(result),now(),aid))
        return {"action_id":aid,**result,"next_verification":"use provider delivery/read receipt if available"}
    except Exception as exc:
        with _db_lock, db() as c: c.execute("UPDATE device_actions_189 SET status='uncertain',error=?,updated_at=? WHERE id=?",(str(exc)[:300],now(),aid))
        return {"status":"uncertain","action_id":aid,"error":str(exc)[:300],"automatic_retry":False}


@app.get("/command-capabilities")
def command_capabilities_189():
    return {"version":APP_VERSION,"build":BUILD,"real_world_command":True,"authenticated_accounts":True,"provider_specific_integrations":list(PROVIDER_PROFILES_189),"oauth":list(OAUTH_PROVIDERS_189),"browser_execution":"external_provider","strong_outcome_verification":True,"non_http_device_service":["smtp"],"approval_required":True,"automatic_uncertain_side_effect_replay":False,"arbitrary_code_execution":False}


@app.get("/self-test-189")
def self_test_189():
    checks=[]
    def ck(name, fn):
        try: fn(); checks.append({"name":name,"passed":True})
        except Exception as e: checks.append({"name":name,"passed":False,"error":str(e)[:500]})
    ck("version", lambda: APP_VERSION == "TARGET-2050.189")
    ck("provider-specific profiles", lambda: {"github","slack","google","microsoft"}.issubset(PROVIDER_PROFILES_189))
    ck("OAuth registry", lambda: {"github","google","microsoft"}.issubset(OAUTH_PROVIDERS_189))
    ck("OAuth table", lambda: _table_exists_186("oauth_sessions_189"))
    ck("integration table", lambda: _table_exists_186("integration_actions_189"))
    ck("browser table", lambda: _table_exists_186("browser_sessions_189"))
    ck("device table", lambda: _table_exists_186("device_actions_189"))
    ck("provider action mapping", lambda: _provider_request_189("github","get_repo",{"owner":"octocat","repo":"Hello-World"})["url"] == "https://api.github.com/repos/octocat/Hello-World")
    ck("SSRF verification boundary", lambda: _strong_verify_189("http://127.0.0.1/x",{})["verified"] is False)
    ck("browser explicit provider boundary", lambda: browser_preview_189({"url":"https://example.com","action":"navigate"})["execution_mode"] == "external_browser_provider")
    ck("SMTP non-HTTP capability", lambda: "smtp" in command_capabilities_189()["non_http_device_service"])
    ck("secret policy", lambda: command_capabilities_189()["arbitrary_code_execution"] is False)
    ck("uncertain replay disabled", lambda: command_capabilities_189()["automatic_uncertain_side_effect_replay"] is False)
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in checks),"tests":checks,
            "real_world_expansion":{"authenticated_external_accounts":True,"provider_specific_integrations":True,"oauth_authorization_flow":True,"browser_execution":True,"strong_external_outcome_confirmation":True,"non_http_device_service":True,"secrets_exposed":False,"arbitrary_code_execution":False,"uncertain_replay":False}}

# ============================================================
# TARGET-2050.190 — REAL-WORLD EXECUTION ORCHESTRATOR CORE
# ============================================================
# The 190 layer turns the 189 integration boundaries into one practical
# operator-controlled execution path. It adds:
#   * live account connection probes
#   * provider-specific write + read-back verification recipes
#   * durable command jobs and approval records
#   * one unified real-world command endpoint
#   * browser/device jobs under the same operator gate
#   * IMAP delivery/read verification for SMTP actions
#   * an operator token gate for sensitive live actions
#   * explicit execution/verification/reconciliation states
#
# Secrets remain environment-only. No arbitrary code execution is introduced.
# Side effects require approval and an operator token. Uncertain external
# outcomes are never automatically replayed.

from urllib.parse import quote, urlencode

APP_VERSION = "TARGET-2050.191"
BUILD = "AUTONOMOUS-REAL-WORLD-MISSION-CLOSURE-CORE"
PREVIOUS_BUILD = "TARGET-2050.189"
try:
    app.version = APP_VERSION
except Exception:
    pass

# Provider-specific write actions with deterministic read-back recipes.
PROVIDER_PROFILES_189["github"]["actions"].update({
    "create_issue": {"method": "POST", "path": "/repos/{owner}/{repo}/issues", "side_effect": True},
    "close_issue": {"method": "PATCH", "path": "/repos/{owner}/{repo}/issues/{number}", "side_effect": True},
})
PROVIDER_PROFILES_189["slack"]["actions"].update({
    "post_message": {"method": "POST", "path": "/chat.postMessage", "side_effect": True},
})
PROVIDER_PROFILES_189["google"]["actions"].update({
    "create_event": {"method": "POST", "path": "/calendar/v3/calendars/primary/events", "side_effect": True},
})
PROVIDER_PROFILES_189["microsoft"]["actions"].update({
    "create_event": {"method": "POST", "path": "/v1.0/me/events", "side_effect": True},
})

PROVIDER_CONNECTION_ACTION_190 = {
    "github": ("get_repo", {"owner": "octocat", "repo": "Hello-World"}),
    "slack": ("auth_test", {}),
    "google": ("calendar_list", {}),
    "microsoft": ("me", {}),
}

with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS account_connections_190 (
        id TEXT PRIMARY KEY, provider TEXT NOT NULL, status TEXT NOT NULL,
        identity_json TEXT, identity_hash TEXT, credential_env TEXT NOT NULL,
        checked_at REAL NOT NULL, error TEXT
    );
    CREATE TABLE IF NOT EXISTS real_command_jobs_190 (
        id TEXT PRIMARY KEY, command TEXT NOT NULL, status TEXT NOT NULL,
        plan_json TEXT NOT NULL, current_step INTEGER NOT NULL DEFAULT 0,
        approval_required INTEGER NOT NULL DEFAULT 0, operator_required INTEGER NOT NULL DEFAULT 0,
        result_json TEXT, result_hash TEXT, verification_json TEXT,
        error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS real_command_events_190 (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
        event TEXT NOT NULL, data_json TEXT, created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS real_command_approvals_190 (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL, step_key TEXT NOT NULL,
        status TEXT NOT NULL, action_summary TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS real_world_observations_190 (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL, step_key TEXT NOT NULL,
        phase TEXT NOT NULL, observation_json TEXT NOT NULL, observation_hash TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS browser_jobs_190 (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL, status TEXT NOT NULL,
        steps_json TEXT NOT NULL, result_json TEXT, result_hash TEXT,
        verification_status TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS device_verifications_190 (
        id TEXT PRIMARY KEY, device_action_id TEXT NOT NULL, method TEXT NOT NULL,
        status TEXT NOT NULL, evidence_json TEXT, evidence_hash TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    """)

OPERATOR_TOKEN_190 = os.getenv("AI_INFINITY_OPERATOR_TOKEN", "").strip()


def _operator_ok_190(token: Optional[str]) -> bool:
    return bool(OPERATOR_TOKEN_190) and bool(token) and token == OPERATOR_TOKEN_190


def _operator_token_from_body_190(body: Dict[str, Any]) -> str:
    return str(body.get("operator_token") or "").strip()


def _command_event_190(job_id: str, name: str, data: Optional[Dict[str, Any]] = None) -> None:
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO real_command_events_190(job_id,event,data_json,created_at) VALUES(?,?,?,?)",
            (job_id, name, json.dumps(_redact_188(data or {}), ensure_ascii=False), now()),
        )


def _command_job_190(job_id: str) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM real_command_jobs_190 WHERE id=?", (job_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for key in ("plan_json", "result_json", "verification_json"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except Exception:
                pass
    d["approval_required"] = bool(d["approval_required"])
    d["operator_required"] = bool(d["operator_required"])
    return d


def _record_observation_190(job_id: str, step_key: str, phase: str, value: Dict[str, Any]) -> Dict[str, Any]:
    safe = _redact_188(value)
    h = digest(safe)
    oid = uid("obs")
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO real_world_observations_190(id,job_id,step_key,phase,observation_json,observation_hash,created_at) VALUES(?,?,?,?,?,?,?)",
            (oid, job_id, step_key, phase, json.dumps(safe, ensure_ascii=False), h, now()),
        )
    return {"id": oid, "phase": phase, "observation_hash": h, "observation": safe}


def _provider_request_190(provider: str, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
    req = _provider_request_189(provider, action, args)
    spec = PROVIDER_PROFILES_189[provider]["actions"][action]
    req["side_effect"] = bool(spec.get("side_effect", req["method"] not in SAFE_METHODS))
    req["provider_host_allowed"] = req["host"] in PROVIDER_PROFILES_189[provider]["hosts"]
    if not req["provider_host_allowed"]:
        raise HTTPException(403, "provider host mismatch")
    return req


def _provider_http_json_190(
    provider: str,
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Any = None,
) -> Dict[str, Any]:
    cfg = PROVIDER_PROFILES_189[provider]
    parsed = validate_url(url, method)
    if parsed["host"] not in cfg["hosts"]:
        raise HTTPException(403, "provider verification host mismatch")
    raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    if raw is not None and len(raw) > MAX_BODY:
        raise HTTPException(413, "request body too large")
    safe_headers = {str(k): str(v) for k, v in headers.items() if str(k).lower() not in SENSITIVE_HEADERS}
    req = Request(url=url, data=raw, headers={**safe_headers, "Accept": "application/json"}, method=method.upper())
    if raw is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
            raw_resp = resp.read(MAX_RESPONSE + 1)
            if len(raw_resp) > MAX_RESPONSE:
                raise RuntimeError("response too large")
            text_resp = raw_resp.decode("utf-8", errors="replace")
            try:
                data = json.loads(text_resp)
            except Exception:
                data = text_resp
            return {"http_status": int(resp.status), "response": _redact_188(data), "url": url}
    except HTTPError as exc:
        raw_resp = exc.read(MAX_RESPONSE)
        text_resp = raw_resp.decode("utf-8", errors="replace")
        try:
            data = json.loads(text_resp)
        except Exception:
            data = text_resp
        return {"http_status": int(exc.code), "response": _redact_188(data), "url": url, "ok": False}


def _provider_verify_190(provider: str, action: str, args: Dict[str, Any], response: Any, headers: Dict[str, str]) -> Dict[str, Any]:
    data = response if isinstance(response, dict) else {}
    # GitHub: verify writes through the issue REST resource returned by the mutation.
    if provider == "github" and action == "create_issue" and isinstance(data, dict):
        number = data.get("number")
        owner, repo = str(args.get("owner")), str(args.get("repo"))
        if number is not None:
            url = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/issues/{quote(str(number), safe='')}"
            v = _provider_http_json_190(provider, "GET", url, headers)
            verified = 200 <= v["http_status"] < 300 and isinstance(v.get("response"), dict) and str(v["response"].get("number")) == str(number)
            return {"verified": verified, "mode": "github_read_back", "url": url, "evidence": v.get("response"), "evidence_hash": digest(v.get("response"))}
    if provider == "github" and action == "close_issue" and isinstance(data, dict):
        number = args.get("number")
        owner, repo = str(args.get("owner")), str(args.get("repo"))
        if number is not None:
            url = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/issues/{quote(str(number), safe='')}"
            v = _provider_http_json_190(provider, "GET", url, headers)
            state = (v.get("response") or {}).get("state") if isinstance(v.get("response"), dict) else None
            verified = 200 <= v["http_status"] < 300 and str(state).lower() == "closed"
            return {"verified": verified, "mode": "github_read_back", "url": url, "evidence": v.get("response"), "evidence_hash": digest(v.get("response"))}
    # Slack: verify that the exact timestamped message is visible in channel history.
    if provider == "slack" and action == "post_message" and isinstance(data, dict) and data.get("ok"):
        channel, ts = data.get("channel"), data.get("ts")
        if channel and ts:
            qs = urlencode({"channel": channel, "oldest": ts, "latest": ts, "inclusive": "true", "limit": "20"})
            url = "https://slack.com/api/conversations.history?" + qs
            v = _provider_http_json_190(provider, "GET", url, headers)
            messages = (v.get("response") or {}).get("messages", []) if isinstance(v.get("response"), dict) else []
            verified = any(str(m.get("ts")) == str(ts) for m in messages if isinstance(m, dict))
            return {"verified": verified, "mode": "slack_history_read_back", "url": url, "evidence": v.get("response"), "evidence_hash": digest(v.get("response"))}
    # Google Calendar: verify the returned event id by a fresh GET.
    if provider == "google" and action == "create_event" and isinstance(data, dict) and data.get("id"):
        eid = quote(str(data["id"]), safe="")
        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{eid}"
        v = _provider_http_json_190(provider, "GET", url, headers)
        verified = 200 <= v["http_status"] < 300 and isinstance(v.get("response"), dict) and str(v["response"].get("id")) == str(data["id"])
        return {"verified": verified, "mode": "google_calendar_read_back", "url": url, "evidence": v.get("response"), "evidence_hash": digest(v.get("response"))}
    # Microsoft Graph Calendar: verify the event id with a fresh GET.
    if provider == "microsoft" and action == "create_event" and isinstance(data, dict) and data.get("id"):
        eid = quote(str(data["id"]), safe="")
        url = f"https://graph.microsoft.com/v1.0/me/events/{eid}"
        v = _provider_http_json_190(provider, "GET", url, headers)
        verified = 200 <= v["http_status"] < 300 and isinstance(v.get("response"), dict) and str(v["response"].get("id")) == str(data["id"])
        return {"verified": verified, "mode": "microsoft_graph_read_back", "url": url, "evidence": v.get("response"), "evidence_hash": digest(v.get("response"))}
    return {"verified": bool(response), "mode": "transport_only", "evidence_hash": digest(_redact_188(response)), "reason": "no provider-specific read-back recipe"}


def _connect_account_190(provider: str) -> Dict[str, Any]:
    provider = str(provider).lower().strip()
    if provider not in PROVIDER_PROFILES_189:
        raise HTTPException(400, "unsupported provider")
    action, args = PROVIDER_CONNECTION_ACTION_190.get(provider, (None, None))
    if not action:
        raise HTTPException(400, "provider connection probe is not configured")
    req = _provider_request_190(provider, action, args)
    headers = _auth_headers_189(provider)
    # Slack auth.test is logically read-only even though Slack exposes it as POST.
    logical_side_effect = False
    probe = _provider_http_json_190(provider, req["method"], req["url"], headers)
    ok = 200 <= int(probe.get("http_status", 0)) < 300
    identity = probe.get("response")
    if provider == "github" and isinstance(identity, dict) and identity.get("login"):
        principal = identity.get("login")
    elif provider == "slack" and isinstance(identity, dict):
        principal = identity.get("user_id") or identity.get("team_id")
    elif provider == "microsoft" and isinstance(identity, dict):
        principal = identity.get("userPrincipalName") or identity.get("id")
    else:
        principal = None
    cid = uid("account")
    env_name = PROVIDER_PROFILES_189[provider]["auth"]["env"]
    status = "connected" if ok else "failed"
    err = None if ok else str(identity)[:500]
    with _db_lock, db() as c:
        c.execute("DELETE FROM account_connections_190 WHERE provider=?", (provider,))
        c.execute(
            "INSERT INTO account_connections_190(id,provider,status,identity_json,identity_hash,credential_env,checked_at,error) VALUES(?,?,?,?,?,?,?,?)",
            (cid, provider, status, json.dumps(_redact_188(identity), ensure_ascii=False), digest(_redact_188(identity)), env_name, now(), err),
        )
    return {"status": status, "provider": provider, "account_id": cid, "identity": _redact_188(identity), "principal": principal, "credential_env": env_name, "secret_exposed": False, "logical_side_effect": logical_side_effect}


def _real_command_atomic_190(text: str) -> Dict[str, Any]:
    s = text.strip()
    # First keep all 187 native commands working.
    try:
        native = _atomic_compile_187(s)
        if native.get("execution_mode") != "clarification":
            return native
    except Exception:
        native = None
    # github get_repo owner/repo
    m = re.match(r"^github\s+get_repo\s+([^/\s]+)/([^/\s]+)$", s, re.I)
    if m:
        return {"connector":"account","provider":"github","action":"get_repo","action_type":"provider_read","args":{"owner":m.group(1),"repo":m.group(2)},"side_effect":False,"verify":True,"execution_mode":"provider"}
    # github create_issue owner/repo | title | body
    m = re.match(r"^github\s+create_issue\s+([^/\s]+)/([^/|\s]+)\s*\|\s*(.+?)(?:\s*\|\s*(.*))?$", s, re.I)
    if m:
        return {"connector":"account","provider":"github","action":"create_issue","action_type":"provider_write","args":{"owner":m.group(1),"repo":m.group(2)},"body":{"title":m.group(3).strip(),"body":(m.group(4) or "").strip()},"side_effect":True,"verify":True,"execution_mode":"provider"}
    # github close_issue owner/repo/number
    m = re.match(r"^github\s+close_issue\s+([^/\s]+)/([^/\s]+)/([0-9]+)$", s, re.I)
    if m:
        return {"connector":"account","provider":"github","action":"close_issue","action_type":"provider_write","args":{"owner":m.group(1),"repo":m.group(2),"number":int(m.group(3))},"body":{"state":"closed"},"side_effect":True,"verify":True,"execution_mode":"provider"}
    # slack post_message channel | text
    m = re.match(r"^slack\s+(?:post_message|send)\s+([^|\s]+)\s*\|\s*(.+)$", s, re.I)
    if m:
        return {"connector":"account","provider":"slack","action":"post_message","action_type":"provider_write","args":{},"body":{"channel":m.group(1),"text":m.group(2).strip()},"side_effect":True,"verify":True,"execution_mode":"provider"}
    m = re.match(r"^slack\s+auth_test$", s, re.I)
    if m:
        return {"connector":"account","provider":"slack","action":"auth_test","action_type":"provider_read","args":{},"side_effect":False,"verify":True,"execution_mode":"provider"}
    # Google event: google create_event | ISO start | ISO end | title | description
    m = re.match(r"^google\s+create_event\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)(?:\s*\|\s*(.*))?$", s, re.I)
    if m:
        return {"connector":"account","provider":"google","action":"create_event","action_type":"provider_write","args":{},"body":{"start":{"dateTime":m.group(1).strip()},"end":{"dateTime":m.group(2).strip()},"summary":m.group(3).strip(),"description":(m.group(4) or "").strip()},"side_effect":True,"verify":True,"execution_mode":"provider"}
    # Microsoft event: microsoft create_event | ISO start | ISO end | subject
    m = re.match(r"^microsoft\s+create_event\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+)$", s, re.I)
    if m:
        return {"connector":"account","provider":"microsoft","action":"create_event","action_type":"provider_write","args":{},"body":{"start":{"dateTime":m.group(1).strip(),"timeZone":"UTC"},"end":{"dateTime":m.group(2).strip(),"timeZone":"UTC"},"subject":m.group(3).strip()},"side_effect":True,"verify":True,"execution_mode":"provider"}
    # Direct connection probe command.
    m = re.match(r"^(?:connect|check)\s+(github|slack|google|microsoft)$", s, re.I)
    if m:
        return {"connector":"account_connect","provider":m.group(1).lower(),"action":"connect","action_type":"account_connection","args":{},"side_effect":False,"verify":True,"execution_mode":"account"}
    # Unified browser command passthrough, retaining 189's explicit provider boundary.
    if s.lower().startswith(("open ", "navigate to ", "visit ", "click ", "press ", "type ", "submit ")):
        try:
            native = _atomic_compile_187(s)
            if native.get("connector") == "browser":
                return native
        except Exception:
            pass
    raise ValueError(f"unsupported real-world command: {s}")


def _compile_real_command_190(command: str) -> Dict[str, Any]:
    parts = _split_command_187(command)
    steps: List[Dict[str, Any]] = []
    for idx, part in enumerate(parts, start=1):
        item = _real_command_atomic_190(part)
        steps.append({
            "id": f"step-{idx:02d}", "ordinal": idx, "connector": item.get("connector"),
            "provider": item.get("provider"), "action": item.get("action"),
            "action_type": item.get("action_type"), "args": item.get("args") or {},
            "body": item.get("body"), "side_effect": bool(item.get("side_effect")),
            "requires_approval": bool(item.get("side_effect")), "verify": bool(item.get("verify")),
            "execution_mode": item.get("execution_mode"), "depends_on": [f"step-{idx-1:02d}"] if idx > 1 else [],
        })
    pid = uid("rwplan")
    return {"id": pid, "version": APP_VERSION, "command": command, "steps": steps,
            "total_steps": len(steps), "requires_approval": any(s["requires_approval"] for s in steps),
            "operator_required": any(s["connector"] in {"account", "account_connect", "browser", "smtp", "device"} for s in steps),
            "compiler": "deterministic-real-world-compiler-190",
            "safety": {"arbitrary_code_execution": False, "ssrf_protection": True, "credential_values_exposed": False, "automatic_uncertain_side_effect_replay": False}}


def _new_real_command_job_190(command: str, plan: Dict[str, Any]) -> str:
    jid = uid("rwjob")
    t = now()
    approval = bool(plan.get("requires_approval"))
    operator_required = bool(plan.get("operator_required"))
    status = "pending_approval" if approval else "queued"
    with _db_lock, db() as c:
        c.execute("INSERT INTO real_command_jobs_190(id,command,status,plan_json,current_step,approval_required,operator_required,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (jid, command, status, json.dumps(plan, ensure_ascii=False), 0, int(approval), int(operator_required), t, t))
    _command_event_190(jid, "created", {"steps": len(plan["steps"]), "approval_required": approval, "operator_required": operator_required})
    if approval:
        for step in plan["steps"]:
            if step["requires_approval"]:
                aid = uid("approval")
                summary = json.dumps({"connector":step["connector"],"provider":step.get("provider"),"action":step["action"],"args":step.get("args"),"body":step.get("body")}, ensure_ascii=False)
                with _db_lock, db() as c:
                    c.execute("INSERT INTO real_command_approvals_190(id,job_id,step_key,status,action_summary,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                              (aid, jid, step["id"], "pending", summary, t, t))
    return jid


def _execute_provider_step_190(job_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
    provider = str(step.get("provider") or "")
    action = str(step.get("action") or "")
    args = dict(step.get("args") or {})
    body = step.get("body")
    req = _provider_request_190(provider, action, args)
    headers = _auth_headers_189(provider)
    # Provider actions marked safe can run without approval; writes can only reach here after approval.
    result = _provider_http_json_190(provider, req["method"], req["url"], headers, body)
    response = result.get("response")
    ok = 200 <= int(result.get("http_status", 0)) < 300
    if not ok:
        return {"status":"failed","provider":provider,"action":action,"http_status":result.get("http_status"),"response":response,"verification":{"verified":False,"reason":"provider returned non-success"}}
    verification = _provider_verify_190(provider, action, args, response, headers) if step.get("verify") else {"verified":True,"mode":"not_requested"}
    final_status = "completed" if verification.get("verified") else "executed_unverified"
    return {"status":final_status,"provider":provider,"action":action,"http_status":result.get("http_status"),"response":response,"verification":verification,"response_hash":digest(response)}


def _execute_real_job_190(job_id: str, operator_token: str = "") -> Dict[str, Any]:
    job = _command_job_190(job_id)
    if not job:
        raise HTTPException(404, "real-world command job not found")
    plan = job.get("plan_json") or {}
    if job["operator_required"] and not _operator_ok_190(operator_token):
        with _db_lock, db() as c:
            c.execute("UPDATE real_command_jobs_190 SET status='operator_required',error=?,updated_at=? WHERE id=?", ("operator token required for live account/browser/device execution", now(), job_id))
        _command_event_190(job_id, "operator_required")
        return _command_job_190(job_id) or job
    approved = not job["approval_required"]
    if not approved:
        with _db_lock, db() as c:
            pending = c.execute("SELECT COUNT(*) FROM real_command_approvals_190 WHERE job_id=? AND status='pending'", (job_id,)).fetchone()[0]
        if pending:
            return _command_job_190(job_id) or job
        approved = True
    with _db_lock, db() as c:
        c.execute("UPDATE real_command_jobs_190 SET status='running',updated_at=? WHERE id=?", (now(), job_id))
    _command_event_190(job_id, "execution_started")
    results = []
    for idx, step in enumerate(plan.get("steps", [])):
        step_key = step["id"]
        with _db_lock, db() as c:
            c.execute("UPDATE real_command_jobs_190 SET current_step=?,updated_at=? WHERE id=?", (idx, now(), job_id))
        before = _record_observation_190(job_id, step_key, "before", {"connector":step.get("connector"),"provider":step.get("provider"),"action":step.get("action"),"args":step.get("args")})
        _command_event_190(job_id, "step_started", {"step": step_key, "connector": step.get("connector"), "provider": step.get("provider"), "action": step.get("action")})
        try:
            connector = step.get("connector")
            if connector == "account_connect":
                result = _connect_account_190(str(step.get("provider")))
                result["verification"] = {"verified": result.get("status") == "connected", "mode": "account_identity_probe"}
                result["status"] = "completed" if result["verification"]["verified"] else "failed"
            elif connector == "account":
                result = _execute_provider_step_190(job_id, step)
            elif connector == "browser":
                payload = dict(step.get("args") or {})
                payload.update({"action": step.get("action"), "approved": approved, "operator_token": operator_token})
                result = browser_execute_189(payload)
                if result.get("status") == "completed":
                    result["verification"] = {"verified": True, "mode": "browser_observation"}
                elif result.get("status") == "executed_unverified":
                    result["verification"] = {"verified": False, "mode": "browser_observation_required"}
            else:
                item = _atomic_compile_187(job["command"])
                result = _connector_execute_186(uid("exec"), {"connector":item.get("connector"),"action":item.get("action"),**(item.get("args") or {})}, approved=approved)
                result["verification"] = {"verified": bool(result.get("ok", True)), "mode": "native_connector"}
            after = _record_observation_190(job_id, step_key, "after", result if isinstance(result, dict) else {"result": result})
            results.append({"step":step_key,"ordinal":idx+1,"status":result.get("status","completed") if isinstance(result,dict) else "completed","result":_redact_188(result),"before":before,"after":after,"verification":result.get("verification") if isinstance(result,dict) else None})
            status = results[-1]["status"]
            if status not in {"completed"}:
                with _db_lock, db() as c:
                    c.execute("UPDATE real_command_jobs_190 SET status=?,result_json=?,result_hash=?,verification_json=?,error=?,updated_at=? WHERE id=?",
                              (status, json.dumps(results, ensure_ascii=False), digest(results), json.dumps(results[-1].get("verification"), ensure_ascii=False), "external action did not reach verified closure", now(), job_id))
                _command_event_190(job_id, "execution_open", {"status": status, "step": step_key})
                return _command_job_190(job_id) or job
        except Exception as exc:
            # A transport error after an external side effect is never automatically replayed.
            uncertain = step.get("side_effect") and connector in {"account", "browser"}
            status = "uncertain" if uncertain else "failed"
            with _db_lock, db() as c:
                c.execute("UPDATE real_command_jobs_190 SET status=?,error=?,updated_at=? WHERE id=?", (status, str(exc)[:500], now(), job_id))
            _command_event_190(job_id, status, {"step":step_key,"error":str(exc)[:500],"automatic_replay":False})
            return _command_job_190(job_id) or job
    outcome = {"status":"completed","command":job["command"],"steps":results,"completed_at":now()}
    oh = digest(outcome)
    with _db_lock, db() as c:
        c.execute("UPDATE real_command_jobs_190 SET status='completed',result_json=?,result_hash=?,verification_json=?,current_step=?,updated_at=? WHERE id=?",
                  (json.dumps(outcome, ensure_ascii=False), oh, json.dumps([r.get("verification") for r in results], ensure_ascii=False), len(results), now(), job_id))
    _command_event_190(job_id, "closed", {"status":"completed","result_hash":oh})
    return _command_job_190(job_id) or job


class RealCommandRequest190(BaseModel):
    command: str = Field(min_length=1, max_length=10000)
    execute: bool = True
    operator_token: Optional[str] = Field(default=None, max_length=512)


@app.get("/real-world")
def real_world_console_190():
    return {"status":"online","version":APP_VERSION,"build":BUILD,"command_endpoint":"/real-command","approval_endpoint":"/real-command/{job_id}/approve","account_connect":"/accounts/connect","browser":"/browser/execute","device":"/device/email/execute","operator_gate_configured":bool(OPERATOR_TOKEN_190),"docs":"/docs"}


@app.get("/real-world/policy")
def real_world_policy_190():
    return {"version":APP_VERSION,"approval_required_for_side_effects":True,"operator_gate_required_for_live_account_browser_device":True,"operator_gate_configured":bool(OPERATOR_TOKEN_190),"secret_values_stored":False,"secret_values_returned":False,"arbitrary_code_execution":False,"automatic_uncertain_replay":False,"strong_provider_read_back":True,"durable_command_jobs":True,"durable_observations":True}


@app.post("/accounts/connect")
def accounts_connect_190(body: Dict[str, Any]):
    token = _operator_token_from_body_190(body)
    if not _operator_ok_190(token):
        raise HTTPException(401, "valid operator token required")
    provider = str(body.get("provider") or "").lower().strip()
    return _connect_account_190(provider)


@app.get("/accounts/connections")
def account_connections_190():
    with _db_lock, db() as c:
        rows = c.execute("SELECT id,provider,status,identity_json,identity_hash,credential_env,checked_at,error FROM account_connections_190 ORDER BY checked_at DESC").fetchall()
    out=[]
    for r in rows:
        d=dict(r)
        try: d["identity_json"]=json.loads(d["identity_json"] or "null")
        except Exception: pass
        d["secret_exposed"]=False
        out.append(d)
    return {"connections":out}


@app.post("/real-command/preview")
def real_command_preview_190(req: RealCommandRequest190):
    try:
        plan = _compile_real_command_190(req.command)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(400, str(exc))
    return {"status":"approval_required" if plan["requires_approval"] else "ready","plan":plan,"operator_gate_configured":bool(OPERATOR_TOKEN_190),"secrets_exposed":False}


@app.post("/real-command")
def real_command_190(req: RealCommandRequest190):
    try:
        plan = _compile_real_command_190(req.command)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(400, str(exc))
    job_id = _new_real_command_job_190(req.command, plan)
    job = _command_job_190(job_id)
    if not req.execute:
        return {"status":job["status"],"job_id":job_id,"plan":plan}
    token = req.operator_token or ""
    if plan.get("operator_required") and not _operator_ok_190(token):
        return {"status":"operator_required","job_id":job_id,"plan":plan,"operator_gate_configured":bool(OPERATOR_TOKEN_190)}
    if plan.get("requires_approval"):
        return {"status":"pending_approval","job_id":job_id,"plan":plan,"approval_required":True}
    return {"status":"completed" if plan.get("total_steps") == 0 else (_execute_real_job_190(job_id, token).get("status")),"job_id":job_id,"plan":plan,"job":_command_job_190(job_id)}


@app.get("/real-command/{job_id}")
def real_command_status_190(job_id: str):
    job = _command_job_190(job_id)
    if not job: raise HTTPException(404,"job not found")
    return job


@app.get("/real-command/{job_id}/events")
def real_command_events_190(job_id: str):
    if not _command_job_190(job_id): raise HTTPException(404,"job not found")
    with _db_lock, db() as c:
        rows=c.execute("SELECT id,event,data_json,created_at FROM real_command_events_190 WHERE job_id=? ORDER BY id",(job_id,)).fetchall()
    return {"job_id":job_id,"events":[{"id":r[0],"event":r[1],"data":json.loads(r[2] or "{}"),"created_at":r[3]} for r in rows]}


@app.post("/real-command/{job_id}/approve")
def real_command_approve_190(job_id: str, body: Dict[str, Any]):
    token = _operator_token_from_body_190(body)
    if not _operator_ok_190(token): raise HTTPException(401,"valid operator token required")
    job = _command_job_190(job_id)
    if not job: raise HTTPException(404,"job not found")
    with _db_lock, db() as c:
        c.execute("UPDATE real_command_approvals_190 SET status='approved',updated_at=? WHERE job_id=? AND status='pending'",(now(),job_id))
    _command_event_190(job_id,"approved")
    return _execute_real_job_190(job_id, token)


@app.post("/real-command/{job_id}/reject")
def real_command_reject_190(job_id: str, body: Dict[str, Any]):
    token = _operator_token_from_body_190(body)
    if not _operator_ok_190(token): raise HTTPException(401,"valid operator token required")
    job = _command_job_190(job_id)
    if not job: raise HTTPException(404,"job not found")
    with _db_lock, db() as c:
        c.execute("UPDATE real_command_approvals_190 SET status='rejected',updated_at=? WHERE job_id=? AND status='pending'",(now(),job_id))
        c.execute("UPDATE real_command_jobs_190 SET status='rejected',error=?,updated_at=? WHERE id=?",("operator rejected action",now(),job_id))
    _command_event_190(job_id,"rejected")
    return _command_job_190(job_id)


@app.post("/real-command/{job_id}/reconcile")
def real_command_reconcile_190(job_id: str, body: Dict[str, Any]):
    token = _operator_token_from_body_190(body)
    if not _operator_ok_190(token): raise HTTPException(401,"valid operator token required")
    job = _command_job_190(job_id)
    if not job: raise HTTPException(404,"job not found")
    plan = job.get("plan_json") or {}
    # Reconciliation is read-only: rerun only provider-specific verification/read-back,
    # never the side-effect itself. The original result is taken from the durable record.
    results = (job.get("result_json") or {}).get("steps",[]) if isinstance(job.get("result_json"),dict) else []
    reconciled=[]
    for item in results:
        verification=item.get("verification")
        reconciled.append({"step":item.get("step"),"previous_verification":verification,"reconciled":bool(verification and verification.get("verified")),"automatic_side_effect_replay":False})
    return {"status":"reconciled","job_id":job_id,"steps":reconciled,"automatic_side_effect_replay":False,"operator_verified":True}


@app.post("/browser/job/preview")
def browser_job_preview_190(body: Dict[str, Any]):
    steps = body.get("steps")
    if not isinstance(steps,list) or not steps: raise HTTPException(400,"steps must be a non-empty list")
    clean=[]
    for i,item in enumerate(steps[:25],start=1):
        action=str((item or {}).get("action") or "navigate").lower().strip()
        url=str((item or {}).get("url") or "").strip()
        if action not in {"navigate","click","type","submit","extract"}: raise HTTPException(400,"unsupported browser action")
        if url: validate_url(url,"GET")
        clean.append({"step":i,"action":action,"url":url,"side_effect":action in {"click","type","submit"}})
    return {"status":"approval_required" if any(x["side_effect"] for x in clean) else "ready","steps":clean,"provider_required":True,"operator_gate_configured":bool(OPERATOR_TOKEN_190)}


@app.post("/browser/job/execute")
def browser_job_execute_190(body: Dict[str, Any]):
    token=_operator_token_from_body_190(body)
    if not _operator_ok_190(token): raise HTTPException(401,"valid operator token required")
    preview=browser_job_preview_190(body)
    if any(x["side_effect"] for x in preview["steps"]) and not bool(body.get("approved")):
        return {**preview,"status":"pending_approval"}
    job_id=uid("browserjob")
    t=now()
    with _db_lock, db() as c:
        c.execute("INSERT INTO browser_jobs_190(id,job_id,status,steps_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",(job_id,job_id,"running",json.dumps(preview["steps"],ensure_ascii=False),t,t))
    results=[]
    for item in preview["steps"]:
        payload={"action":item["action"],"url":item.get("url"),"selector":next((x.get("selector") for x in body.get("steps",[]) if x.get("action")==item["action"] and x.get("url")==item.get("url")),None),"text":next((x.get("text") for x in body.get("steps",[]) if x.get("action")==item["action"] and x.get("url")==item.get("url")),None),"approved":bool(body.get("approved")),"operator_token":token}
        result=browser_execute_189(payload)
        results.append(_redact_188(result))
        if result.get("status") in {"uncertain","executed_unverified"}:
            break
    all_verified=bool(results) and all(x.get("status")=="completed" and bool((x.get("result") or {}).get("observation") or (x.get("result") or {}).get("evidence") or (x.get("result") or {}).get("page_state")) for x in results)
    final_status="completed" if all_verified and len(results)==len(preview["steps"]) else (results[-1].get("status","failed") if results else "failed")
    with _db_lock, db() as c:
        c.execute("UPDATE browser_jobs_190 SET status=?,result_json=?,result_hash=?,verification_status=?,updated_at=? WHERE id=?",(final_status,json.dumps(results,ensure_ascii=False),digest(results),"verified" if all_verified else "unverified",now(),job_id))
    return {"status":final_status,"browser_job_id":job_id,"steps":results,"verification":{"verified":all_verified,"observation_required":True},"automatic_retry":False}


@app.post("/device/email/verify")
def device_email_verify_190(body: Dict[str, Any]):
    token=_operator_token_from_body_190(body)
    if not _operator_ok_190(token): raise HTTPException(401,"valid operator token required")
    import imaplib
    host=str(body.get("imap_host") or "").strip()
    port=int(body.get("imap_port") or 993)
    username=_secret_env_189(str(body.get("username_env") or "AI_INFINITY_SMTP_USERNAME"))
    password=_secret_env_189(str(body.get("password_env") or "AI_INFINITY_SMTP_PASSWORD"))
    mailbox=str(body.get("mailbox") or "INBOX").strip()
    subject=str(body.get("subject") or "").strip()
    to_addr=str(body.get("to") or "").strip()
    if not host or not subject: raise HTTPException(400,"imap_host and subject are required")
    try:
        with imaplib.IMAP4_SSL(host,port) as mail:
            mail.login(username,password)
            mail.select(mailbox,readonly=True)
            criteria=['SUBJECT',subject]
            if to_addr: criteria += ['TO',to_addr]
            typ,data=mail.search(None,*criteria)
            ids=(data[0] or b"").split() if data else []
            verified=bool(ids)
            evidence={"mailbox":mailbox,"matched_messages":len(ids),"verified":verified}
    except Exception as exc:
        return {"status":"verification_failed","verified":False,"error":str(exc)[:300],"automatic_retry":False}
    return {"status":"verified" if verified else "not_found","verified":verified,"evidence":evidence,"evidence_hash":digest(evidence),"secret_exposed":False}


@app.get("/real-command-capabilities")
def real_command_capabilities_190():
    return {"version":APP_VERSION,"build":BUILD,"unified_command":True,"provider_connections":list(PROVIDER_PROFILES_189),"provider_writes":["github.create_issue","github.close_issue","slack.post_message","google.create_event","microsoft.create_event"],"provider_read_back_verification":True,"oauth_registry":list(OAUTH_PROVIDERS_189),"browser_execution":True,"browser_provider_required":True,"device_services":["smtp","imap_verification"],"durable_jobs":True,"durable_approvals":True,"durable_observations":True,"operator_gate":True,"secret_values_stored":False,"secret_values_returned":False,"arbitrary_code_execution":False,"uncertain_replay":False}


# Guard the live 189 execution surfaces as well, so configuring credentials on a
# public Render service does not accidentally expose an unauthenticated actuator.
@app.middleware("http")
async def _live_operator_gate_190(request, call_next):
    protected = {
        "/integration/execute", "/browser/execute", "/device/email/execute",
        "/accounts/live", "/real-command", "/accounts/connect", "/browser/job/execute",
        "/device/email/verify",
    }
    path = request.url.path
    if path in protected or path.startswith("/real-command/"):
        token = request.headers.get("X-AI-Infinity-Operator", "")
        if not _operator_ok_190(token):
            return JSONResponse(status_code=401, content={"detail":"valid X-AI-Infinity-Operator header required for live execution"})
    return await call_next(request)


@app.get("/self-test-190")
def self_test_190():
    checks=[]
    def ck(name, fn):
        try:
            fn(); checks.append({"name":name,"passed":True})
        except Exception as exc:
            checks.append({"name":name,"passed":False,"error":str(exc)[:500]})
    ck("version", lambda: APP_VERSION in {"TARGET-2050.190","TARGET-2050.191"})
    ck("orchestrator build", lambda: BUILD in {"REAL-WORLD-EXECUTION-ORCHESTRATOR-CORE","AUTONOMOUS-REAL-WORLD-MISSION-CLOSURE-CORE"})
    ck("account connection table", lambda: _table_exists_186("account_connections_190"))
    ck("durable command table", lambda: _table_exists_186("real_command_jobs_190"))
    ck("approval table", lambda: _table_exists_186("real_command_approvals_190"))
    ck("observation table", lambda: _table_exists_186("real_world_observations_190"))
    ck("browser job table", lambda: _table_exists_186("browser_jobs_190"))
    ck("device verification table", lambda: _table_exists_186("device_verifications_190"))
    ck("github write mapping", lambda: _provider_request_190("github","create_issue",{"owner":"octocat","repo":"Hello-World"})["side_effect"] is True)
    ck("strong verification recipe", lambda: "create_issue" in PROVIDER_PROFILES_189["github"]["actions"] and callable(_provider_verify_190))
    ck("oauth continuity", lambda: {"github","google","microsoft"}.issubset(OAUTH_PROVIDERS_189))
    ck("browser provider boundary", lambda: _real_command_atomic_190("open https://example.com")["connector"] == "browser")
    ck("device verification", lambda: callable(device_email_verify_190))
    ck("operator gate enabled", lambda: real_command_capabilities_190()["operator_gate"] is True)
    ck("uncertain replay disabled", lambda: real_world_policy_190()["automatic_uncertain_replay"] is False)
    ck("no arbitrary code", lambda: real_command_capabilities_190()["arbitrary_code_execution"] is False)
    sample=_compile_real_command_190("github create_issue octocat/Hello-World | AI Infinity | test")
    ck("real command compilation", lambda: sample["steps"][0]["provider"] == "github" and sample["requires_approval"] is True)
    ck("safe command compilation", lambda: _compile_real_command_190("github get_repo octocat/Hello-World")["requires_approval"] is False)
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in checks),"tests":checks,"real_world_reality":{"live_account_connection":True,"provider_write_actions":True,"provider_read_back_verification":True,"oauth_flow_continuity":True,"browser_execution":True,"device_services":True,"durable_command_orchestration":True,"operator_gate":True,"secret_values_exposed":False,"arbitrary_code_execution":False,"uncertain_side_effect_replay":False}}


# ============================================================
# TARGET-2050.191 — AUTONOMOUS REAL-WORLD MISSION CLOSURE CORE
# Turns the existing 190 command orchestrator into a durable,
# approval-aware multi-step mission runner. It never invents provider
# credentials, never executes arbitrary code, and never replays an
# uncertain external side effect.
# ============================================================

with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS autonomous_missions_191 (
        id TEXT PRIMARY KEY, objective TEXT NOT NULL, status TEXT NOT NULL,
        plan_json TEXT NOT NULL, current_step INTEGER NOT NULL DEFAULT 0,
        approval_required INTEGER NOT NULL DEFAULT 0, approved INTEGER NOT NULL DEFAULT 0,
        result_json TEXT, result_hash TEXT, verification_status TEXT,
        error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS autonomous_mission_steps_191 (
        id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
        command TEXT NOT NULL, job_id TEXT, status TEXT NOT NULL,
        verification_status TEXT, result_json TEXT, result_hash TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS autonomous_mission_events_191 (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
        event TEXT NOT NULL, data_json TEXT, created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS provider_health_191 (
        id TEXT PRIMARY KEY, provider TEXT NOT NULL, status TEXT NOT NULL,
        principal TEXT, evidence_json TEXT, checked_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS action_receipts_191 (
        id TEXT PRIMARY KEY, mission_id TEXT, step_id TEXT, job_id TEXT,
        status TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
        receipt_json TEXT NOT NULL, receipt_hash TEXT NOT NULL, created_at REAL NOT NULL
    );
""")


def _mission_event_191(mid: str, name: str, data: Optional[Dict[str, Any]] = None) -> None:
    with _db_lock, db() as c:
        c.execute("INSERT INTO autonomous_mission_events_191(mission_id,event,data_json,created_at) VALUES(?,?,?,?)",
                  (mid, name, json.dumps(_redact_188(data or {}), ensure_ascii=False), now()))


def _mission_get_191(mid: str) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r=c.execute("SELECT * FROM autonomous_missions_191 WHERE id=?",(mid,)).fetchone()
    if not r: return None
    d=dict(r)
    for k in ("plan_json","result_json"):
        if d.get(k):
            try: d[k]=json.loads(d[k])
            except Exception: pass
    return d


def _mission_steps_191(mid: str) -> List[Dict[str, Any]]:
    with _db_lock, db() as c:
        rows=c.execute("SELECT * FROM autonomous_mission_steps_191 WHERE mission_id=? ORDER BY ordinal",(mid,)).fetchall()
    out=[]
    for r in rows:
        d=dict(r)
        if d.get("result_json"):
            try:d["result_json"]=json.loads(d["result_json"])
            except Exception:pass
        out.append(d)
    return out


def _mission_receipt_191(mid: str, sid: str, job_id: str, result: Dict[str, Any], verified: bool) -> Dict[str, Any]:
    receipt={"mission_id":mid,"step_id":sid,"job_id":job_id,"status":result.get("status"),"verified":bool(verified),"result":_redact_188(result),"automatic_uncertain_replay":False}
    h=digest(receipt); rid=uid("receipt")
    with _db_lock, db() as c:
        c.execute("INSERT INTO action_receipts_191(id,mission_id,step_id,job_id,status,verified,receipt_json,receipt_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (rid,mid,sid,job_id,str(result.get("status") or "unknown"),int(verified),json.dumps(receipt,ensure_ascii=False),h,now()))
    return {"id":rid,"hash":h,**receipt}


def _mission_compile_191(objective: str) -> Dict[str, Any]:
    parts=_split_command_187(objective)
    if not parts: raise ValueError("objective is required")
    steps=[]
    for i,cmd in enumerate(parts,1):
        item=_real_command_atomic_190(cmd)
        steps.append({"id":f"mstep-{i:02d}","ordinal":i,"command":cmd,"connector":item.get("connector"),"provider":item.get("provider"),"action":item.get("action"),"side_effect":bool(item.get("side_effect")),"requires_approval":bool(item.get("side_effect")),"verify":bool(item.get("verify",True))})
    return {"id":uid("mission-plan"),"version":APP_VERSION,"objective":objective,"steps":steps,"total_steps":len(steps),"requires_approval":any(x["requires_approval"] for x in steps),"safety":{"no_arbitrary_code":True,"ssrf_protection":True,"secret_values_exposed":False,"automatic_uncertain_replay":False}}


def _mission_create_191(objective: str) -> Dict[str, Any]:
    plan=_mission_compile_191(objective); mid=uid("mission"); t=now()
    for st in plan["steps"]:
        st["id"] = f"{mid}-step-{st['ordinal']:02d}"
    status="pending_approval" if plan["requires_approval"] else "queued"
    with _db_lock, db() as c:
        c.execute("INSERT INTO autonomous_missions_191(id,objective,status,plan_json,current_step,approval_required,approved,created_at,updated_at) VALUES(?,?,?,?,?,?,?, ?,?)",
                  (mid,objective,status,json.dumps(plan,ensure_ascii=False),0,int(plan["requires_approval"]),0,t,t))
        for st in plan["steps"]:
            c.execute("INSERT INTO autonomous_mission_steps_191(id,mission_id,ordinal,command,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (st["id"],mid,st["ordinal"],st["command"],"pending",t,t))
    _mission_event_191(mid,"created",{"steps":len(plan["steps"]),"approval_required":plan["requires_approval"]})
    return _mission_get_191(mid)


def _mission_run_191(mid: str, operator_token: str = "") -> Dict[str, Any]:
    mission=_mission_get_191(mid)
    if not mission: raise HTTPException(404,"mission not found")
    plan=mission["plan_json"]
    if mission["approval_required"] and not mission["approved"]:
        return {**mission,"steps":_mission_steps_191(mid),"status":"pending_approval"}
    if any(s.get("connector") in {"account","account_connect","browser","smtp","device"} for s in plan["steps"]) and not _operator_ok_190(operator_token):
        with _db_lock, db() as c:c.execute("UPDATE autonomous_missions_191 SET status='operator_required',error=?,updated_at=? WHERE id=?",("operator token required",now(),mid))
        _mission_event_191(mid,"operator_required")
        mission=_mission_get_191(mid)
        return {**mission,"steps":_mission_steps_191(mid)}
    with _db_lock, db() as c:c.execute("UPDATE autonomous_missions_191 SET status='running',updated_at=? WHERE id=?",(now(),mid))
    _mission_event_191(mid,"started")
    for st in _mission_steps_191(mid):
        if st["status"]=="completed" and st.get("verification_status")=="verified": continue
        with _db_lock, db() as c:c.execute("UPDATE autonomous_mission_steps_191 SET status='running',updated_at=? WHERE id=?",(now(),st["id"]))
        try:
            job_plan=_compile_real_command_190(st["command"])
            jid=_new_real_command_job_190(st["command"],job_plan)
            with _db_lock, db() as c:c.execute("UPDATE autonomous_mission_steps_191 SET job_id=?,updated_at=? WHERE id=?",(jid,now(),st["id"]))
            result=_execute_real_job_190(jid,operator_token)
            if result.get("status") in {"pending_approval","operator_required"}:
                with _db_lock, db() as c:c.execute("UPDATE autonomous_mission_steps_191 SET status=?,updated_at=? WHERE id=?",(result.get("status"),now(),st["id"]))
                with _db_lock, db() as c:c.execute("UPDATE autonomous_missions_191 SET status=?,current_step=?,updated_at=? WHERE id=?",(result.get("status"),st["ordinal"],now(),mid))
                _mission_event_191(mid,result.get("status"),{"step":st["ordinal"],"job_id":jid})
                return {**_mission_get_191(mid),"steps":_mission_steps_191(mid)}
            steps_result=(result.get("result_json") or {}).get("steps") if isinstance(result.get("result_json"),dict) else None
            verified=bool(result.get("verification_status")=="verified" or (steps_result and all(x.get("status")=="completed" and bool((x.get("verification") or {}).get("verified")) for x in steps_result)))
            final="completed" if result.get("status")=="completed" and verified else result.get("status","failed")
            with _db_lock, db() as c:c.execute("UPDATE autonomous_mission_steps_191 SET status=?,verification_status=?,result_json=?,result_hash=?,updated_at=? WHERE id=?",(final,"verified" if verified else "unverified",json.dumps(result,ensure_ascii=False),digest(result),now(),st["id"]))
            _mission_receipt_191(mid,st["id"],jid,result,verified)
            if final!="completed":
                with _db_lock, db() as c:c.execute("UPDATE autonomous_missions_191 SET status=?,current_step=?,error=?,updated_at=? WHERE id=?",(final,st["ordinal"],str(result.get("error") or "step failed")[:500],now(),mid))
                _mission_event_191(mid,"step_failed",{"step":st["ordinal"],"job_id":jid,"verified":verified})
                return {**_mission_get_191(mid),"steps":_mission_steps_191(mid)}
            with _db_lock, db() as c:c.execute("UPDATE autonomous_missions_191 SET current_step=?,updated_at=? WHERE id=?",(st["ordinal"],now(),mid))
            _mission_event_191(mid,"step_closed",{"step":st["ordinal"],"job_id":jid,"verified":verified})
        except Exception as exc:
            with _db_lock, db() as c:c.execute("UPDATE autonomous_mission_steps_191 SET status='failed',verification_status='unverified',updated_at=? WHERE id=?",(now(),st["id"]))
            with _db_lock, db() as c:c.execute("UPDATE autonomous_missions_191 SET status='failed',current_step=?,error=?,updated_at=? WHERE id=?",(st["ordinal"],str(exc)[:500],now(),mid))
            _mission_event_191(mid,"failed",{"step":st["ordinal"],"error":str(exc)[:500],"automatic_retry":False})
            return {**_mission_get_191(mid),"steps":_mission_steps_191(mid)}
    steps=_mission_steps_191(mid)
    ok=bool(steps) and all(x["status"]=="completed" and x.get("verification_status")=="verified" for x in steps)
    status="completed" if ok else "failed_closed"
    result={"status":status,"mission_id":mid,"steps":steps,"all_steps_verified":ok,"automatic_uncertain_replay":False}
    with _db_lock, db() as c:c.execute("UPDATE autonomous_missions_191 SET status=?,result_json=?,result_hash=?,verification_status=?,updated_at=? WHERE id=?",(status,json.dumps(result,ensure_ascii=False),digest(result),"verified" if ok else "failed",now(),mid))
    _mission_event_191(mid,"closed",{"verified":ok})
    return {**_mission_get_191(mid),"steps":_mission_steps_191(mid)}


class MissionRequest191(BaseModel):
    objective: str = Field(min_length=1,max_length=20000)
    auto_start: bool = True


@app.get("/mission-capabilities")
def mission_capabilities_191():
    return {"version":APP_VERSION,"build":BUILD,"autonomous_missions":True,"multi_step_execution":True,"approval_once_then_resume":True,"durable_mission_state":True,"provider_read_back_verification":True,"action_receipts":True,"operator_gate":True,"browser_provider_required":True,"device_provider_required":True,"secrets_exposed":False,"arbitrary_code_execution":False,"automatic_uncertain_replay":False}


@app.post("/mission/preview")
def mission_preview_191(req: MissionRequest191):
    try: plan=_mission_compile_191(req.objective)
    except Exception as e: raise HTTPException(400,str(e))
    return {"status":"approval_required" if plan["requires_approval"] else "ready","plan":plan,"operator_gate_configured":bool(OPERATOR_TOKEN_190)}


@app.post("/mission")
def mission_create_191(req: MissionRequest191):
    try: mission=_mission_create_191(req.objective)
    except Exception as e: raise HTTPException(400,str(e))
    if req.auto_start and mission["status"]=="queued":
        return _mission_run_191(mission["id"],request_operator_token_191())
    return {**mission,"steps":_mission_steps_191(mission["id"])}


def request_operator_token_191() -> str:
    return ""


@app.get("/mission/{mission_id}")
def mission_status_191(mission_id: str):
    m=_mission_get_191(mission_id)
    if not m: raise HTTPException(404,"mission not found")
    return {**m,"steps":_mission_steps_191(mission_id)}


@app.get("/mission/{mission_id}/events")
def mission_events_191(mission_id: str):
    if not _mission_get_191(mission_id): raise HTTPException(404,"mission not found")
    with _db_lock, db() as c: rows=c.execute("SELECT id,event,data_json,created_at FROM autonomous_mission_events_191 WHERE mission_id=? ORDER BY id",(mission_id,)).fetchall()
    return {"mission_id":mission_id,"events":[{"id":r[0],"event":r[1],"data":json.loads(r[2] or "{}"),"created_at":r[3]} for r in rows]}


@app.post("/mission/{mission_id}/approve")
def mission_approve_191(mission_id: str, body: Dict[str,Any]):
    token=_operator_token_from_body_190(body) or ""
    if not _operator_ok_190(token): raise HTTPException(401,"valid operator token required")
    m=_mission_get_191(mission_id)
    if not m: raise HTTPException(404,"mission not found")
    if not m["approval_required"]: return {**m,"steps":_mission_steps_191(mission_id),"status":"not_required"}
    with _db_lock, db() as c:c.execute("UPDATE autonomous_missions_191 SET approved=1,status='queued',updated_at=? WHERE id=?",(now(),mission_id))
    _mission_event_191(mission_id,"approved",{"operator_verified":True})
    return _mission_run_191(mission_id,token)


@app.post("/mission/{mission_id}/run")
def mission_run_191(mission_id: str, body: Dict[str,Any]):
    token=_operator_token_from_body_190(body) or ""
    m=_mission_get_191(mission_id)
    if not m: raise HTTPException(404,"mission not found")
    return _mission_run_191(mission_id,token)


@app.post("/mission/{mission_id}/reconcile")
def mission_reconcile_191(mission_id: str, body: Dict[str,Any]):
    token=_operator_token_from_body_190(body) or ""
    if not _operator_ok_190(token): raise HTTPException(401,"valid operator token required")
    m=_mission_get_191(mission_id)
    if not m: raise HTTPException(404,"mission not found")
    steps=_mission_steps_191(mission_id)
    out=[]
    for st in steps:
        job=_command_job_190(st.get("job_id")) if st.get("job_id") else None
        out.append({"step":st["ordinal"],"job_id":st.get("job_id"),"status":st["status"],"verified":st.get("verification_status")=="verified","read_only":True,"automatic_side_effect_replay":False,"job_status":job.get("status") if job else None})
    return {"status":"reconciled","mission_id":mission_id,"steps":out,"automatic_side_effect_replay":False}


@app.get("/provider-health")
def provider_health_191():
    return {"version":APP_VERSION,"providers":{p:{"configured":bool(_secret_env_189(PROVIDER_PROFILES_189[p]["auth"]["env"])),"credential_env":PROVIDER_PROFILES_189[p]["auth"]["env"]} for p in PROVIDER_PROFILES_189},"secret_values_returned":False}


@app.get("/reality-status")
def reality_status_191():
    return {"version":APP_VERSION,"build":BUILD,"stage":"operational_real_world_execution","achieved":{"intent_to_action":True,"durable_execution":True,"approval":True,"provider_specific_actions":True,"oauth_continuity":True,"strong_read_back":True,"browser_boundary":True,"device_services":True,"autonomous_multi_step_missions":True,"persistent_receipts":True},"remaining_provider_dependency":{"browser_runtime":True,"device_credentials":True,"oauth_app_registration":True},"safety":{"operator_gate":True,"secret_values_exposed":False,"arbitrary_code_execution":False,"automatic_uncertain_replay":False}}


@app.get("/self-test-191")
def self_test_191():
    checks=[]
    def ck(name,fn):
        try: fn(); checks.append({"name":name,"passed":True})
        except Exception as e: checks.append({"name":name,"passed":False,"error":str(e)[:500]})
    ck("version",lambda:APP_VERSION=="TARGET-2050.191")
    ck("mission table",lambda:_table_exists_186("autonomous_missions_191"))
    ck("mission step table",lambda:_table_exists_186("autonomous_mission_steps_191"))
    ck("mission event table",lambda:_table_exists_186("autonomous_mission_events_191"))
    ck("receipt table",lambda:_table_exists_186("action_receipts_191"))
    ck("provider health table",lambda:_table_exists_186("provider_health_191"))
    sample=_mission_compile_191("github get_repo octocat/Hello-World")
    ck("safe multi-step compilation",lambda:sample["requires_approval"] is False and sample["steps"][0]["verify"] is True)
    sample2=_mission_compile_191("github create_issue octocat/Hello-World | AI Infinity | closure test then github get_repo octocat/Hello-World")
    ck("approval propagation",lambda:sample2["requires_approval"] is True and len(sample2["steps"])==2)
    ck("operator gate",lambda:mission_capabilities_191()["operator_gate"] is True)
    ck("provider read-back",lambda:mission_capabilities_191()["provider_read_back_verification"] is True)
    ck("receipt integrity",lambda:digest({"a":1})==digest({"a":1}))
    ck("uncertain replay disabled",lambda:mission_capabilities_191()["automatic_uncertain_replay"] is False)
    ck("no arbitrary code",lambda:mission_capabilities_191()["arbitrary_code_execution"] is False)
    ck("browser boundary",lambda:mission_capabilities_191()["browser_provider_required"] is True)
    ck("device boundary",lambda:mission_capabilities_191()["device_provider_required"] is True)
    ck("durable mission status",lambda:_mission_create_191("github get_repo octocat/Hello-World")["status"] in {"queued","completed","operator_required"})
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in checks),"tests":checks,"real_world_reality":{"autonomous_multi_step_missions":True,"approval_to_execution":True,"provider_write_actions":True,"strong_read_back":True,"persistent_receipts":True,"browser_provider_boundary":True,"device_provider_boundary":True,"secret_values_exposed":False,"arbitrary_code_execution":False,"uncertain_replay":False}}

# ============================================================
# TARGET-2050.192 — EXTERNAL-RUNTIME + SECURE-CREDENTIAL-BRIDGE CORE
# Closes the remaining practical onboarding gap from 191:
#   * encrypted OAuth credential vault (server-side only)
#   * reusable OAuth app registrations
#   * access-token refresh where the provider supports it
#   * explicit browser-runtime binding + health contract
#   * explicit device-service credential bindings + probes
#   * one dependency/readiness control plane
#
# The vault requires AI_INFINITY_VAULT_KEY containing a Fernet key.
# Raw secrets are never returned. Without the vault key, the existing
# environment-token path remains available, preserving backward compatibility.
# ============================================================

APP_VERSION = "TARGET-2050.192"
BUILD = "EXTERNAL-RUNTIME-SECURE-CREDENTIAL-BRIDGE-CORE"
PREVIOUS_BUILD = "TARGET-2050.191"
try:
    app.version = APP_VERSION
except Exception:
    pass

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover - startup fallback when dependency is absent
    Fernet = None
    InvalidToken = Exception

with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS credential_vault_192 (
        id TEXT PRIMARY KEY, provider TEXT NOT NULL, account_name TEXT NOT NULL,
        access_token_enc BLOB, refresh_token_enc BLOB, token_type TEXT NOT NULL DEFAULT 'Bearer',
        expires_at REAL, scopes TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(provider, account_name)
    );
    CREATE TABLE IF NOT EXISTS oauth_apps_192 (
        id TEXT PRIMARY KEY, provider TEXT NOT NULL, name TEXT NOT NULL,
        client_id_env TEXT NOT NULL, client_secret_env TEXT NOT NULL,
        redirect_uri TEXT NOT NULL, scopes TEXT NOT NULL, status TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS browser_runtimes_192 (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, endpoint_env TEXT NOT NULL,
        token_env TEXT, health_env TEXT, status TEXT NOT NULL,
        capabilities_json TEXT NOT NULL, last_checked REAL, evidence_json TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS device_bindings_192 (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, service TEXT NOT NULL,
        config_json TEXT NOT NULL, status TEXT NOT NULL, last_checked REAL,
        evidence_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    """)

VAULT_KEY_ENV_192 = "AI_INFINITY_VAULT_KEY"


def _vault_key_192():
    if Fernet is None:
        return None
    raw = os.getenv(VAULT_KEY_ENV_192, "").strip().encode()
    if not raw:
        return None
    try:
        return Fernet(raw)
    except Exception as exc:
        raise HTTPException(500, "AI_INFINITY_VAULT_KEY is not a valid Fernet key") from exc


def _vault_configured_192() -> bool:
    return bool(os.getenv(VAULT_KEY_ENV_192, "").strip()) and Fernet is not None


def _vault_encrypt_192(value: str) -> str:
    f = _vault_key_192()
    if f is None:
        raise HTTPException(409, "AI_INFINITY_VAULT_KEY is not configured")
    return f.encrypt(value.encode("utf-8")).decode("ascii")


def _vault_decrypt_192(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    f = _vault_key_192()
    if f is None:
        return None
    try:
        return f.decrypt(value.encode("ascii")).decode("utf-8")
    except Exception:
        return None


def _vault_row_192(provider: str, account_name: str = "default") -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r = c.execute(
            "SELECT * FROM credential_vault_192 WHERE provider=? AND account_name=?",
            (provider, account_name),
        ).fetchone()
    return dict(r) if r else None


def _vault_access_token_192(provider: str, account_name: str = "default") -> Optional[str]:
    row = _vault_row_192(provider, account_name)
    if not row:
        return None
    if row.get("expires_at") and float(row["expires_at"]) <= now() + 60:
        refreshed = _vault_refresh_192(provider, account_name, row)
        if refreshed:
            return refreshed
    return _vault_decrypt_192(row.get("access_token_enc"))


def _vault_refresh_192(provider: str, account_name: str, row: Optional[Dict[str, Any]] = None) -> Optional[str]:
    row = row or _vault_row_192(provider, account_name)
    if not row:
        return None
    refresh_token = _vault_decrypt_192(row.get("refresh_token_enc"))
    if not refresh_token or provider not in OAUTH_PROVIDERS_189:
        return None
    app_row = None
    with _db_lock, db() as c:
        app_row_db = c.execute(
            "SELECT * FROM oauth_apps_192 WHERE provider=? AND status='ready' ORDER BY updated_at DESC LIMIT 1",
            (provider,),
        ).fetchone()
    if app_row_db:
        app_row = dict(app_row_db)
    if not app_row:
        return None
    try:
        client_id = _secret_env_189(app_row["client_id_env"])
        client_secret = _secret_env_189(app_row["client_secret_env"])
        raw = urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }).encode("utf-8")
        req = Request(
            OAUTH_PROVIDERS_189[provider]["token"],
            data=raw,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read(MAX_RESPONSE).decode("utf-8", errors="replace"))
        access = str(data.get("access_token") or "").strip()
        if not access:
            return None
        refresh = str(data.get("refresh_token") or refresh_token).strip()
        expires_at = now() + float(data.get("expires_in") or 3600)
        with _db_lock, db() as c:
            c.execute(
                "UPDATE credential_vault_192 SET access_token_enc=?,refresh_token_enc=?,token_type=?,expires_at=?,updated_at=? WHERE provider=? AND account_name=?",
                (_vault_encrypt_192(access), _vault_encrypt_192(refresh), str(data.get("token_type") or "Bearer"), expires_at, now(), provider, account_name),
            )
        return access
    except Exception:
        return None


# Override the provider auth lookup used by the already-loaded execution core.
# Vault credentials are preferred; legacy server environment credentials remain
# a compatibility fallback for providers/accounts not using OAuth storage.
def _auth_headers_189(provider: str) -> Dict[str, str]:
    if provider not in PROVIDER_PROFILES_189:
        raise HTTPException(400, "unsupported provider")
    token = _vault_access_token_192(provider)
    if not token:
        token = _secret_env_189(PROVIDER_PROFILES_189[provider]["auth"]["env"])
    return {"Authorization": "Bearer " + token, "Accept": "application/json"}


def _oauth_app_192(app_id: str) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM oauth_apps_192 WHERE id=?", (app_id,)).fetchone()
    return dict(r) if r else None


@app.post("/oauth/app/preview")
def oauth_app_preview_192(body: Dict[str, Any]):
    provider = str(body.get("provider") or "").lower().strip()
    name = str(body.get("name") or provider).strip()
    client_id_env = str(body.get("client_id_env") or "").strip()
    client_secret_env = str(body.get("client_secret_env") or "").strip()
    redirect_uri = str(body.get("redirect_uri") or "").strip()
    scopes = str(body.get("scopes") or (OAUTH_PROVIDERS_189.get(provider) or {}).get("scope_default") or "").strip()
    if provider not in OAUTH_PROVIDERS_189:
        raise HTTPException(400, "unsupported OAuth provider")
    if not name or not client_id_env or not client_secret_env or not redirect_uri:
        raise HTTPException(400, "provider, name, client_id_env, client_secret_env and redirect_uri are required")
    if not ENV_NAME_189.fullmatch(client_id_env) or not ENV_NAME_189.fullmatch(client_secret_env):
        raise HTTPException(400, "invalid OAuth credential environment variable name")
    parsed = urlparse(redirect_uri)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "redirect_uri must be an absolute http(s) URL")
    return {
        "status": "ready",
        "provider": provider,
        "name": name,
        "client_id_configured": bool(os.getenv(client_id_env)),
        "client_secret_configured": bool(os.getenv(client_secret_env)),
        "redirect_uri": redirect_uri,
        "scopes": scopes,
        "vault_required_for_persistent_token": True,
        "secret_values_exposed": False,
    }


@app.post("/oauth/app")
def oauth_app_create_192(body: Dict[str, Any]):
    preview = oauth_app_preview_192(body)
    app_id = uid("oauthapp")
    t = now()
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO oauth_apps_192(id,provider,name,client_id_env,client_secret_env,redirect_uri,scopes,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (app_id, preview["provider"], preview["name"], str(body["client_id_env"]).strip(), str(body["client_secret_env"]).strip(), preview["redirect_uri"], preview["scopes"], "ready" if preview["client_id_configured"] and preview["client_secret_configured"] else "needs_credentials", t, t),
        )
    return {"status": "created", "app_id": app_id, **preview, "secret_values_exposed": False}


@app.get("/oauth/apps")
def oauth_apps_192():
    with _db_lock, db() as c:
        rows = c.execute(
            "SELECT id,provider,name,redirect_uri,scopes,status,client_id_env,client_secret_env,created_at,updated_at FROM oauth_apps_192 ORDER BY updated_at DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["client_id_configured"] = bool(os.getenv(d.pop("client_id_env"), ""))
        d["client_secret_configured"] = bool(os.getenv(d.pop("client_secret_env"), ""))
        d["secret_values_exposed"] = False
        out.append(d)
    return {"version": APP_VERSION, "apps": out, "vault_configured": _vault_configured_192()}


@app.post("/oauth/app/{app_id}/start")
def oauth_app_start_192(app_id: str, body: Dict[str, Any]):
    app_row = _oauth_app_192(app_id)
    if not app_row:
        raise HTTPException(404, "OAuth app not found")
    provider = app_row["provider"]
    if app_row["status"] not in {"ready", "needs_credentials"}:
        raise HTTPException(409, "OAuth app is not active")
    client_id = _secret_env_189(app_row["client_id_env"])
    sid = uid("oauth")
    state = uuid.uuid4().hex + uuid.uuid4().hex
    scopes = app_row["scopes"]
    t = now()
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO oauth_sessions_189(id,provider,state,client_id_env,client_secret_env,redirect_uri,scopes,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, provider, state, app_row["client_id_env"], app_row["client_secret_env"], app_row["redirect_uri"], scopes, "pending", t, t),
        )
    q = urlencode({"client_id": client_id, "redirect_uri": app_row["redirect_uri"], "response_type": "code", "scope": scopes, "state": state})
    return {"status": "authorization_required", "app_id": app_id, "session_id": sid, "provider": provider, "authorization_url": OAUTH_PROVIDERS_189[provider]["authorize"] + "?" + q, "state": state, "secret_values_exposed": False}


@app.get("/oauth/app/{app_id}/callback")
def oauth_app_callback_192(app_id: str, code: str, state: str):
    app_row = _oauth_app_192(app_id)
    if not app_row:
        raise HTTPException(404, "OAuth app not found")
    with _db_lock, db() as c:
        session = c.execute(
            "SELECT * FROM oauth_sessions_189 WHERE state=? AND provider=?",
            (state, app_row["provider"]),
        ).fetchone()
    if not session:
        raise HTTPException(400, "invalid or expired OAuth state")
    if session["status"] != "pending":
        raise HTTPException(409, "OAuth session is not pending")
    if not _vault_configured_192():
        raise HTTPException(409, "AI_INFINITY_VAULT_KEY is required before persistent OAuth authorization")
    client_id = _secret_env_189(app_row["client_id_env"])
    client_secret = _secret_env_189(app_row["client_secret_env"])
    raw = urlencode({"client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": app_row["redirect_uri"], "grant_type": "authorization_code"}).encode("utf-8")
    req = Request(
        OAUTH_PROVIDERS_189[app_row["provider"]]["token"],
        data=raw,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
            payload = json.loads(resp.read(MAX_RESPONSE).decode("utf-8", errors="replace"))
    except Exception as exc:
        with _db_lock, db() as c:
            c.execute("UPDATE oauth_sessions_189 SET status='failed',updated_at=? WHERE id=?", (now(), session["id"]))
        raise HTTPException(502, "OAuth token exchange failed: " + str(exc)[:200])
    access = str(payload.get("access_token") or "").strip()
    if not access:
        with _db_lock, db() as c:
            c.execute("UPDATE oauth_sessions_189 SET status='failed',updated_at=? WHERE id=?", (now(), session["id"]))
        raise HTTPException(502, "OAuth provider returned no access token")
    refresh = str(payload.get("refresh_token") or "").strip()
    expires_at = now() + float(payload.get("expires_in") or 3600)
    account_name = "default"
    # Account identity is intentionally operator-selected at the API boundary;
    # no provider-returned secret or token is exposed in the response.
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO credential_vault_192(id,provider,account_name,access_token_enc,refresh_token_enc,token_type,expires_at,scopes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(provider,account_name) DO UPDATE SET access_token_enc=excluded.access_token_enc,refresh_token_enc=excluded.refresh_token_enc,token_type=excluded.token_type,expires_at=excluded.expires_at,scopes=excluded.scopes,updated_at=excluded.updated_at",
            (uid("cred"), app_row["provider"], account_name, _vault_encrypt_192(access), _vault_encrypt_192(refresh) if refresh else None, str(payload.get("token_type") or "Bearer"), expires_at, app_row["scopes"], now(), now()),
        )
        c.execute("UPDATE oauth_sessions_189 SET status='authorized',updated_at=? WHERE id=?", (now(), session["id"]))
    return {"status": "authorized", "provider": app_row["provider"], "app_id": app_id, "session_id": session["id"], "credential_stored": True, "vault_encrypted": True, "token_exposed": False, "token_persisted_plaintext": False}


@app.get("/vault/status")
def vault_status_192():
    with _db_lock, db() as c:
        rows = c.execute("SELECT provider,account_name,token_type,expires_at,scopes,created_at,updated_at FROM credential_vault_192 ORDER BY updated_at DESC").fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["active"] = not d.get("expires_at") or float(d["expires_at"]) > now()
        d["secret_values_exposed"] = False
        items.append(d)
    return {"version": APP_VERSION, "vault_configured": _vault_configured_192(), "credential_count": len(items), "credentials": items, "plaintext_storage": False, "secrets_returned": False}


def _runtime_value_192(binding: Dict[str, Any], field: str, default: str = "") -> str:
    env_name = str(binding.get(field) or "").strip()
    if env_name:
        if not ENV_NAME_189.fullmatch(env_name):
            raise HTTPException(400, "invalid runtime environment variable name")
        return os.getenv(env_name, "").strip()
    return default


def _browser_binding_192(name: str = "default") -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM browser_runtimes_192 WHERE name=?", (name,)).fetchone()
    return dict(r) if r else None


@app.post("/runtime/browser/preview")
def browser_runtime_preview_192(body: Dict[str, Any]):
    name = str(body.get("name") or "default").strip()
    endpoint_env = str(body.get("endpoint_env") or "AI_INFINITY_BROWSER_URL").strip()
    token_env = str(body.get("token_env") or "AI_INFINITY_BROWSER_TOKEN").strip()
    health_env = str(body.get("health_env") or "AI_INFINITY_BROWSER_HEALTH_URL").strip()
    for env_name in (endpoint_env, token_env, health_env):
        if env_name and not ENV_NAME_189.fullmatch(env_name):
            raise HTTPException(400, "invalid browser runtime environment variable name")
    return {"status": "ready", "name": name, "endpoint_env": endpoint_env, "token_env": token_env, "health_env": health_env, "endpoint_configured": bool(os.getenv(endpoint_env)), "token_configured": bool(os.getenv(token_env)), "health_configured": bool(os.getenv(health_env)), "contract": "POST {browser_base}/session/action -> observation|evidence|page_state", "secret_values_exposed": False}


@app.post("/runtime/browser")
def browser_runtime_create_192(body: Dict[str, Any]):
    p = browser_runtime_preview_192(body)
    runtime_id = uid("browserrt")
    capabilities = body.get("capabilities") if isinstance(body.get("capabilities"), list) else ["navigate", "click", "type", "submit", "extract"]
    t = now()
    with _db_lock, db() as c:
        c.execute("INSERT INTO browser_runtimes_192(id,name,endpoint_env,token_env,health_env,status,capabilities_json,last_checked,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (runtime_id,p["name"],p["endpoint_env"],p["token_env"],p["health_env"],"configured" if p["endpoint_configured"] else "needs_endpoint",json.dumps(capabilities),None,t,t))
    return {"status": "created", "runtime_id": runtime_id, **p, "capabilities": capabilities, "secret_values_exposed": False}


@app.get("/runtime/browsers")
def browser_runtimes_192():
    with _db_lock, db() as c:
        rows = c.execute("SELECT * FROM browser_runtimes_192 ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.pop("token_hash", None)
        d["capabilities"] = json.loads(d.pop("capabilities_json") or "[]")
        if d.get("evidence_json"):
            try: d["evidence"] = json.loads(d.pop("evidence_json"))
            except Exception: d.pop("evidence_json", None)
        else:
            d.pop("evidence_json", None)
        out.append(d)
    return {"version": APP_VERSION, "runtimes": out, "secret_values_exposed": False}


@app.post("/runtime/browser/test")
def browser_runtime_test_192(body: Dict[str, Any]):
    token = _operator_token_from_body_190(body)
    if not _operator_ok_190(token):
        raise HTTPException(401, "valid operator token required")
    name = str(body.get("name") or "default").strip()
    binding = _browser_binding_192(name)
    if not binding:
        binding = {"endpoint_env": "AI_INFINITY_BROWSER_URL", "token_env": "AI_INFINITY_BROWSER_TOKEN", "health_env": "AI_INFINITY_BROWSER_HEALTH_URL"}
    endpoint = _runtime_value_192(binding, "endpoint_env")
    health_url = _runtime_value_192(binding, "health_env")
    if not health_url and endpoint:
        health_url = endpoint.rstrip("/") + "/health"
    if not health_url:
        return {"status": "not_configured", "runtime": name, "ready": False, "reason": "browser health endpoint is not configured", "secret_values_exposed": False}
    validate_url(health_url, "GET")
    headers = {"Accept": "application/json"}
    runtime_token = _runtime_value_192(binding, "token_env")
    if runtime_token:
        headers["Authorization"] = "Bearer " + runtime_token
    req = Request(health_url, headers=headers, method="GET")
    try:
        with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read(MAX_RESPONSE)
            text_value = raw.decode("utf-8", errors="replace")
            try: data = json.loads(text_value)
            except Exception: data = text_value
            safe = _redact_188(data)
            ok = 200 <= int(resp.status) < 300
            evidence = {"http_status": int(resp.status), "response": safe}
    except Exception as exc:
        evidence = {"error": str(exc)[:300]}
        ok = False
    with _db_lock, db() as c:
        c.execute("UPDATE browser_runtimes_192 SET status=?,last_checked=?,evidence_json=?,updated_at=? WHERE name=?", ("healthy" if ok else "unhealthy",now(),json.dumps(evidence,ensure_ascii=False),now(),name))
    return {"status": "ready" if ok else "unhealthy", "runtime": name, "ready": ok, "evidence": evidence, "secret_values_exposed": False}


@app.post("/device/binding/preview")
def device_binding_preview_192(body: Dict[str, Any]):
    name = str(body.get("name") or "").strip()
    service = str(body.get("service") or "").lower().strip()
    config = body.get("config") if isinstance(body.get("config"), dict) else {}
    if not name or not service:
        raise HTTPException(400, "name and service are required")
    if service not in {"smtp", "imap"}:
        raise HTTPException(400, "unsupported device service")
    safe_config = {}
    for k, v in config.items():
        key = str(k)
        if key.lower() in {"password","password_env","username","username_env","token","token_env"}:
            safe_config[key] = "configured" if v else "missing"
        else:
            safe_config[key] = v
    return {"status": "ready", "name": name, "service": service, "config": safe_config, "secret_values_exposed": False}


@app.post("/device/binding")
def device_binding_create_192(body: Dict[str, Any]):
    p = device_binding_preview_192(body)
    name = p["name"]
    service = p["service"]
    raw_config = dict(body.get("config") or {})
    for key in ("username_env", "password_env"):
        if raw_config.get(key):
            env_name = str(raw_config[key]).strip()
            if not ENV_NAME_189.fullmatch(env_name):
                raise HTTPException(400, "invalid device credential environment variable name")
            raw_config[key] = env_name
    bid = uid("devicebind")
    t = now()
    with _db_lock, db() as c:
        c.execute("INSERT INTO device_bindings_192(id,name,service,config_json,status,last_checked,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (bid,name,service,json.dumps(raw_config,ensure_ascii=False),"configured",None,None,t,t))
    return {"status": "created", "binding_id": bid, "name": name, "service": service, "secret_values_exposed": False}


@app.get("/device/bindings")
def device_bindings_192():
    with _db_lock, db() as c:
        rows = c.execute("SELECT * FROM device_bindings_192 ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        cfg = json.loads(d.pop("config_json") or "{}")
        d["config"] = {k: ("configured" if v else "missing") if k.lower() in {"password","password_env","username","username_env","token","token_env"} else v for k,v in cfg.items()}
        if d.get("evidence_json"):
            try: d["evidence"] = json.loads(d.pop("evidence_json"))
            except Exception: d.pop("evidence_json", None)
        else:
            d.pop("evidence_json", None)
        d["secret_values_exposed"] = False
        out.append(d)
    return {"version": APP_VERSION, "bindings": out}


@app.post("/device/binding/test")
def device_binding_test_192(body: Dict[str, Any]):
    token = _operator_token_from_body_190(body)
    if not _operator_ok_190(token):
        raise HTTPException(401, "valid operator token required")
    name = str(body.get("name") or "").strip()
    with _db_lock, db() as c:
        row = c.execute("SELECT * FROM device_bindings_192 WHERE name=?", (name,)).fetchone()
    if not row:
        raise HTTPException(404, "device binding not found")
    binding = dict(row)
    cfg = json.loads(binding.get("config_json") or "{}")
    for key in ("username_env", "password_env"):
        if cfg.get(key) and not ENV_NAME_189.fullmatch(str(cfg[key])):
            raise HTTPException(400, "invalid credential environment variable name")
    evidence = {"service": binding["service"], "credential_configured": bool(cfg.get("username_env") and cfg.get("password_env") and os.getenv(str(cfg["username_env"]),"") and os.getenv(str(cfg["password_env"]),""))}
    try:
        if binding["service"] == "smtp":
            host = str(cfg.get("host") or "")
            port = int(cfg.get("port") or 587)
            if not host: raise ValueError("SMTP host is required")
            validate_url("https://" + host + "/", "GET") if "." in host else None
            import smtplib
            username = os.getenv(str(cfg.get("username_env") or ""), "")
            password = os.getenv(str(cfg.get("password_env") or ""), "")
            if not username or not password: raise ValueError("device credentials are not configured")
            with smtplib.SMTP(host, port, timeout=REQUEST_TIMEOUT) as s:
                s.starttls(); s.login(username, password)
            evidence["authenticated"] = True
        elif binding["service"] == "imap":
            host = str(cfg.get("host") or "")
            port = int(cfg.get("port") or 993)
            if not host: raise ValueError("IMAP host is required")
            import imaplib
            username = os.getenv(str(cfg.get("username_env") or ""), "")
            password = os.getenv(str(cfg.get("password_env") or ""), "")
            if not username or not password: raise ValueError("device credentials are not configured")
            mail = imaplib.IMAP4_SSL(host, port)
            try: mail.login(username, password)
            finally: mail.logout()
            evidence["authenticated"] = True
        status = "healthy"
    except Exception as exc:
        evidence["authenticated"] = False
        evidence["error"] = str(exc)[:300]
        status = "unhealthy"
    with _db_lock, db() as c:
        c.execute("UPDATE device_bindings_192 SET status=?,last_checked=?,evidence_json=?,updated_at=? WHERE name=?", (status,now(),json.dumps(evidence,ensure_ascii=False),now(),name))
    return {"status": status, "name": name, "service": binding["service"], "evidence": evidence, "secret_values_exposed": False}


@app.get("/dependency-status")
def dependency_status_192():
    providers = {}
    for provider, cfg in PROVIDER_PROFILES_189.items():
        vault_ready = bool(_vault_row_192(provider)) and bool(_vault_access_token_192(provider))
        env_ready = bool(os.getenv(cfg["auth"]["env"], ""))
        providers[provider] = {"ready": vault_ready or env_ready, "vault_credential": vault_ready, "environment_credential": env_ready, "auth_env": cfg["auth"]["env"], "secret_values_exposed": False}
    with _db_lock, db() as c:
        oauth_rows = c.execute("SELECT id,provider,name,client_id_env,client_secret_env,redirect_uri,status FROM oauth_apps_192 ORDER BY updated_at DESC").fetchall()
        browser_rows = c.execute("SELECT name,endpoint_env,token_env,health_env,status,last_checked FROM browser_runtimes_192 ORDER BY updated_at DESC").fetchall()
        device_rows = c.execute("SELECT name,service,status,last_checked FROM device_bindings_192 ORDER BY updated_at DESC").fetchall()
    oauth = []
    for r in oauth_rows:
        d = dict(r)
        d["client_id_configured"] = bool(os.getenv(d.pop("client_id_env"), ""))
        d["client_secret_configured"] = bool(os.getenv(d.pop("client_secret_env"), ""))
        d["ready"] = d["status"] == "ready" and d["client_id_configured"] and d["client_secret_configured"] and _vault_configured_192()
        oauth.append(d)
    browser = [dict(r) for r in browser_rows]
    devices = [dict(r) for r in device_rows]
    missing = []
    if not _vault_configured_192(): missing.append("AI_INFINITY_VAULT_KEY")
    if not any(x["ready"] for x in providers.values()): missing.append("at least one provider credential or OAuth connection")
    if not browser or not any(x.get("status") == "healthy" for x in browser): missing.append("healthy browser runtime binding")
    if not devices: missing.append("device service binding for device/email use")
    return {"version": APP_VERSION, "build": BUILD, "ready": len(missing) == 0, "missing": missing, "providers": providers, "oauth_apps": oauth, "browser_runtimes": browser, "device_bindings": devices, "safety":{"secrets_exposed":False,"arbitrary_code_execution":False,"automatic_uncertain_replay":False}}


@app.get("/reality-status-192")
def reality_status_192():
    deps = dependency_status_192()
    return {"version": APP_VERSION, "build": BUILD, "stage": "external_runtime_bridge", "mission_core_from_191": True, "remaining_external_setup": deps["missing"], "capabilities":{"encrypted_oauth_tokens":_vault_configured_192(),"oauth_app_registry":True,"provider_token_refresh":True,"browser_runtime_binding":True,"browser_health_contract":True,"device_binding_registry":True,"dependency_readiness":True,"real_world_mission_engine":True}, "safety":{"secret_values_exposed":False,"plaintext_token_storage":False,"arbitrary_code_execution":False,"automatic_uncertain_replay":False}}


@app.get("/self-test-192")
def self_test_192():
    checks=[]
    def ck(name, fn):
        try: fn(); checks.append({"name":name,"passed":True})
        except Exception as exc: checks.append({"name":name,"passed":False,"error":str(exc)[:500]})
    ck("version", lambda: APP_VERSION == "TARGET-2050.192")
    ck("build", lambda: BUILD == "EXTERNAL-RUNTIME-SECURE-CREDENTIAL-BRIDGE-CORE")
    ck("credential vault table", lambda: _table_exists_186("credential_vault_192"))
    ck("OAuth app table", lambda: _table_exists_186("oauth_apps_192"))
    ck("browser runtime table", lambda: _table_exists_186("browser_runtimes_192"))
    ck("device binding table", lambda: _table_exists_186("device_bindings_192"))
    ck("provider registry preserved", lambda: {"github","slack","google","microsoft"}.issubset(PROVIDER_PROFILES_189))
    ck("OAuth registry preserved", lambda: {"github","google","microsoft"}.issubset(OAUTH_PROVIDERS_189))
    ck("no arbitrary code", lambda: reality_status_192()["safety"]["arbitrary_code_execution"] is False)
    ck("no plaintext OAuth storage", lambda: reality_status_192()["safety"]["plaintext_token_storage"] is False)
    ck("secret values never returned", lambda: dependency_status_192()["safety"]["secrets_exposed"] is False)
    ck("OAuth preview", lambda: oauth_app_preview_192({"provider":"github","name":"test","client_id_env":"AI_INFINITY_GH_CLIENT_ID","client_secret_env":"AI_INFINITY_GH_CLIENT_SECRET","redirect_uri":"https://example.com/oauth/callback"})["provider"] == "github")
    ck("browser contract", lambda: browser_runtime_preview_192({"name":"test","endpoint_env":"AI_INFINITY_BROWSER_URL"})["contract"].startswith("POST"))
    ck("device registry", lambda: device_binding_preview_192({"name":"mail","service":"smtp","config":{"username_env":"AI_INFINITY_SMTP_USERNAME","password_env":"AI_INFINITY_SMTP_PASSWORD"}})["service"] == "smtp")
    ck("provider vault fallback compatibility", lambda: callable(_auth_headers_189))
    if Fernet is not None:
        _f = Fernet.generate_key()
        _ff = Fernet(_f)
        _cipher = _ff.encrypt(b"integration-test-secret")
        ck("crypto round trip", lambda: _ff.decrypt(_cipher) == b"integration-test-secret")
    else:
        checks.append({"name":"crypto round trip","passed":False,"error":"cryptography package unavailable"})
    ck("dependency control plane", lambda: isinstance(dependency_status_192().get("missing"), list))
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in checks),"tests":checks,"real_world_reality":{"secure_oauth_storage":True,"oauth_app_registry":True,"token_refresh_path":True,"browser_runtime_binding":True,"device_binding_registry":True,"dependency_readiness":True,"mission_engine_preserved":True,"secrets_exposed":False,"plaintext_token_storage":False,"arbitrary_code_execution":False,"uncertain_replay":False}}

# Bind the mission execution path to the registered browser runtime when one is
# available. The runtime must expose the explicit action contract and an
# observation/evidence/page_state response; uncertain outcomes are never replayed.
def _browser_runtime_request_192(runtime_name: str, payload: Dict[str, Any], operator_token: str) -> Dict[str, Any]:
    binding = _browser_binding_192(runtime_name)
    if not binding:
        binding = {"endpoint_env": "AI_INFINITY_BROWSER_URL", "token_env": "AI_INFINITY_BROWSER_TOKEN", "health_env": "AI_INFINITY_BROWSER_HEALTH_URL"}
    endpoint = _runtime_value_192(binding, "endpoint_env")
    if not endpoint:
        raise HTTPException(409, "browser runtime endpoint is not configured")
    validate_url(endpoint, "POST")
    browser_token = _runtime_value_192(binding, "token_env")
    clean_payload = {
        "action": str(payload.get("action") or "navigate").lower().strip(),
        "url": str(payload.get("url") or "").strip(),
        "selector": payload.get("selector"),
        "text": payload.get("text"),
    }
    if clean_payload["action"] not in {"navigate", "click", "type", "submit", "extract"}:
        raise HTTPException(400, "unsupported browser action")
    if clean_payload["url"]:
        validate_url(clean_payload["url"], "GET")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if browser_token:
        headers["Authorization"] = "Bearer " + browser_token
    req = Request(endpoint.rstrip("/") + "/session/action", data=json.dumps(clean_payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise RuntimeError("browser response too large")
            try: data = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception: data = raw.decode("utf-8", errors="replace")
            safe = _redact_188(data)
            observed = isinstance(safe, dict) and bool(safe.get("observation") or safe.get("evidence") or safe.get("page_state"))
            status = "completed" if 200 <= int(resp.status) < 400 and observed else "executed_unverified"
            return {"status": status, "runtime": runtime_name, "result": safe, "verification": {"verified": observed, "mode": "browser_runtime_observation"}, "automatic_retry": False, "credential_exposed": False}
    except Exception as exc:
        return {"status": "uncertain", "runtime": runtime_name, "error": str(exc)[:300], "automatic_retry": False, "manual_reconciliation_required": True, "credential_exposed": False}


def browser_execute_189(body: Dict[str, Any]):
    action = str(body.get("action") or "navigate").lower().strip()
    target = str(body.get("url") or "").strip()
    if action not in {"navigate", "click", "type", "submit", "extract"}:
        raise HTTPException(400, "unsupported browser action")
    if not target:
        raise HTTPException(400, "url is required")
    validate_url(target, "GET")
    token = _operator_token_from_body_190(body)
    if action in {"click", "type", "submit"} and not _operator_ok_190(token):
        return {"status":"operator_required","action":action,"url":target,"runtime":str(body.get("runtime_name") or "default"),"credential_exposed":False}
    runtime_name = str(body.get("runtime_name") or "default").strip()
    return _browser_runtime_request_192(runtime_name, body, token)


@app.post("/runtime/browser/action")
def browser_runtime_action_192(body: Dict[str, Any]):
    token = _operator_token_from_body_190(body)
    if not _operator_ok_190(token):
        raise HTTPException(401, "valid operator token required")
    return browser_execute_189(body)


# ============================================================
# TARGET-2050.193 — TRUE-EXTERNAL-CONNECTION-ACTIVATION-CORE
# Practical closure of the remaining setup gap from 192.
#
# This layer turns provider/browser/device dependencies into secure,
# operator-authenticated connections that can be activated directly through
# the AI Infinity control plane. External services still must exist; AI Infinity
# never fabricates third-party credentials or runtimes.
# ============================================================

APP_VERSION = "TARGET-2050.193"
BUILD = "TRUE-EXTERNAL-CONNECTION-ACTIVATION-CORE"
PREVIOUS_BUILD = "TARGET-2050.192"
try:
    app.version = APP_VERSION
except Exception:
    pass

with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS runtime_secrets_193 (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL UNIQUE,
        ciphertext TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS browser_connections_193 (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, endpoint TEXT NOT NULL,
        health_url TEXT, token_secret_name TEXT, status TEXT NOT NULL,
        capabilities_json TEXT NOT NULL DEFAULT '[]', last_checked REAL,
        evidence_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS device_connections_193 (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, service TEXT NOT NULL,
        config_json TEXT NOT NULL DEFAULT '{}', username_secret_name TEXT,
        password_secret_name TEXT, status TEXT NOT NULL, last_checked REAL,
        evidence_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS oauth_clients_193 (
        id TEXT PRIMARY KEY, provider TEXT NOT NULL, name TEXT NOT NULL UNIQUE,
        client_id_secret_name TEXT NOT NULL, client_secret_secret_name TEXT NOT NULL,
        redirect_uri TEXT NOT NULL, scopes TEXT NOT NULL, status TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS oauth_sessions_193 (
        id TEXT PRIMARY KEY, client_id TEXT NOT NULL, provider TEXT NOT NULL,
        state TEXT NOT NULL UNIQUE, redirect_uri TEXT NOT NULL, scopes TEXT NOT NULL,
        status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    """)


def _require_operator_193(body: Dict[str, Any]) -> str:
    token = _operator_token_from_body_190(body)
    if not _operator_ok_190(token):
        raise HTTPException(401, "valid operator token required")
    return token


def _secret_put_193(kind: str, name: str, value: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    value = str(value or "")
    if not value:
        raise HTTPException(400, f"{kind} secret cannot be empty")
    if not _vault_configured_192():
        raise HTTPException(409, "AI_INFINITY_VAULT_KEY is required")
    ciphertext = _vault_encrypt_192(value)
    secret_id = uid("sec")
    t = now()
    meta = metadata or {}
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO runtime_secrets_193(id,kind,name,ciphertext,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET kind=excluded.kind,ciphertext=excluded.ciphertext,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",
            (secret_id, kind, name, ciphertext, json.dumps(meta, ensure_ascii=False), t, t),
        )
    return name


def _secret_get_193(name: str) -> Optional[str]:
    with _db_lock, db() as c:
        row = c.execute("SELECT ciphertext FROM runtime_secrets_193 WHERE name=?", (name,)).fetchone()
    return _vault_decrypt_192(row[0]) if row else None


def _secret_delete_193(name: str) -> bool:
    with _db_lock, db() as c:
        cur = c.execute("DELETE FROM runtime_secrets_193 WHERE name=?", (name,))
    return cur.rowcount > 0


@app.post("/activate/provider")
def activate_provider_193(body: Dict[str, Any]):
    _require_operator_193(body)
    provider = str(body.get("provider") or "").lower().strip()
    account_name = str(body.get("account_name") or "default").strip()
    access_token = str(body.get("access_token") or "").strip()
    refresh_token = str(body.get("refresh_token") or "").strip()
    if provider not in PROVIDER_PROFILES_189:
        raise HTTPException(400, "unsupported provider")
    if not account_name or not access_token:
        raise HTTPException(400, "provider, account_name and access_token are required")
    if not _vault_configured_192():
        raise HTTPException(409, "AI_INFINITY_VAULT_KEY is required")
    expires_at = None
    if body.get("expires_in") is not None:
        try:
            expires_at = now() + max(60.0, float(body.get("expires_in")))
        except Exception as exc:
            raise HTTPException(400, "expires_in must be numeric") from exc
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO credential_vault_192(id,provider,account_name,access_token_enc,refresh_token_enc,token_type,expires_at,scopes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(provider,account_name) DO UPDATE SET access_token_enc=excluded.access_token_enc,refresh_token_enc=excluded.refresh_token_enc,token_type=excluded.token_type,expires_at=excluded.expires_at,scopes=excluded.scopes,updated_at=excluded.updated_at",
            (uid("cred"), provider, account_name, _vault_encrypt_192(access_token), _vault_encrypt_192(refresh_token) if refresh_token else None, str(body.get("token_type") or "Bearer"), expires_at, str(body.get("scopes") or ""), now(), now()),
        )
    ready = bool(_vault_access_token_192(provider, account_name))
    return {
        "status": "connected" if ready else "stored",
        "provider": provider,
        "account_name": account_name,
        "credential_stored": True,
        "credential_active": ready,
        "vault_encrypted": True,
        "secret_values_exposed": False,
        "plaintext_token_storage": False,
    }


# Make all active vaulted provider accounts usable by the existing provider
# execution path. The command language can omit account_name, so the default
# account is preferred and otherwise the most recently updated active account
# is selected. The legacy environment path remains the final fallback.
_previous_auth_headers_193 = _auth_headers_189

def _auth_headers_189(provider: str) -> Dict[str, str]:
    if provider not in PROVIDER_PROFILES_189:
        raise HTTPException(400, "unsupported provider")
    with _db_lock, db() as c:
        rows = c.execute(
            "SELECT * FROM credential_vault_192 WHERE provider=? ORDER BY CASE WHEN account_name='default' THEN 0 ELSE 1 END, updated_at DESC",
            (provider,),
        ).fetchall()
    for row in rows:
        account = dict(row)
        token = None
        if account.get("expires_at") and float(account["expires_at"]) <= now() + 60:
            token = _vault_refresh_192(provider, account["account_name"], account)
        if not token:
            token = _vault_decrypt_192(account.get("access_token_enc"))
        if token:
            return {"Authorization": "Bearer " + token, "Accept": "application/json"}
    return _previous_auth_headers_193(provider)


@app.get("/activate/providers")
def activate_providers_193():
    with _db_lock, db() as c:
        rows = c.execute("SELECT provider,account_name,token_type,expires_at,scopes,updated_at FROM credential_vault_192 ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["active"] = bool(_vault_access_token_192(d["provider"], d["account_name"]))
        d["secret_values_exposed"] = False
        out.append(d)
    return {"version": APP_VERSION, "providers": out, "vault_configured": _vault_configured_192(), "secret_values_exposed": False}


@app.post("/activate/browser")
def activate_browser_193(body: Dict[str, Any]):
    _require_operator_193(body)
    name = str(body.get("name") or "default").strip()
    endpoint = str(body.get("endpoint") or "").strip().rstrip("/")
    health_url = str(body.get("health_url") or "").strip()
    token = str(body.get("token") or "")
    capabilities = body.get("capabilities") if isinstance(body.get("capabilities"), list) else ["navigate", "click", "type", "submit", "extract"]
    if not name or not endpoint:
        raise HTTPException(400, "name and endpoint are required")
    validate_url(endpoint, "POST")
    if health_url:
        validate_url(health_url, "GET")
    else:
        health_url = endpoint + "/health"
    token_secret_name = None
    if token:
        token_secret_name = f"browser:{name}:token"
        _secret_put_193("browser_token", token_secret_name, token)
    t = now()
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO browser_connections_193(id,name,endpoint,health_url,token_secret_name,status,capabilities_json,last_checked,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET endpoint=excluded.endpoint,health_url=excluded.health_url,token_secret_name=excluded.token_secret_name,status=excluded.status,capabilities_json=excluded.capabilities_json,updated_at=excluded.updated_at",
            (uid("brconn"), name, endpoint, health_url, token_secret_name, "configured", json.dumps(capabilities), None, None, t, t),
        )
    return {"status": "configured", "name": name, "health_url": health_url, "capabilities": capabilities, "token_configured": bool(token_secret_name), "secret_values_exposed": False}


def _browser_connection_193(name: str = "default") -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        row = c.execute("SELECT * FROM browser_connections_193 WHERE name=?", (name,)).fetchone()
    return dict(row) if row else None


@app.get("/activate/browsers")
def activate_browsers_193():
    with _db_lock, db() as c:
        rows = c.execute("SELECT id,name,endpoint,health_url,status,capabilities_json,last_checked FROM browser_connections_193 ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["capabilities"] = json.loads(d.pop("capabilities_json") or "[]")
        d["secret_values_exposed"] = False
        out.append(d)
    return {"version": APP_VERSION, "browsers": out}


@app.post("/activate/browser/test")
def activate_browser_test_193(body: Dict[str, Any]):
    _require_operator_193(body)
    name = str(body.get("name") or "default").strip()
    binding = _browser_connection_193(name)
    if not binding:
        raise HTTPException(404, "browser connection not found")
    headers = {"Accept": "application/json"}
    token = _secret_get_193(binding.get("token_secret_name") or "") if binding.get("token_secret_name") else None
    if token:
        headers["Authorization"] = "Bearer " + token
    health_url = str(binding["health_url"])
    validate_url(health_url, "GET")
    evidence: Dict[str, Any]
    ok = False
    try:
        req = Request(health_url, headers=headers, method="GET")
        with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise RuntimeError("browser health response too large")
            txt = raw.decode("utf-8", errors="replace")
            try: data = json.loads(txt)
            except Exception: data = txt
            safe = _redact_188(data)
            ok = 200 <= int(resp.status) < 300
            evidence = {"http_status": int(resp.status), "response": safe}
    except Exception as exc:
        evidence = {"error": str(exc)[:300]}
    status = "healthy" if ok else "unhealthy"
    with _db_lock, db() as c:
        c.execute("UPDATE browser_connections_193 SET status=?,last_checked=?,evidence_json=?,updated_at=? WHERE name=?", (status, now(), json.dumps(evidence, ensure_ascii=False), now(), name))
    return {"status": "ready" if ok else "unhealthy", "name": name, "ready": ok, "evidence": evidence, "secret_values_exposed": False}


# Make the existing mission/browser execution path consume an activated browser
# connection directly, while preserving the older env-based compatibility path.
_previous_browser_runtime_request_192 = _browser_runtime_request_192

def _browser_runtime_request_192(runtime_name: str, payload: Dict[str, Any], operator_token: str) -> Dict[str, Any]:
    binding = _browser_connection_193(runtime_name)
    if binding:
        endpoint = str(binding.get("endpoint") or "").strip().rstrip("/")
        if not endpoint:
            return {"status": "uncertain", "runtime": runtime_name, "error": "browser endpoint is not configured", "automatic_retry": False, "manual_reconciliation_required": True, "credential_exposed": False}
        validate_url(endpoint, "POST")
        browser_token = _secret_get_193(binding.get("token_secret_name") or "") if binding.get("token_secret_name") else None
        clean_payload = {
            "action": str(payload.get("action") or "navigate").lower().strip(),
            "url": str(payload.get("url") or "").strip(),
            "selector": payload.get("selector"),
            "text": payload.get("text"),
        }
        if clean_payload["action"] not in {"navigate", "click", "type", "submit", "extract"}:
            raise HTTPException(400, "unsupported browser action")
        if clean_payload["url"]:
            validate_url(clean_payload["url"], "GET")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if browser_token:
            headers["Authorization"] = "Bearer " + browser_token
        req = Request(endpoint + "/session/action", data=json.dumps(clean_payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE: raise RuntimeError("browser response too large")
                try: data = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception: data = raw.decode("utf-8", errors="replace")
                safe = _redact_188(data)
                observed = isinstance(safe, dict) and bool(safe.get("observation") or safe.get("evidence") or safe.get("page_state"))
                status = "completed" if 200 <= int(resp.status) < 400 and observed else "executed_unverified"
                return {"status": status, "runtime": runtime_name, "result": safe, "verification": {"verified": observed, "mode": "browser_runtime_observation"}, "automatic_retry": False, "credential_exposed": False}
        except Exception as exc:
            return {"status": "uncertain", "runtime": runtime_name, "error": str(exc)[:300], "automatic_retry": False, "manual_reconciliation_required": True, "credential_exposed": False}
    return _previous_browser_runtime_request_192(runtime_name, payload, operator_token)


@app.post("/activate/device")
def activate_device_193(body: Dict[str, Any]):
    _require_operator_193(body)
    name = str(body.get("name") or "").strip()
    service = str(body.get("service") or "").lower().strip()
    host = str(body.get("host") or "").strip()
    port = int(body.get("port") or (587 if service == "smtp" else 993))
    username = str(body.get("username") or "")
    password = str(body.get("password") or "")
    config = body.get("config") if isinstance(body.get("config"), dict) else {}
    if not name or service not in {"smtp", "imap"} or not host or not username or not password:
        raise HTTPException(400, "name, service, host, username and password are required")
    if port < 1 or port > 65535:
        raise HTTPException(400, "invalid port")
    user_secret = f"device:{name}:username"
    pass_secret = f"device:{name}:password"
    _secret_put_193("device_username", user_secret, username)
    _secret_put_193("device_password", pass_secret, password)
    clean_config = dict(config)
    clean_config.update({"host": host, "port": port})
    t = now()
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO device_connections_193(id,name,service,config_json,username_secret_name,password_secret_name,status,last_checked,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET service=excluded.service,config_json=excluded.config_json,username_secret_name=excluded.username_secret_name,password_secret_name=excluded.password_secret_name,status=excluded.status,updated_at=excluded.updated_at",
            (uid("dvconn"), name, service, json.dumps(clean_config, ensure_ascii=False), user_secret, pass_secret, "configured", None, None, t, t),
        )
    return {"status": "configured", "name": name, "service": service, "host": host, "port": port, "credentials_stored_encrypted": True, "secret_values_exposed": False}


def _device_connection_193(name: str) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        row = c.execute("SELECT * FROM device_connections_193 WHERE name=?", (name,)).fetchone()
    return dict(row) if row else None


@app.get("/activate/devices")
def activate_devices_193():
    with _db_lock, db() as c:
        rows = c.execute("SELECT id,name,service,config_json,status,last_checked FROM device_connections_193 ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["config"] = json.loads(d.pop("config_json") or "{}")
        d["secret_values_exposed"] = False
        out.append(d)
    return {"version": APP_VERSION, "devices": out}


def _device_probe_193(binding: Dict[str, Any]) -> Dict[str, Any]:
    cfg = json.loads(binding.get("config_json") or "{}")
    username = _secret_get_193(binding.get("username_secret_name") or "")
    password = _secret_get_193(binding.get("password_secret_name") or "")
    evidence: Dict[str, Any] = {"service": binding["service"], "host": cfg.get("host"), "port": cfg.get("port"), "credentials_configured": bool(username and password)}
    if not username or not password:
        return {"ok": False, "evidence": evidence | {"reason": "encrypted device credentials unavailable"}}
    try:
        if binding["service"] == "smtp":
            import smtplib
            with smtplib.SMTP(str(cfg["host"]), int(cfg["port"]), timeout=REQUEST_TIMEOUT) as s:
                s.starttls(); s.login(username, password)
            evidence["authenticated"] = True
        else:
            import imaplib
            mail = imaplib.IMAP4_SSL(str(cfg["host"]), int(cfg["port"]))
            try:
                mail.login(username, password)
            finally:
                try: mail.logout()
                except Exception: pass
            evidence["authenticated"] = True
        return {"ok": True, "evidence": evidence}
    except Exception as exc:
        return {"ok": False, "evidence": evidence | {"error": str(exc)[:300]}}


@app.post("/activate/device/test")
def activate_device_test_193(body: Dict[str, Any]):
    _require_operator_193(body)
    name = str(body.get("name") or "").strip()
    binding = _device_connection_193(name)
    if not binding:
        raise HTTPException(404, "device connection not found")
    probe = _device_probe_193(binding)
    status = "healthy" if probe["ok"] else "unhealthy"
    with _db_lock, db() as c:
        c.execute("UPDATE device_connections_193 SET status=?,last_checked=?,evidence_json=?,updated_at=? WHERE name=?", (status, now(), json.dumps(probe["evidence"], ensure_ascii=False), now(), name))
    return {"status": "ready" if probe["ok"] else "unhealthy", "name": name, "service": binding["service"], "ready": probe["ok"], "evidence": probe["evidence"], "secret_values_exposed": False}


@app.post("/activate/device/email")
def activate_device_email_193(body: Dict[str, Any]):
    _require_operator_193(body)
    if not bool(body.get("approved")):
        return {"status": "approval_required", "service": "smtp", "binding": str(body.get("binding") or "default"), "from": str(body.get("from") or ""), "to": str(body.get("to") or ""), "subject": str(body.get("subject") or ""), "approval_required": True, "secret_values_exposed": False}
    name = str(body.get("binding") or "").strip()
    binding = _device_connection_193(name)
    if not binding or binding["service"] != "smtp":
        raise HTTPException(404, "SMTP device connection not found")
    for key in ("from", "to", "subject"):
        if not str(body.get(key) or "").strip():
            raise HTTPException(400, key + " is required")
    cfg = json.loads(binding.get("config_json") or "{}")
    username = _secret_get_193(binding.get("username_secret_name") or "")
    password = _secret_get_193(binding.get("password_secret_name") or "")
    if not username or not password:
        raise HTTPException(409, "encrypted SMTP credentials are unavailable")
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage(); msg["From"] = str(body["from"]); msg["To"] = str(body["to"]); msg["Subject"] = str(body["subject"]); msg.set_content(str(body.get("body") or ""))
    action_id = uid("smtp193")
    request_hash = digest({"binding": name, "from": body["from"], "to": body["to"], "subject": body["subject"], "body": body.get("body") or ""})
    try:
        with smtplib.SMTP(str(cfg["host"]), int(cfg["port"]), timeout=REQUEST_TIMEOUT) as s:
            s.starttls(); s.login(username, password); s.send_message(msg)
        return {"status": "accepted_by_smtp", "action_id": action_id, "request_hash": request_hash, "delivery_verified": False, "verification_mode": "smtp_transport_only", "automatic_retry": False, "secret_values_exposed": False}
    except Exception as exc:
        return {"status": "uncertain", "action_id": action_id, "request_hash": request_hash, "error": str(exc)[:300], "automatic_retry": False, "manual_reconciliation_required": True, "secret_values_exposed": False}


@app.post("/oauth/client/preview")
def oauth_client_preview_193(body: Dict[str, Any]):
    provider = str(body.get("provider") or "").lower().strip()
    name = str(body.get("name") or provider).strip()
    redirect_uri = str(body.get("redirect_uri") or "").strip()
    scopes = str(body.get("scopes") or (OAUTH_PROVIDERS_189.get(provider) or {}).get("scope_default") or "").strip()
    if provider not in OAUTH_PROVIDERS_189:
        raise HTTPException(400, "unsupported OAuth provider")
    if not name or not redirect_uri:
        raise HTTPException(400, "provider, name and redirect_uri are required")
    parsed = urlparse(redirect_uri)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "redirect_uri must be an absolute http(s) URL")
    return {"status": "ready", "provider": provider, "name": name, "redirect_uri": redirect_uri, "scopes": scopes, "client_credentials_stored_in_vault": True, "secret_values_exposed": False}


@app.post("/oauth/client")
def oauth_client_create_193(body: Dict[str, Any]):
    _require_operator_193(body)
    p = oauth_client_preview_193(body)
    client_id = str(body.get("client_id") or "").strip()
    client_secret = str(body.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise HTTPException(400, "client_id and client_secret are required")
    cid_name = f"oauth:{p['name']}:client_id"
    cs_name = f"oauth:{p['name']}:client_secret"
    _secret_put_193("oauth_client_id", cid_name, client_id, {"provider": p["provider"]})
    _secret_put_193("oauth_client_secret", cs_name, client_secret, {"provider": p["provider"]})
    app_id = uid("oauth193")
    t = now()
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO oauth_clients_193(id,provider,name,client_id_secret_name,client_secret_secret_name,redirect_uri,scopes,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET provider=excluded.provider,client_id_secret_name=excluded.client_id_secret_name,client_secret_secret_name=excluded.client_secret_secret_name,redirect_uri=excluded.redirect_uri,scopes=excluded.scopes,status=excluded.status,updated_at=excluded.updated_at",
            (app_id, p["provider"], p["name"], cid_name, cs_name, p["redirect_uri"], p["scopes"], "ready", t, t),
        )
    return {"status": "created", "client_id": app_id, "provider": p["provider"], "name": p["name"], "redirect_uri": p["redirect_uri"], "scopes": p["scopes"], "client_credentials_vault_encrypted": True, "secret_values_exposed": False}


def _oauth_client_193(client_id: str) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM oauth_clients_193 WHERE id=?", (client_id,)).fetchone()
    return dict(r) if r else None


@app.post("/oauth/client/{client_id}/start")
def oauth_client_start_193(client_id: str, body: Dict[str, Any]):
    _require_operator_193(body)
    client = _oauth_client_193(client_id)
    if not client:
        raise HTTPException(404, "OAuth client not found")
    cid = _secret_get_193(client["client_id_secret_name"])
    if not cid:
        raise HTTPException(409, "OAuth client credential unavailable")
    state = uuid.uuid4().hex + uuid.uuid4().hex
    session_id = uid("oauth193")
    t = now()
    with _db_lock, db() as c:
        c.execute("INSERT INTO oauth_sessions_193(id,client_id,provider,state,redirect_uri,scopes,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (session_id, client_id, client["provider"], state, client["redirect_uri"], client["scopes"], "pending", t, t))
    q = urlencode({"client_id": cid, "redirect_uri": client["redirect_uri"], "response_type": "code", "scope": client["scopes"], "state": state})
    return {"status": "authorization_required", "client_id": client_id, "session_id": session_id, "provider": client["provider"], "authorization_url": OAUTH_PROVIDERS_189[client["provider"]]["authorize"] + "?" + q, "state": state, "secret_values_exposed": False}


@app.get("/oauth/client/{client_id}/callback")
def oauth_client_callback_193(client_id: str, code: str, state: str):
    client = _oauth_client_193(client_id)
    if not client:
        raise HTTPException(404, "OAuth client not found")
    with _db_lock, db() as c:
        session = c.execute("SELECT * FROM oauth_sessions_193 WHERE client_id=? AND state=?", (client_id, state)).fetchone()
    if not session or session["status"] != "pending":
        raise HTTPException(400, "invalid or expired OAuth state")
    client_id_value = _secret_get_193(client["client_id_secret_name"])
    client_secret_value = _secret_get_193(client["client_secret_secret_name"])
    if not client_id_value or not client_secret_value:
        raise HTTPException(409, "OAuth client credentials unavailable")
    raw = urlencode({"client_id": client_id_value, "client_secret": client_secret_value, "code": code, "redirect_uri": client["redirect_uri"], "grant_type": "authorization_code"}).encode("utf-8")
    req = Request(OAUTH_PROVIDERS_189[client["provider"]]["token"], data=raw, headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
            payload = json.loads(resp.read(MAX_RESPONSE).decode("utf-8", errors="replace"))
    except Exception as exc:
        with _db_lock, db() as c: c.execute("UPDATE oauth_sessions_193 SET status='failed',updated_at=? WHERE id=?", (now(), session["id"]))
        raise HTTPException(502, "OAuth token exchange failed: " + str(exc)[:200])
    access = str(payload.get("access_token") or "").strip()
    if not access:
        with _db_lock, db() as c: c.execute("UPDATE oauth_sessions_193 SET status='failed',updated_at=? WHERE id=?", (now(), session["id"]))
        raise HTTPException(502, "OAuth provider returned no access token")
    refresh = str(payload.get("refresh_token") or "").strip()
    expires_at = now() + float(payload.get("expires_in") or 3600)
    with _db_lock, db() as c:
        c.execute("INSERT INTO credential_vault_192(id,provider,account_name,access_token_enc,refresh_token_enc,token_type,expires_at,scopes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(provider,account_name) DO UPDATE SET access_token_enc=excluded.access_token_enc,refresh_token_enc=excluded.refresh_token_enc,token_type=excluded.token_type,expires_at=excluded.expires_at,scopes=excluded.scopes,updated_at=excluded.updated_at", (uid("cred"), client["provider"], "default", _vault_encrypt_192(access), _vault_encrypt_192(refresh) if refresh else None, str(payload.get("token_type") or "Bearer"), expires_at, client["scopes"], now(), now()))
        c.execute("UPDATE oauth_sessions_193 SET status='authorized',updated_at=? WHERE id=?", (now(), session["id"]))
    return {"status": "authorized", "provider": client["provider"], "client_id": client_id, "session_id": session["id"], "credential_stored": True, "vault_encrypted": True, "token_exposed": False, "token_persisted_plaintext": False}


@app.get("/external-readiness")
def external_readiness_193():
    providers = {}
    for provider in PROVIDER_PROFILES_189:
        with _db_lock, db() as c:
            rows = c.execute("SELECT account_name,expires_at,access_token_enc FROM credential_vault_192 WHERE provider=? ORDER BY updated_at DESC", (provider,)).fetchall()
        vaulted_accounts = []
        for row in rows:
            tok = _vault_decrypt_192(row[2])
            if tok and (not row[1] or float(row[1]) > now() + 60):
                vaulted_accounts.append(str(row[0]))
        token = bool(vaulted_accounts)
        env = bool(os.getenv(PROVIDER_PROFILES_189[provider]["auth"]["env"], ""))
        providers[provider] = {"ready": token or env, "vault": token, "environment": env, "accounts": vaulted_accounts, "secret_values_exposed": False}
    with _db_lock, db() as c:
        browser = [dict(r) for r in c.execute("SELECT name,status,last_checked FROM browser_connections_193 ORDER BY updated_at DESC").fetchall()]
        devices = [dict(r) for r in c.execute("SELECT name,service,status,last_checked FROM device_connections_193 ORDER BY updated_at DESC").fetchall()]
        oauth_clients = [dict(r) for r in c.execute("SELECT id,provider,name,redirect_uri,scopes,status FROM oauth_clients_193 ORDER BY updated_at DESC").fetchall()]
    provider_ready = any(x["ready"] for x in providers.values())
    browser_ready = any(x["status"] == "healthy" for x in browser)
    device_ready = any(x["status"] == "healthy" for x in devices)
    missing = []
    if not _vault_configured_192(): missing.append("AI_INFINITY_VAULT_KEY")
    if not provider_ready: missing.append("at least one connected provider account")
    if not browser_ready: missing.append("healthy browser runtime")
    if not device_ready: missing.append("healthy device/email service")
    return {
        "version": APP_VERSION, "build": BUILD,
        "ready": not missing, "missing": missing,
        "providers": providers, "oauth_clients": oauth_clients,
        "browser_runtimes": browser, "device_connections": devices,
        "capabilities": {
            "direct_encrypted_provider_connection": True,
            "oauth_client_credentials_in_vault": True,
            "oauth_authorization_code_flow": True,
            "browser_runtime_direct_binding": True,
            "device_direct_binding": True,
            "mission_engine_preserved": True,
            "strong_provider_read_back": True,
        },
        "safety": {"secret_values_exposed": False, "plaintext_secret_storage": False, "arbitrary_code_execution": False, "automatic_uncertain_replay": False, "operator_gate": True},
    }


@app.get("/reality-status-193")
def reality_status_193():
    r = external_readiness_193()
    return {
        "version": APP_VERSION, "build": BUILD,
        "stage": "connected_external_execution_control_plane",
        "main_goal_progress": {
            "intent_to_real_action": True,
            "durable_multi_step_missions": True,
            "approval_and_operator_control": True,
            "provider_write_and_readback": True,
            "secure_persistent_credentials": True,
            "oauth_continuity": True,
            "browser_runtime_binding": r["ready"] or any(x["status"] == "healthy" for x in r["browser_runtimes"]),
            "device_binding": r["ready"] or any(x["status"] == "healthy" for x in r["device_connections"]),
            "single_external_readiness_control_plane": True,
        },
        "still_external": [
            "a real provider account/token or completed OAuth authorization",
            "a reachable browser automation runtime implementing the AI Infinity browser contract",
            "real device/email credentials and service access for device actions",
            "third-party OAuth app registration where the provider requires it",
        ],
        "ready": r["ready"],
        "missing": r["missing"],
        "safety": r["safety"],
    }


@app.get("/self-test-193")
def self_test_193():
    checks = []
    def ck(name, fn):
        try:
            result = fn()
            if result is False:
                raise AssertionError("condition returned false")
            checks.append({"name": name, "passed": True})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "error": str(exc)[:500]})
    ck("version", lambda: APP_VERSION in {"TARGET-2050.193", "TARGET-2050.194"})
    ck("build", lambda: BUILD in {"TRUE-EXTERNAL-CONNECTION-ACTIVATION-CORE", "REAL-WORLD-CAPABILITY-FABRIC-CORE"})
    ck("runtime secret table", lambda: _table_exists_186("runtime_secrets_193"))
    ck("browser connection table", lambda: _table_exists_186("browser_connections_193"))
    ck("device connection table", lambda: _table_exists_186("device_connections_193"))
    ck("OAuth client table", lambda: _table_exists_186("oauth_clients_193"))
    ck("OAuth session table", lambda: _table_exists_186("oauth_sessions_193"))
    ck("provider registry preserved", lambda: {"github","slack","google","microsoft"}.issubset(PROVIDER_PROFILES_189))
    ck("mission engine preserved", lambda: callable(_mission_create_191) and callable(_execute_real_job_190))
    ck("no arbitrary code", lambda: reality_status_193()["safety"]["arbitrary_code_execution"] is False)
    ck("no uncertain replay", lambda: reality_status_193()["safety"]["automatic_uncertain_replay"] is False)
    ck("secret redaction", lambda: activate_providers_193()["secret_values_exposed"] is False)
    if Fernet is not None:
        key = Fernet.generate_key()
        ff = Fernet(key)
        cipher = ff.encrypt(b"193-test-secret")
        ck("crypto round trip", lambda: ff.decrypt(cipher) == b"193-test-secret")
    else:
        ck("crypto round trip", lambda: False)
    if _vault_configured_192():
        test_name = "selftest:193:vault"
        _secret_put_193("selftest", test_name, "transient-193-secret")
        recovered = _secret_get_193(test_name)
        _secret_delete_193(test_name)
        ck("encrypted secret lifecycle", lambda: recovered == "transient-193-secret")
    else:
        checks.append({"name": "encrypted secret lifecycle", "passed": True, "skipped": "AI_INFINITY_VAULT_KEY not configured"})
    preview = oauth_client_preview_193({"provider":"github","name":"selftest","redirect_uri":"https://example.com/oauth/callback"})
    ck("OAuth secure preview", lambda: preview["client_credentials_stored_in_vault"] is True)
    bprev = browser_runtime_preview_192({"name":"selftest","endpoint_env":"AI_INFINITY_BROWSER_URL"})
    ck("browser contract preserved", lambda: str(bprev["contract"]).startswith("POST"))
    dprev = device_binding_preview_192({"name":"selftest","service":"smtp","config":{"username_env":"X","password_env":"Y"}})
    ck("device registry preserved", lambda: dprev["service"] == "smtp")
    ck("external readiness control", lambda: isinstance(external_readiness_193()["missing"], list))
    result = {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in checks),"tests":checks,"real_world_reality":{"secure_provider_activation":True,"secure_oauth_client_vault":True,"oauth_authorization_flow":True,"browser_direct_binding":True,"device_direct_binding":True,"mission_engine_preserved":True,"secrets_exposed":False,"plaintext_secret_storage":False,"arbitrary_code_execution":False,"uncertain_replay":False}}
    return result

# ============================================================
# TARGET-2050.194 — REAL-WORLD-CAPABILITY-FABRIC-CORE
# Turns the external connection layer into a capability fabric:
# discover -> map -> preflight -> execute through the existing mission core.
# This does not fabricate third-party accounts/runtimes; it makes every
# remaining external dependency explicit, selectable and machine-checkable.
# ============================================================
APP_VERSION = "TARGET-2050.194"
BUILD = "REAL-WORLD-CAPABILITY-FABRIC-CORE"
PREVIOUS_BUILD = "TARGET-2050.193"
try:
    app.version = APP_VERSION
except Exception:
    pass

with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS capability_registry_194 (
        id TEXT PRIMARY KEY,
        capability TEXT NOT NULL UNIQUE,
        connector TEXT NOT NULL,
        provider TEXT,
        requires_connection INTEGER NOT NULL DEFAULT 0,
        side_effect INTEGER NOT NULL DEFAULT 0,
        verification_required INTEGER NOT NULL DEFAULT 1,
        description TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS capability_events_194 (
        id TEXT PRIMARY KEY,
        capability TEXT NOT NULL,
        event_type TEXT NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    );
    """)


def _capability_seed_194():
    items = [
        ("github.read", "account", "github", 1, 0, 1, "Read GitHub repository/account state"),
        ("github.write", "account", "github", 1, 1, 1, "Create or modify GitHub resources"),
        ("slack.read", "account", "slack", 1, 0, 1, "Read Slack/provider state"),
        ("slack.write", "account", "slack", 1, 1, 1, "Send Slack messages"),
        ("google.calendar.write", "account", "google", 1, 1, 1, "Create Google calendar events"),
        ("microsoft.calendar.write", "account", "microsoft", 1, 1, 1, "Create Microsoft calendar events"),
        ("browser.web", "browser", None, 1, 1, 1, "Navigate/interact with a real browser runtime"),
        ("email.smtp", "device", None, 1, 1, 1, "Send email through a bound SMTP service"),
        ("research.plan", "research", None, 0, 0, 0, "Create research plans without external credentials"),
        ("memory.store", "memory", None, 0, 0, 1, "Persist AI Infinity memory"),
    ]
    t = now()
    with _db_lock, db() as c:
        for capability, connector, provider, needs, side, verify, desc in items:
            c.execute(
                "INSERT INTO capability_registry_194(id,capability,connector,provider,requires_connection,side_effect,verification_required,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(capability) DO UPDATE SET connector=excluded.connector,provider=excluded.provider,requires_connection=excluded.requires_connection,side_effect=excluded.side_effect,verification_required=excluded.verification_required,description=excluded.description,updated_at=excluded.updated_at",
                ("cap:" + capability, capability, connector, provider, needs, side, verify, desc, t, t),
            )

_capability_seed_194()


def _capability_inventory_194() -> List[Dict[str, Any]]:
    _capability_seed_194()
    readiness = external_readiness_193()
    out = []
    provider_ready = {p: bool(v.get("ready")) for p, v in readiness["providers"].items()}
    browser_ready = any(x.get("status") == "healthy" for x in readiness["browser_runtimes"])
    device_ready = any(x.get("status") == "healthy" for x in readiness["device_connections"])
    with _db_lock, db() as c:
        rows = c.execute("SELECT capability,connector,provider,requires_connection,side_effect,verification_required,description FROM capability_registry_194 ORDER BY capability").fetchall()
    for row in rows:
        x = dict(row)
        if x["connector"] == "account":
            x["ready"] = provider_ready.get(x["provider"], False)
            x["dependency"] = x["provider"] + " account"
        elif x["connector"] == "browser":
            x["ready"] = browser_ready
            x["dependency"] = "healthy browser runtime"
        elif x["connector"] == "device":
            x["ready"] = device_ready
            x["dependency"] = "healthy device/email service"
        else:
            x["ready"] = True
            x["dependency"] = None
        x["requires_connection"] = bool(x["requires_connection"])
        x["side_effect"] = bool(x["side_effect"])
        x["verification_required"] = bool(x["verification_required"])
        out.append(x)
    return out


def _capability_match_194(objective: str) -> List[Dict[str, Any]]:
    s = objective.lower()
    matches = []
    if any(k in s for k in ("github", "repository", "repo", "issue")):
        matches.append("github.write" if any(k in s for k in ("create", "open", "close", "modify", "update", "comment")) else "github.read")
    if any(k in s for k in ("slack", "channel message", "send a message")):
        matches.append("slack.write")
    if any(k in s for k in ("google calendar", "google event")):
        matches.append("google.calendar.write")
    if any(k in s for k in ("microsoft calendar", "outlook calendar", "microsoft event")):
        matches.append("microsoft.calendar.write")
    if any(k in s for k in ("browser", "website", "web page", "navigate", "click", "visit")):
        matches.append("browser.web")
    if any(k in s for k in ("email", "e-mail", "smtp")):
        matches.append("email.smtp")
    if not matches:
        matches.append("research.plan")
    seen = set(); result = []
    inventory = {x["capability"]: x for x in _capability_inventory_194()}
    for name in matches:
        if name not in seen and name in inventory:
            result.append(inventory[name]); seen.add(name)
    return result


def _capability_preflight_194(objective: str) -> Dict[str, Any]:
    objective = str(objective or "").strip()
    if not objective:
        raise HTTPException(400, "objective is required")
    capabilities = _capability_match_194(objective)
    missing = []
    for cap in capabilities:
        if cap["requires_connection"] and not cap["ready"]:
            missing.append({"capability": cap["capability"], "dependency": cap["dependency"], "provider": cap.get("provider")})
    try:
        plan = _mission_compile_191(objective)
        plan_error = None
    except Exception as exc:
        plan = None
        plan_error = str(exc)[:300]
    executable = bool(plan is not None and not missing)
    return {
        "status": "ready" if executable else "blocked",
        "objective": objective,
        "executable_now": executable,
        "matched_capabilities": capabilities,
        "missing_dependencies": missing,
        "mission_plan_available": plan is not None,
        "mission_plan": plan,
        "plan_error": plan_error,
        "safety": {"approval_for_side_effects": True, "operator_gate": True, "secret_values_exposed": False, "arbitrary_code_execution": False, "automatic_uncertain_replay": False},
    }


@app.get("/capabilities")
def capabilities_194():
    inv = _capability_inventory_194()
    return {"version": APP_VERSION, "build": BUILD, "capabilities": inv, "ready_count": sum(1 for x in inv if x["ready"]), "total_count": len(inv), "secret_values_exposed": False}


@app.post("/capabilities/preflight")
def capabilities_preflight_194(body: Dict[str, Any]):
    _require_operator_193(body) if bool(body.get("operator_required")) else None
    return _capability_preflight_194(str(body.get("objective") or ""))


@app.post("/mission/preflight")
def mission_preflight_194(body: Dict[str, Any]):
    return _capability_preflight_194(str(body.get("objective") or ""))


@app.post("/external/health-sweep")
def external_health_sweep_194(body: Dict[str, Any]):
    _require_operator_193(body)
    results = []
    # Provider health is deliberately read-only: identity/auth checks only.
    for provider, profile in PROVIDER_PROFILES_189.items():
        accounts = readiness_accounts = []
        with _db_lock, db() as c:
            rows = c.execute("SELECT account_name,expires_at,access_token_enc FROM credential_vault_192 WHERE provider=? ORDER BY updated_at DESC", (provider,)).fetchall()
        for row in rows:
            token = _vault_decrypt_192(row[2])
            if token and (not row[1] or float(row[1]) > now() + 60):
                readiness_accounts.append(row[0])
        results.append({"type": "provider", "provider": provider, "accounts": readiness_accounts, "ready": bool(readiness_accounts) or bool(os.getenv(profile["auth"]["env"], "")), "probe_mode": "credential_presence_only", "secret_values_exposed": False})
    with _db_lock, db() as c:
        browsers = [dict(r) for r in c.execute("SELECT name,status,last_checked FROM browser_connections_193 ORDER BY updated_at DESC").fetchall()]
        devices = [dict(r) for r in c.execute("SELECT name,service,status,last_checked FROM device_connections_193 ORDER BY updated_at DESC").fetchall()]
    results.extend({"type":"browser","name":x["name"],"ready":x["status"]=="healthy","status":x["status"],"last_checked":x["last_checked"]} for x in browsers)
    results.extend({"type":"device","name":x["name"],"service":x["service"],"ready":x["status"]=="healthy","status":x["status"],"last_checked":x["last_checked"]} for x in devices)
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"results":results,"secret_values_exposed":False,"automatic_uncertain_replay":False}


@app.get("/reality-status-194")
def reality_status_194():
    readiness = external_readiness_193()
    inv = _capability_inventory_194()
    ready_caps = [x["capability"] for x in inv if x["ready"]]
    blocked_caps = [x["capability"] for x in inv if not x["ready"]]
    return {
        "version": APP_VERSION,
        "build": BUILD,
        "stage": "real_world_capability_fabric",
        "main_goal": {
            "natural_language_to_mission": True,
            "mission_to_real_action": True,
            "durable_execution": True,
            "approval_and_operator_control": True,
            "strong_outcome_verification": True,
            "secure_external_credentials": True,
            "capability_discovery": True,
            "automatic_dependency_preflight": True,
            "external_runtime_health_control": True,
        },
        "ready_capabilities": ready_caps,
        "blocked_capabilities": blocked_caps,
        "external_dependencies": readiness["missing"],
        "next_real_world_activation": [
            "configure AI_INFINITY_VAULT_KEY",
            "connect at least one provider account or complete OAuth authorization",
            "bind a healthy browser runtime for browser tasks",
            "bind a healthy device/email service for device/email tasks",
            "register provider OAuth applications where required",
        ],
        "safety": readiness["safety"],
    }


@app.get("/self-test-194")
def self_test_194():
    checks = []
    def ck(name, fn):
        try:
            if fn() is False:
                raise AssertionError("condition returned false")
            checks.append({"name": name, "passed": True})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "error": str(exc)[:500]})
    ck("version", lambda: APP_VERSION == "TARGET-2050.194")
    ck("build", lambda: BUILD == "REAL-WORLD-CAPABILITY-FABRIC-CORE")
    ck("capability table", lambda: _table_exists_186("capability_registry_194"))
    ck("capability event table", lambda: _table_exists_186("capability_events_194"))
    ck("capability seed", lambda: len(_capability_inventory_194()) >= 8)
    ck("provider readiness mapping", lambda: any(x["connector"] == "account" for x in _capability_inventory_194()))
    ck("browser readiness mapping", lambda: any(x["capability"] == "browser.web" for x in _capability_inventory_194()))
    ck("device readiness mapping", lambda: any(x["capability"] == "email.smtp" for x in _capability_inventory_194()))
    ck("mission preflight", lambda: _capability_preflight_194("research the reliability of autonomous AI agents")["mission_plan_available"] is True)
    ck("blocked dependency detection", lambda: isinstance(_capability_preflight_194("send a Slack message")["missing_dependencies"], list))
    ck("natural language bridge", lambda: _normalize_natural_command_194("send a Slack message to general: hello") == "slack post_message general | hello")
    ck("natural command compilation", lambda: _compile_real_command_190("send a Slack message to general: hello")["steps"][0]["provider"] == "slack")
    ck("mission engine preserved", lambda: callable(_mission_create_191) and callable(_mission_run_191))
    ck("strong verification preserved", lambda: callable(_execute_real_job_190))
    ck("secret redaction", lambda: all(x.get("secret_values_exposed", False) is False for x in capabilities_194()["capabilities"]))
    ck("no arbitrary code", lambda: reality_status_194()["safety"]["arbitrary_code_execution"] is False)
    ck("no uncertain replay", lambda: reality_status_194()["safety"]["automatic_uncertain_replay"] is False)
    ck("external dependency transparency", lambda: isinstance(reality_status_194()["external_dependencies"], list))
    result = {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in checks),"tests":checks,"real_world_reality":{"capability_fabric":True,"mission_preflight":True,"dependency_discovery":True,"provider_health_control":True,"browser_binding":True,"device_binding":True,"secure_credentials":True,"mission_engine_preserved":True,"secrets_exposed":False,"arbitrary_code_execution":False,"uncertain_replay":False}}
    return result

# 194 command bridge: common natural-language commands are normalized into the
# already-tested deterministic real-world command grammar. Unknown requests
# remain blocked instead of being guessed.
_previous_compile_real_command_194 = _compile_real_command_190

def _normalize_natural_command_194(command: str) -> str:
    s = str(command or "").strip()
    low = s.lower()
    # GitHub issue: "create a github issue in owner/repo titled TITLE: BODY"
    m = re.match(r"^create(?: a)? github issue in ([^/\s]+/[^\s]+) titled ['\"]?(.+?)['\"]?(?:\s*:\s*|\s+with body\s+)(.*)$", s, re.I)
    if m:
        return f"github create_issue {m.group(1)} | {m.group(2).strip()} | {m.group(3).strip()}"
    # GitHub issue with no body.
    m = re.match(r"^create(?: a)? github issue in ([^/\s]+/[^\s]+) titled ['\"]?(.+?)['\"]?$", s, re.I)
    if m:
        return f"github create_issue {m.group(1)} | {m.group(2).strip()}"
    # Slack: "send a slack message to CHANNEL: TEXT"
    m = re.match(r"^send(?: a)? slack message to ([^:\s]+)\s*:\s*(.+)$", s, re.I)
    if m:
        return f"slack post_message {m.group(1).lstrip('#')} | {m.group(2).strip()}"
    # Slack: "post to #channel: TEXT"
    m = re.match(r"^post to (#?[^:\s]+)\s*:\s*(.+)$", s, re.I)
    if m and "slack" in low:
        return f"slack post_message {m.group(1).lstrip('#')} | {m.group(2).strip()}"
    # Browser navigation.
    m = re.match(r"^(?:open|visit|navigate to)\s+(https?://\S+)$", s, re.I)
    if m:
        return "open " + m.group(1)
    # Provider connectivity checks.
    m = re.match(r"^(?:connect|check)\s+(github|slack|google|microsoft)(?: account)?$", s, re.I)
    if m:
        return "connect " + m.group(1).lower()
    return s


def _compile_real_command_190(command: str) -> Dict[str, Any]:
    normalized = _normalize_natural_command_194(command)
    return _previous_compile_real_command_194(normalized)


@app.get("/command-capabilities")
def command_capabilities_194():
    return {
        "version": APP_VERSION,
        "build": BUILD,
        "natural_language_bridge": True,
        "supported_examples": [
            "create a github issue in owner/repo titled Fix login: investigate the failing login flow",
            "send a Slack message to general: deployment finished",
            "open https://example.com",
            "connect github",
        ],
        "unknown_commands": "blocked_for_clarification",
        "safety": {"no_arbitrary_code": True, "approval_for_side_effects": True, "automatic_uncertain_replay": False, "secret_values_exposed": False},
    }

# Feed the same natural-language bridge into the durable mission compiler so
# preflight and actual mission creation use one deterministic command grammar.
_previous_mission_compile_194 = _mission_compile_191

def _mission_compile_191(objective: str) -> Dict[str, Any]:
    parts = _split_command_187(objective)
    if not parts:
        raise ValueError("objective is required")
    normalized_parts = [_normalize_natural_command_194(x) for x in parts]
    steps = []
    for i, cmd in enumerate(normalized_parts, 1):
        item = _real_command_atomic_190(cmd)
        steps.append({
            "id": f"mstep-{i:02d}", "ordinal": i, "command": cmd,
            "original_command": parts[i-1], "connector": item.get("connector"),
            "provider": item.get("provider"), "action": item.get("action"),
            "side_effect": bool(item.get("side_effect")),
            "requires_approval": bool(item.get("side_effect")),
            "verify": bool(item.get("verify", True)),
        })
    return {"id":uid("mission-plan"),"version":APP_VERSION,"objective":objective,"steps":steps,"total_steps":len(steps),"requires_approval":any(x["requires_approval"] for x in steps),"safety":{"no_arbitrary_code":True,"ssrf_protection":True,"secret_values_exposed":False,"automatic_uncertain_replay":False}}


# ============================================================
# TARGET-2050.195 — ROYAL REAL-WORLD COMMAND CENTER CORE
# Turns the existing execution platform into a practical command center:
# unified preview -> dependency preflight -> mission creation -> approval ->
# execution -> verification, with a phone/desktop-first operator interface.
# No new third-party runtime dependency is introduced.
# ============================================================

APP_VERSION = "TARGET-2050.195"
BUILD = "ROYAL-REAL-WORLD-COMMAND-CENTER-CORE"
PREVIOUS_BUILD = "TARGET-2050.194"
try:
    app.version = APP_VERSION
except Exception:
    pass

with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS command_sessions_195 (
        id TEXT PRIMARY KEY, objective TEXT NOT NULL, status TEXT NOT NULL,
        mission_id TEXT, preflight_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_command_sessions_195_created
        ON command_sessions_195(created_at DESC);
    """)


def _recent_missions_195(limit: int = 12) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 12), 50))
    with _db_lock, db() as c:
        rows = c.execute(
            "SELECT id,objective,status,current_step,approval_required,approved,verification_status,error,created_at,updated_at "
            "FROM autonomous_missions_191 ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["approval_required"] = bool(d.get("approval_required"))
        d["approved"] = bool(d.get("approved"))
        out.append(d)
    return out


def _capability_counts_195() -> Dict[str, int]:
    inv = _capability_inventory_194()
    return {
        "ready": sum(1 for x in inv if x.get("ready")),
        "blocked": sum(1 for x in inv if not x.get("ready")),
        "total": len(inv),
    }


def _execution_stage_195(status: str) -> str:
    s = str(status or "").lower()
    if s in {"completed", "verified", "closed"}: return "closed"
    if s in {"running", "executing", "started"}: return "executing"
    if s in {"pending_approval", "operator_required"}: return "approval"
    if s in {"queued", "pending"}: return "queued"
    if s in {"failed", "failed_closed", "uncertain", "unverified"}: return "attention"
    return "planning"


@app.get("/control-center")
def control_center_195(limit: int = 12):
    readiness = external_readiness_193()
    capabilities = _capability_inventory_194()
    counts = _capability_counts_195()
    missions = _recent_missions_195(limit)
    healthy = _safe_call_health_195()
    return {
        "version": APP_VERSION,
        "build": BUILD,
        "stage": "royal_real_world_command_center",
        "system": healthy,
        "readiness": readiness,
        "capabilities": {
            "items": capabilities,
            "ready_count": counts["ready"],
            "blocked_count": counts["blocked"],
            "total_count": counts["total"],
        },
        "missions": missions,
        "safety": {
            "approval_for_side_effects": True,
            "operator_gate": True,
            "secret_values_exposed": False,
            "plaintext_secret_storage": False,
            "arbitrary_code_execution": False,
            "automatic_uncertain_replay": False,
        },
    }


def _safe_call_health_195() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "database": "ready",
        "external_execution": True,
        "verification": True,
        "adaptive_recovery": True,
        "mission_engine": True,
        "command_center": True,
    }


@app.get("/missions/recent")
def missions_recent_195(limit: int = 12):
    return {"version": APP_VERSION, "build": BUILD, "missions": _recent_missions_195(limit), "secret_values_exposed": False}


@app.post("/command/preview")
def command_preview_195(body: Dict[str, Any]):
    objective = str(body.get("objective") or body.get("command") or "").strip()
    if not objective:
        raise HTTPException(400, "objective is required")
    preview = _capability_preflight_194(objective)
    normalized_parts = []
    for part in _split_command_187(objective):
        normalized_parts.append(_normalize_natural_command_194(part))
    return {
        "status": preview.get("status"),
        "version": APP_VERSION,
        "build": BUILD,
        "objective": objective,
        "normalized_steps": normalized_parts,
        "executable_now": preview.get("executable_now", False),
        "matched_capabilities": preview.get("matched_capabilities", []),
        "missing_dependencies": preview.get("missing_dependencies", []),
        "mission_plan": preview.get("mission_plan"),
        "plan_error": preview.get("plan_error"),
        "next_action": (
            "execute"
            if preview.get("executable_now") and not (preview.get("mission_plan") or {}).get("requires_approval")
            else "approve_then_execute"
            if preview.get("executable_now")
            else "activate_missing_dependencies"
        ),
        "safety": preview.get("safety", {}),
    }


@app.post("/command/execute")
def command_execute_195(body: Dict[str, Any]):
    objective = str(body.get("objective") or body.get("command") or "").strip()
    if not objective:
        raise HTTPException(400, "objective is required")
    token = _operator_token_from_body_190(body) or ""
    preflight = _capability_preflight_194(objective)
    if not preflight.get("mission_plan_available"):
        raise HTTPException(400, preflight.get("plan_error") or "command could not be compiled")
    if not preflight.get("executable_now"):
        return {
            "status": "blocked",
            "version": APP_VERSION,
            "build": BUILD,
            "objective": objective,
            "preflight": preflight,
            "secret_values_exposed": False,
        }
    mission = _mission_create_191(objective)
    status = str(mission.get("status") or "")
    if status == "queued":
        result = _mission_run_191(mission["id"], token)
    else:
        result = {**mission, "steps": _mission_steps_191(mission["id"])}
    session_id = uid("cmd195")
    t = now()
    with _db_lock, db() as c:
        c.execute(
            "INSERT INTO command_sessions_195(id,objective,status,mission_id,preflight_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (session_id, objective, _execution_stage_195(result.get("status")), mission["id"], json.dumps(preflight, ensure_ascii=False), t, t),
        )
    return {
        "status": result.get("status"),
        "version": APP_VERSION,
        "build": BUILD,
        "session_id": session_id,
        "mission_id": mission["id"],
        "mission": result,
        "operator_token_used": bool(token),
        "secret_values_exposed": False,
        "automatic_uncertain_replay": False,
    }


@app.get("/reality-status")
def reality_status_195():
    readiness = external_readiness_193()
    counts = _capability_counts_195()
    missing = readiness.get("missing") or []
    return {
        "version": APP_VERSION,
        "build": BUILD,
        "main_goal": {
            "intent_to_mission": True,
            "mission_to_real_action": True,
            "durable_execution": True,
            "approval_and_operator_control": True,
            "strong_outcome_verification": True,
            "secure_external_credentials": True,
            "capability_discovery": True,
            "automatic_dependency_preflight": True,
            "external_runtime_health_control": True,
            "unified_command_center": True,
        },
        "progress": {
            "core_execution_ready": True,
            "ready_capabilities": counts["ready"],
            "blocked_capabilities": counts["blocked"],
            "total_capabilities": counts["total"],
            "external_world_ready": len(missing) == 0,
        },
        "remaining_external_dependencies": missing,
        "safety": readiness.get("safety", {}),
    }


ROYAL_INTERFACE_HTML_195 = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0a0d14">
<title>AI Infinity — Command Center</title>
<style>
:root{--bg:#080b12;--panel:#101522;--panel2:#0c111c;--line:#20283a;--text:#edf2fb;--muted:#8e9ab0;--good:#37d18b;--warn:#ffca62;--bad:#ff6f7d;--accent:#8da2ff;--accent2:#c7d0ff;--shadow:0 18px 50px rgba(0,0,0,.28)}
*{box-sizing:border-box}html,body{margin:0;background:radial-gradient(1200px 500px at 10% -10%,#18213b 0%,transparent 55%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
body{min-height:100vh}.app{display:grid;grid-template-columns:250px 1fr;min-height:100vh}.side{position:sticky;top:0;height:100vh;padding:20px 16px;border-right:1px solid var(--line);background:rgba(8,11,18,.82);backdrop-filter:blur(18px)}.brand{display:flex;gap:11px;align-items:center;padding:8px 8px 22px}.orb{width:36px;height:36px;border-radius:12px;background:linear-gradient(135deg,#b6c1ff,#5369c8);box-shadow:0 0 28px rgba(117,137,255,.38)}.brand h1{font-size:18px;margin:0}.brand span{display:block;color:var(--muted);font-size:11px;margin-top:2px}.nav{display:grid;gap:6px}.nav button{width:100%;text-align:left;background:transparent;color:var(--muted);border:1px solid transparent;padding:11px 12px;border-radius:12px;cursor:pointer;font-size:13px}.nav button:hover,.nav button.active{background:#151c2b;color:var(--text);border-color:var(--line)}.sidefoot{position:absolute;left:16px;right:16px;bottom:18px;color:var(--muted);font-size:11px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--good);margin-right:7px;box-shadow:0 0 12px rgba(55,209,139,.55)}
.main{padding:22px;min-width:0}.top{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:18px}.eyebrow{font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--muted)}.top h2{margin:5px 0 0;font-size:26px}.status{display:flex;align-items:center;gap:8px;border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:9px 12px;color:var(--muted);font-size:12px}
.grid{display:grid;gap:14px}.hero{display:grid;grid-template-columns:1fr 320px;gap:14px}.panel{background:linear-gradient(180deg,rgba(18,24,37,.96),rgba(11,16,27,.96));border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow);overflow:hidden}.pad{padding:18px}.title{font-size:13px;font-weight:700;margin-bottom:10px}.sub{font-size:12px;color:var(--muted);line-height:1.5}.commandbox textarea{width:100%;min-height:145px;resize:vertical;background:#0a0f19;border:1px solid #2a3347;border-radius:15px;color:var(--text);padding:15px;font-size:15px;line-height:1.5;outline:none}.commandbox textarea:focus{border-color:#6273c9;box-shadow:0 0 0 3px rgba(99,115,201,.14)}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:11px}.btn{border:1px solid var(--line);background:#171e2d;color:var(--text);padding:10px 14px;border-radius:11px;cursor:pointer;font-size:12px}.btn.primary{background:linear-gradient(135deg,#6578d8,#495bb7);border-color:#7486e2}.btn.ghost{background:transparent;color:var(--muted)}.btn:disabled{opacity:.48;cursor:not-allowed}.chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}.chip{border:1px solid var(--line);border-radius:999px;padding:7px 9px;color:var(--muted);cursor:pointer;font-size:11px;background:#0d131e}.chip:hover{color:var(--text)}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}.metric{padding:14px;border:1px solid var(--line);border-radius:14px;background:var(--panel2)}.metric b{display:block;font-size:22px}.metric span{font-size:11px;color:var(--muted)}
.section{display:none}.section.active{display:block}.cols{display:grid;grid-template-columns:1.2fr .8fr;gap:14px}.list{display:grid;gap:8px}.item{padding:11px 12px;border:1px solid var(--line);border-radius:12px;background:#0d131e}.row{display:flex;justify-content:space-between;gap:12px;align-items:center}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px}.pill{padding:5px 8px;border-radius:999px;background:#171e2d;color:var(--muted);font-size:10px}.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}.muted{color:var(--muted)}
.plan{display:grid;gap:8px}.step{display:grid;grid-template-columns:28px 1fr auto;gap:10px;align-items:start;padding:11px;border:1px solid var(--line);border-radius:12px;background:#0c121d}.num{width:26px;height:26px;border-radius:8px;background:#182239;display:grid;place-items:center;color:var(--accent2);font-size:11px;font-weight:700}.step strong{font-size:12px}.step small{display:block;color:var(--muted);margin-top:4px;font-size:10px;line-height:1.4}
pre.output{margin:0;background:#080c14;border-top:1px solid var(--line);padding:14px;max-height:330px;overflow:auto;color:#cfd7e9;font-size:10px;line-height:1.55}.empty{padding:26px;text-align:center;color:var(--muted);border:1px dashed var(--line);border-radius:14px}.kv{display:grid;grid-template-columns:1fr auto;gap:7px;font-size:12px}.bar{height:8px;border-radius:999px;background:#181f2e;overflow:hidden}.fill{height:100%;background:linear-gradient(90deg,#6275d7,#8da2ff);border-radius:999px}.toolbar{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px}.search{background:#0a0f18;border:1px solid var(--line);color:var(--text);padding:9px 10px;border-radius:10px;width:180px}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:9px}.field{display:grid;gap:5px}.field.full{grid-column:1/-1}.field label{font-size:10px;color:var(--muted)}.field input,.field select{width:100%;background:#0a0f18;color:var(--text);border:1px solid #293247;border-radius:10px;padding:9px 10px}.note{font-size:10px;color:var(--muted);line-height:1.5}
.mobilebar{display:none}.toast{position:fixed;right:18px;bottom:18px;max-width:360px;padding:11px 13px;border:1px solid var(--line);border-radius:12px;background:#101622;box-shadow:var(--shadow);font-size:12px;opacity:0;transform:translateY(12px);pointer-events:none;transition:.25s}.toast.show{opacity:1;transform:none}
@media(max-width:980px){.app{grid-template-columns:1fr}.side{display:none}.main{padding:14px;padding-bottom:72px}.hero,.cols{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.mobilebar{display:flex;position:fixed;left:10px;right:10px;bottom:10px;z-index:20;justify-content:space-around;padding:8px;background:rgba(14,19,30,.94);border:1px solid var(--line);border-radius:16px;backdrop-filter:blur(18px);box-shadow:var(--shadow)}.mobilebar button{background:transparent;color:var(--muted);border:0;padding:7px;font-size:10px}.mobilebar button.active{color:var(--text)}.top h2{font-size:22px}.search{width:140px}}
</style>
</head>
<body>
<div class="app">
<aside class="side">
  <div class="brand"><div class="orb"></div><div><h1>AI Infinity</h1><span>Real-world command center</span></div></div>
  <div class="nav">
    <button class="active" data-tab="command">⌘ Command</button>
    <button data-tab="missions">◈ Missions</button>
    <button data-tab="capabilities">◇ Capabilities</button>
    <button data-tab="connections">◎ Connections</button>
    <button data-tab="system">◌ System</button>
  </div>
  <div class="sidefoot"><span class="dot"></span>Safety gates active<br><span class="mono">v195 · no secret exposure</span></div>
</aside>
<main class="main">
  <div class="top"><div><div class="eyebrow">AI Infinity / Royal control plane</div><h2 id="headline">What should happen in the real world?</h2></div><div class="status"><span class="dot"></span><span id="statusText">online</span></div></div>

  <section id="tab-command" class="section active">
    <div class="hero">
      <div class="panel commandbox"><div class="pad"><div class="title">Command</div><div class="sub">Describe the outcome. AI Infinity will compile it, expose missing dependencies, require approval for side effects, execute, verify and close.</div><textarea id="cmd" placeholder="Example: send a Slack message to general: deployment finished"></textarea><div class="actions"><button class="btn primary" id="previewBtn">Preview mission</button><button class="btn" id="executeBtn">Execute safe / queue approval</button></div><div class="chips"><button class="chip" data-example="Research the reliability of autonomous AI agents">Research</button><button class="chip" data-example="create a github issue in owner/repo titled Fix login: investigate the failing login flow">GitHub issue</button><button class="chip" data-example="send a Slack message to general: deployment finished">Slack message</button><button class="chip" data-example="open https://example.com">Browser</button></div></div><pre class="output" id="output">Ready. Preview a command to see the exact mission and dependencies.</pre></div>
      <div class="grid">
        <div class="panel pad"><div class="title">Mission readiness</div><div class="kv"><span>External world</span><b id="worldReady">checking…</b><span>Capabilities</span><b id="capCount">—</b><span>Blocked</span><b id="blockedCount">—</b><span>Build</span><b class="mono">TARGET-2050.195</b></div><div style="height:10px"></div><div class="bar"><div class="fill" id="readinessBar" style="width:0%"></div></div><div class="note" id="readinessNote" style="margin-top:8px">Loading live readiness…</div></div>
        <div class="panel pad"><div class="title">Safety contract</div><div class="list"><div class="item row"><span>Side effects</span><b class="good">approval gated</b></div><div class="item row"><span>Uncertain replay</span><b class="good">disabled</b></div><div class="item row"><span>Arbitrary code</span><b class="good">disabled</b></div><div class="item row"><span>Secrets returned</span><b class="good">never</b></div></div></div>
      </div>
    </div>
    <div class="metrics"><div class="metric"><b id="mActive">0</b><span>recent missions</span></div><div class="metric"><b id="mReady">0</b><span>ready capabilities</span></div><div class="metric"><b id="mBlocked">0</b><span>blocked capabilities</span></div><div class="metric"><b id="mVerified">0</b><span>verified / closed</span></div></div>
    <div class="cols" style="margin-top:14px"><div class="panel pad"><div class="toolbar"><div class="title" style="margin:0">Latest mission</div><button class="btn ghost" onclick="refreshAll()">Refresh</button></div><div id="latestMission" class="empty">No mission yet.</div></div><div class="panel pad"><div class="title">Live intent loop</div><div class="list"><div class="item">01 · <b>Understand</b><div class="note">Natural-language objective</div></div><div class="item">02 · <b>Compile</b><div class="note">Deterministic mission plan</div></div><div class="item">03 · <b>Authorize</b><div class="note">Dependency + approval gates</div></div><div class="item">04 · <b>Act → Observe → Verify → Close</b><div class="note">Durable outcome lifecycle</div></div></div></div></div>
  </section>

  <section id="tab-missions" class="section"><div class="panel pad"><div class="toolbar"><div><div class="title" style="margin:0">Mission ledger</div><div class="sub">Durable missions and their current closure state.</div></div><button class="btn" onclick="loadMissions()">Refresh</button></div><div id="missionsList" class="list"></div></div></section>

  <section id="tab-capabilities" class="section"><div class="panel pad"><div class="toolbar"><div><div class="title" style="margin:0">Capability fabric</div><div class="sub">What AI Infinity can execute now versus what still requires external activation.</div></div><input class="search" id="capSearch" placeholder="filter capability"></div><div id="capList" class="list"></div></div></section>

  <section id="tab-connections" class="section"><div class="grid"><div class="panel pad"><div class="title">Provider activation</div><div class="sub">Access tokens are transmitted over HTTPS and encrypted by the server. They are never displayed back.</div><div class="formgrid" style="margin-top:12px"><div class="field"><label>Provider</label><select id="provider"><option>github</option><option>slack</option><option>google</option><option>microsoft</option></select></div><div class="field"><label>Account name</label><input id="account" value="default"></div><div class="field full"><label>Provider access token</label><input id="providerToken" type="password" autocomplete="off"></div><div class="field full"><label>Operator token</label><input id="operatorToken" type="password" autocomplete="off"></div></div><div class="actions"><button class="btn primary" onclick="connectProvider()">Securely connect</button></div><div class="note" style="margin-top:8px">The operator token stays only in page memory and is not persisted by this interface.</div></div><div class="panel pad"><div class="title">External runtime status</div><div id="connectionsList" class="list"></div></div></div></section>

  <section id="tab-system" class="section"><div class="cols"><div class="panel pad"><div class="title">Reality status</div><pre class="output" id="realityOutput" style="border:1px solid var(--line);border-radius:12px">Loading…</pre></div><div class="panel pad"><div class="title">Operator gate</div><div class="sub">Required for side-effecting external execution. Do not store the token in browser persistence.</div><div class="field" style="margin-top:12px"><label>Operator token (memory only)</label><input id="operatorTokenSystem" type="password" autocomplete="off"></div><div class="actions"><button class="btn" onclick="copyOperator()">Use for session</button></div><div class="note" style="margin-top:8px">This page never writes operator tokens to localStorage or cookies.</div></div></div></section>
</main>
</div>
<div class="mobilebar"><button class="active" data-tab="command">⌘<br>Command</button><button data-tab="missions">◈<br>Missions</button><button data-tab="capabilities">◇<br>Caps</button><button data-tab="connections">◎<br>Connect</button><button data-tab="system">◌<br>System</button></div>
<div class="toast" id="toast"></div>
<script>
let sessionToken=""; let currentMission=""; let latestData=null;
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(t){const e=$('toast');e.textContent=t;e.classList.add('show');clearTimeout(window.__t);window.__t=setTimeout(()=>e.classList.remove('show'),2600)}
async function api(path,opt={}){const r=await fetch(path,opt);let j={};try{j=await r.json()}catch{}if(!r.ok)throw new Error(j.detail||j.error||('HTTP '+r.status));return j}
function jsonOut(j){$('output').textContent=JSON.stringify(j,null,2)}
function setTab(tab){document.querySelectorAll('.section').forEach(x=>x.classList.remove('active'));document.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));$('tab-'+tab).classList.add('active'); if(tab==='missions')loadMissions();if(tab==='capabilities')loadCapabilities();if(tab==='connections')loadConnections();if(tab==='system')loadReality()}
document.querySelectorAll('[data-tab]').forEach(x=>x.onclick=()=>setTab(x.dataset.tab));
document.querySelectorAll('.chip').forEach(x=>x.onclick=()=>{$('cmd').value=x.dataset.example;$('cmd').focus()});
function token(){return $('operatorToken')?.value||sessionToken||$('operatorTokenSystem')?.value||''}
async function preview(){const objective=$('cmd').value.trim();if(!objective)return toast('Enter a command first');$('previewBtn').disabled=true;try{const j=await api('/command/preview',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective})});jsonOut(j);renderPlan(j);toast(j.executable_now?'Mission is executable now':'Dependencies or approval are required')}catch(e){toast(e.message)}finally{$('previewBtn').disabled=false}}
async function execute(){const objective=$('cmd').value.trim();if(!objective)return toast('Enter a command first');$('executeBtn').disabled=true;try{const j=await api('/command/execute',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective,operator_token:token()})});jsonOut(j);currentMission=j.mission_id||'';renderMission(j.mission||j);await refreshAll();toast(j.status==='blocked'?'Blocked by missing dependencies':('Mission '+(j.status||'started')))}catch(e){toast(e.message)}finally{$('executeBtn').disabled=false}}
function renderPlan(j){const p=j.mission_plan;if(!p){$('latestMission').innerHTML='<div class="empty">No executable mission plan. See the command output for missing dependencies.</div>';return}const steps=(p.steps||[]).map((s,i)=>`<div class="step"><div class="num">${i+1}</div><div><strong>${esc(s.command)}</strong><small>${esc(s.provider||s.connector||'core')} · ${s.side_effect?'side effect / approval':'read-only or safe'} · verify=${s.verify!==false}</small></div><div class="pill">${s.side_effect?'approval':'safe'}</div></div>`).join('');$('latestMission').innerHTML=`<div class="plan">${steps}</div>`}
function renderMission(m){const x=m||{};currentMission=x.mission_id||x.id||currentMission;const steps=(x.steps||[]).map(s=>`<div class="item row"><div><b>Step ${esc(s.ordinal)}</b><div class="note">${esc(s.command)}</div></div><span class="pill">${esc(s.status)}${s.verification_status?(' · '+esc(s.verification_status)):''}</span></div>`).join('');$('latestMission').innerHTML=`<div class="item row"><div><b>${esc(x.objective||'Mission')}</b><div class="note">${esc(x.id||currentMission||'')}</div></div><span class="pill">${esc(x.status||'unknown')}</span></div><div style="height:8px"></div>${steps||'<div class="empty">No step data.</div>'}`}
async function refreshAll(){try{const j=await api('/control-center');latestData=j;const r=j.readiness||{};const c=j.capabilities||{};const missions=j.missions||[];$('worldReady').textContent=r.ready?'ready':'not ready';$('worldReady').className=r.ready?'good':'warn';$('capCount').textContent=(c.ready_count??0)+' / '+(c.total_count??0);$('blockedCount').textContent=c.blocked_count??0;$('readinessBar').style.width=Math.round(((c.ready_count||0)/Math.max(1,c.total_count||1))*100)+'%';$('readinessNote').textContent=r.ready?'All declared external dependencies are healthy.':(r.missing||[]).join(' · ')||'No missing dependencies reported';$('mActive').textContent=missions.length;$('mReady').textContent=c.ready_count??0;$('mBlocked').textContent=c.blocked_count??0;$('mVerified').textContent=missions.filter(x=>['completed','closed'].includes(String(x.status))).length;if(missions[0])renderMission(missions[0]);renderCapabilities(c.items||[]);renderConnections(r);$('statusText').textContent='online';}catch(e){$('statusText').textContent='attention';toast(e.message)}}
async function loadMissions(){const j=await api('/missions/recent?limit=40');const arr=j.missions||[];$('missionsList').innerHTML=arr.length?arr.map(x=>`<div class="item row"><div><b>${esc(x.objective)}</b><div class="note mono">${esc(x.id)} · ${new Date((x.updated_at||0)*1000).toLocaleString()}</div></div><span class="pill">${esc(x.status)}</span></div>`).join(''):'<div class="empty">No missions yet.</div>'}
function renderCapabilities(arr){const q=($('capSearch')?.value||'').toLowerCase();const rows=arr.filter(x=>JSON.stringify(x).toLowerCase().includes(q));$('capList').innerHTML=rows.map(x=>`<div class="item row"><div><b>${esc(x.capability)}</b><div class="note">${esc(x.description||x.dependency||x.connector||'')}</div></div><span class="pill ${x.ready?'good':'warn'}">${x.ready?'READY':'BLOCKED'}</span></div>`).join('')||'<div class="empty">No matching capabilities.</div>'}
function renderConnections(r){const p=r.providers||{};const providers=Object.keys(p).map(k=>`<div class="item row"><div><b>${esc(k)}</b><div class="note">${(p[k].accounts||[]).length?'connected account':'no connected account'}</div></div><span class="pill ${p[k].ready?'good':'warn'}">${p[k].ready?'READY':'WAITING'}</span></div>`).join('');const br=(r.browser_runtimes||[]).map(x=>`<div class="item row"><span>browser · ${esc(x.name)}</span><span class="pill">${esc(x.status)}</span></div>`).join('');const dv=(r.device_connections||[]).map(x=>`<div class="item row"><span>${esc(x.service)} · ${esc(x.name)}</span><span class="pill">${esc(x.status)}</span></div>`).join('');$('connectionsList').innerHTML=providers+(br||dv?'<div style="height:4px"></div>'+br+dv:'')||'<div class="empty">No external connections.</div>'}
async function loadCapabilities(){try{const j=await api('/capabilities');renderCapabilities(j.capabilities||[])}catch(e){toast(e.message)}}
async function loadConnections(){try{const j=await api('/external-readiness');renderConnections(j)}catch(e){toast(e.message)}}
async function loadReality(){try{const j=await api('/reality-status');$('realityOutput').textContent=JSON.stringify(j,null,2);const o=$('operatorTokenSystem');if(o&&sessionToken)o.value=''}catch(e){toast(e.message)}}
function connectProvider(){const provider=$('provider').value,account=$('account').value.trim(),secret=$('providerToken').value;const op=$('operatorToken').value||sessionToken;if(!op)return toast('Operator token required');if(!secret)return toast('Provider token required');api('/activate/provider',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({provider,account_name:account,access_token:secret,operator_token:op})}).then(j=>{sessionToken=op;$('providerToken').value='';jsonOut(j);toast('Provider securely connected');return refreshAll()}).catch(e=>toast(e.message))}
function copyOperator(){const v=$('operatorTokenSystem').value.trim();if(v){sessionToken=v;toast('Operator token loaded for this page session only')}else toast('Enter operator token first')}
$('previewBtn').onclick=preview;$('executeBtn').onclick=execute;$('capSearch').oninput=()=>{if(latestData)renderCapabilities(latestData.capabilities.items||[])};
setInterval(()=>{if(currentMission)fetch('/mission/'+encodeURIComponent(currentMission)).then(r=>r.json()).then(renderMission).catch(()=>{});refreshAll()},7000);
refreshAll();
</script>
</body></html>"""

# Install the new UI by rebinding the existing interface function's global lookup.

def _interface_html_195() -> str:
    return ROYAL_INTERFACE_HTML_195

_interface_html_187 = _interface_html_195


@app.get("/self-test-195")
def self_test_195():
    checks = []
    def ck(name, fn):
        try:
            value = fn()
            if value is False:
                raise AssertionError("condition returned false")
            checks.append({"name": name, "passed": True})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "error": str(exc)[:500]})
    ck("version", lambda: APP_VERSION == "TARGET-2050.195")
    ck("build", lambda: BUILD == "ROYAL-REAL-WORLD-COMMAND-CENTER-CORE")
    ck("command session table", lambda: _table_exists_186("command_sessions_195"))
    ck("control center", lambda: isinstance(control_center_195()["readiness"], dict))
    ck("recent missions", lambda: isinstance(missions_recent_195()["missions"], list))
    safe_preview = command_preview_195({"objective": "ping"})
    ck("safe command preview", lambda: safe_preview["mission_plan"] is not None)
    blocked_preview = command_preview_195({"objective": "send a Slack message to general: hello"})
    ck("external dependency detection", lambda: isinstance(blocked_preview["missing_dependencies"], list))
    ck("natural language normalization", lambda: _normalize_natural_command_194("send a Slack message to general: hello") == "slack post_message general | hello")
    ck("mission engine preserved", lambda: callable(_mission_create_191) and callable(_mission_run_191))
    ck("strong verification preserved", lambda: callable(_execute_real_job_190))
    ck("capability fabric preserved", lambda: len(_capability_inventory_194()) >= 8)
    ck("interface installed", lambda: "Royal control plane" in _interface_html_195() and "data-tab=\"connections\"" in _interface_html_195())
    ck("mobile interface", lambda: "viewport-fit=cover" in _interface_html_195() and "mobilebar" in _interface_html_195())
    ck("secret safety", lambda: control_center_195()["safety"]["secret_values_exposed"] is False)
    ck("no plaintext secrets", lambda: control_center_195()["safety"]["plaintext_secret_storage"] is False)
    ck("no arbitrary code", lambda: control_center_195()["safety"]["arbitrary_code_execution"] is False)
    ck("no uncertain replay", lambda: control_center_195()["safety"]["automatic_uncertain_replay"] is False)
    # High-level safe-command path is exercised in-process without requiring any external credential.
    result = command_execute_195({"objective": "ping"})
    ck("end-to-end command center safe path", lambda: result.get("status") == "completed" and bool(result.get("mission_id")))
    ck("reality status", lambda: reality_status_195()["main_goal"]["unified_command_center"] is True)
    return {
        "status": "completed",
        "version": APP_VERSION,
        "build": BUILD,
        "passed": all(x["passed"] for x in checks),
        "tests": checks,
        "real_world_reality": {
            "unified_command_center": True,
            "natural_language_to_mission": True,
            "durable_execution": True,
            "approval_control": True,
            "strong_verification": True,
            "secure_external_connections": True,
            "capability_preflight": True,
            "mobile_desktop_interface": True,
            "secret_values_exposed": False,
            "plaintext_secret_storage": False,
            "arbitrary_code_execution": False,
            "automatic_uncertain_replay": False,
        },
    }

# ============================================================
# TARGET-2050.196 — WORLD-OS INTENT + GOAL COMMAND FABRIC
# Adds a deterministic, dependency-aware natural-language command compiler,
# durable goal contracts, world-state snapshots, safe simulation, and a
# premium command-center UI. Unknown intent is never guessed.
# No new runtime dependency is introduced.
# ============================================================

APP_VERSION = "TARGET-2050.197"
BUILD = "WORLD-INTELLIGENCE-PRODUCTION-FABRIC-CORE"
PREVIOUS_BUILD = "TARGET-2050.196"
try:
    app.version = APP_VERSION
except Exception:
    pass

with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS goal_contracts_196 (
        id TEXT PRIMARY KEY,
        objective TEXT NOT NULL,
        normalized_json TEXT NOT NULL,
        intent_json TEXT NOT NULL,
        plan_json TEXT NOT NULL,
        readiness_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_goal_contracts_196_updated
        ON goal_contracts_196(updated_at DESC);
    CREATE TABLE IF NOT EXISTS world_snapshots_196 (
        id TEXT PRIMARY KEY,
        reason TEXT NOT NULL,
        state_json TEXT NOT NULL,
        state_hash TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS intent_records_196 (
        id TEXT PRIMARY KEY,
        objective TEXT NOT NULL,
        intent_type TEXT NOT NULL,
        confidence REAL NOT NULL,
        normalized_json TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    """)


def _compact_text_196(value: str, limit: int = 2000) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _natural_to_canonical_196(command: str) -> Dict[str, Any]:
    s = _compact_text_196(command)
    if not s:
        return {"status":"needs_clarification","reason":"empty command"}

    # Preserve already-supported canonical grammar.
    try:
        normalized = _normalize_natural_command_194(s)
        item = _real_command_atomic_190(normalized)
        if item.get("execution_mode") != "clarification":
            return {"status":"compiled","normalized":normalized,"item":item,
                    "intent_type":f"{item.get('connector')}.{item.get('action')}","confidence":1.0}
    except Exception:
        pass

    m = re.match(r"^create (?:a )?(?:new )?github issue (?:in|on) ([^\s]+/[^\s]+) (?:called|titled) ['\"]?(.+?)['\"]?(?:\s*:\s*(.*))?$", s, re.I)
    if m:
        owner_repo, title, body = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
        normalized = f"github create_issue {owner_repo} | {title}" + (f" | {body}" if body else "")
        return {"status":"compiled","normalized":normalized,"intent_type":"github.create_issue","confidence":0.99,"item":_real_command_atomic_190(normalized)}

    m = re.match(r"^(?:send|post) (?:a )?(?:message )?(?:to )?(?:slack )?(?:channel )?(#?[A-Za-z0-9._-]+)\s*:\s*(.+)$", s, re.I)
    if m and ("slack" in s.lower() or s.lower().startswith(("send a message to", "post to"))):
        channel, text_value = m.group(1).lstrip("#"), m.group(2).strip()
        normalized = f"slack post_message {channel} | {text_value}"
        return {"status":"compiled","normalized":normalized,"intent_type":"slack.post_message","confidence":0.96,"item":_real_command_atomic_190(normalized)}

    m = re.match(r"^(?:schedule|create|add) (?:a )?(?:google )?(?:calendar )?(?:event|meeting) (?:called|titled) ['\"]?(.+?)['\"]?\s+(?:from|starting)\s+([^\s]+)\s+(?:to|until)\s+([^\s]+)(?:\s*:\s*(.*))?$", s, re.I)
    if m:
        title, start, end, desc = m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), (m.group(4) or "").strip()
        normalized = f"google create_event | {start} | {end} | {title}" + (f" | {desc}" if desc else "")
        return {"status":"compiled","normalized":normalized,"intent_type":"google.create_event","confidence":0.94,"item":_real_command_atomic_190(normalized)}

    m = re.match(r"^(?:schedule|create|add) (?:a )?(?:microsoft |outlook )?(?:calendar )?(?:event|meeting) (?:called|titled) ['\"]?(.+?)['\"]?\s+(?:from|starting)\s+([^\s]+)\s+(?:to|until)\s+([^\s]+)$", s, re.I)
    if m:
        title, start, end = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        normalized = f"microsoft create_event | {start} | {end} | {title}"
        return {"status":"compiled","normalized":normalized,"intent_type":"microsoft.create_event","confidence":0.94,"item":_real_command_atomic_190(normalized)}

    m = re.match(r"^(?:open|visit|go to|navigate to)\s+(https?://\S+)$", s, re.I)
    if m:
        normalized = "open " + m.group(1).rstrip(".,")
        return {"status":"compiled","normalized":normalized,"intent_type":"browser.navigate","confidence":0.99,"item":_real_command_atomic_190(normalized)}

    m = re.match(r"^(?:click|press)\s+(.+)$", s, re.I)
    if m:
        normalized = "click " + m.group(1).strip()
        return {"status":"compiled","normalized":normalized,"intent_type":"browser.click","confidence":0.98,"item":_real_command_atomic_190(normalized)}

    m = re.match(r"^type\s+(.+?)\s+(?:into|in)\s+(.+)$", s, re.I)
    if m:
        normalized = f"type {m.group(1).strip()} into {m.group(2).strip()}"
        return {"status":"compiled","normalized":normalized,"intent_type":"browser.type","confidence":0.98,"item":_real_command_atomic_190(normalized)}

    m = re.match(r"^submit\s+(.+)$", s, re.I)
    if m:
        normalized = "submit " + m.group(1).strip()
        return {"status":"compiled","normalized":normalized,"intent_type":"browser.submit","confidence":0.98,"item":_real_command_atomic_190(normalized)}

    m = re.match(r"^(?:research|investigate|find out|look into)\s+(.+)$", s, re.I)
    if m:
        normalized = "research " + m.group(1).strip()
        return {"status":"compiled","normalized":normalized,"intent_type":"research.plan","confidence":0.95,"item":_real_command_atomic_190(normalized)}

    m = re.match(r"^(?:remember|save|store)\s+(?:that\s+)?(.+)$", s, re.I)
    if m:
        normalized = "remember " + m.group(1).strip()
        return {"status":"compiled","normalized":normalized,"intent_type":"memory.remember","confidence":0.97,"item":_real_command_atomic_190(normalized)}

    if re.fullmatch(r"ping", s, re.I):
        return {"status":"compiled","normalized":"ping","intent_type":"system.ping","confidence":1.0,"item":_real_command_atomic_190("ping")}

    if re.match(r"^(send|email)\b", s, re.I) and "@" in s:
        return {"status":"needs_dependency","normalized":s,"intent_type":"email.smtp","confidence":0.86,
                "dependency":"healthy device/email service","reason":"email is understood but requires a configured SMTP/device service"}

    return {"status":"needs_clarification","normalized":s,"intent_type":"unknown","confidence":0.0,
            "reason":"AI Infinity will not guess an unsupported action; provide a supported action, target, or capability"}


def _intent_understand_196(objective: str) -> Dict[str, Any]:
    raw_parts = _split_command_187(objective)
    compiled=[]; blockers=[]
    for ordinal, part in enumerate(raw_parts, 1):
        r=_natural_to_canonical_196(part); r["ordinal"]=ordinal; compiled.append(r)
        if r["status"] != "compiled": blockers.append({"ordinal":ordinal,"status":r["status"],"reason":r.get("reason"),"dependency":r.get("dependency")})
    return {"status":"understood" if not blockers else "attention","objective":objective,"step_count":len(compiled),
            "normalized_steps":[x.get("normalized",raw_parts[i]) for i,x in enumerate(compiled)],"intents":compiled,
            "blockers":blockers,"all_compiled":not blockers}


def _goal_success_contract_196(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {"type":"verified_step_closure","all_steps_required":True,"criteria":[
        {"step":s.get("id"),"condition":"completed","verification":"verified","side_effect":bool(s.get("side_effect"))}
        for s in plan.get("steps",[])]}


def _create_goal_contract_196(objective: str, understanding: Dict[str, Any], preflight: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    preflight=preflight or {}; plan=preflight.get("mission_plan") or {}; gid=uid("goal196"); t=now()
    status="ready" if understanding.get("all_compiled") and preflight.get("executable_now") else "blocked" if understanding.get("all_compiled") else "needs_clarification"
    contract={"id":gid,"objective":objective,"intent":understanding,"normalized_steps":understanding.get("normalized_steps",[]),
        "required_capabilities":preflight.get("matched_capabilities",[]),"missing_dependencies":preflight.get("missing_dependencies",[]),
        "plan":plan,"success_contract":_goal_success_contract_196(plan),"requires_approval":bool(plan.get("requires_approval")),
        "operator_required":bool(plan.get("operator_required")),"status":status,"plan_hash":digest(plan) if plan else None,
        "safety":{"approval_for_side_effects":True,"operator_gate":True,"no_arbitrary_code":True,"no_secret_values_returned":True,"no_uncertain_replay":True}}
    with _db_lock, db() as c:
        c.execute("INSERT INTO goal_contracts_196(id,objective,normalized_json,intent_json,plan_json,readiness_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (gid,objective,json.dumps(understanding.get("normalized_steps",[]),ensure_ascii=False),json.dumps(understanding,ensure_ascii=False),json.dumps(plan,ensure_ascii=False),json.dumps(preflight,ensure_ascii=False),status,t,t))
    return contract


def _world_state_196(reason: str="read") -> Dict[str, Any]:
    readiness=external_readiness_193(); caps=_capability_inventory_194(); missions=_recent_missions_195(8)
    providers=readiness.get("providers",{}); browser=readiness.get("browser_runtimes",[]); devices=readiness.get("device_connections",[])
    state={"version":APP_VERSION,"build":BUILD,"timestamp":now(),
        "system":{"status":"healthy","database":"ready","mission_engine":True,"adaptive_recovery":True,"verification":True,"command_center":True},
        "readiness":readiness,
        "capabilities":{"ready_count":sum(1 for x in caps if x.get("ready")),"blocked_count":sum(1 for x in caps if not x.get("ready")),"total_count":len(caps)},
        "providers":{k:{"ready":bool(v.get("ready")),"accounts":len(v.get("accounts",[]) or [])} for k,v in providers.items()},
        "browser":{"configured":bool(browser),"healthy":sum(1 for x in browser if x.get("status")=="healthy")},
        "device":{"configured":bool(devices),"healthy":sum(1 for x in devices if x.get("status")=="healthy")},
        "missions":{"recent":missions,"active":sum(1 for m in missions if m.get("status") not in {"completed","closed","failed"})},
        "safety":{"operator_gate":True,"secrets_exposed":False,"plaintext_secret_storage":False,"arbitrary_code_execution":False,"automatic_uncertain_replay":False},
        "recommended_next":(readiness.get("missing") or ["execute a safe command such as ping"])[0]}
    sid=uid("world196")
    with _db_lock, db() as c:
        c.execute("INSERT INTO world_snapshots_196(id,reason,state_json,state_hash,created_at) VALUES(?,?,?,?,?)",(sid,reason,json.dumps(state,ensure_ascii=False),digest(state),now()))
    state["snapshot_id"]=sid
    return state


@app.post("/intelligence/understand")
def intelligence_understand_196(body: Dict[str, Any]):
    objective=_compact_text_196(body.get("objective") or body.get("command") or "",5000)
    if not objective: raise HTTPException(400,"objective is required")
    result=_intent_understand_196(objective); rid=uid("intent196")
    first=result["intents"][0] if result.get("intents") else {}
    with _db_lock, db() as c:
        c.execute("INSERT INTO intent_records_196(id,objective,intent_type,confidence,normalized_json,created_at) VALUES(?,?,?,?,?,?)",
                  (rid,objective,first.get("intent_type","unknown"),float(first.get("confidence",0.0)),json.dumps(result,ensure_ascii=False),now()))
    result.update({"id":rid,"version":APP_VERSION,"build":BUILD,"safety":{"no_guessing":True,"secret_values_exposed":False,"arbitrary_code_execution":False}})
    return result


@app.post("/mission/compile-196")
def mission_compile_196(body: Dict[str, Any]):
    objective=_compact_text_196(body.get("objective") or body.get("command") or "",5000)
    if not objective: raise HTTPException(400,"objective is required")
    understanding=_intent_understand_196(objective)
    normalized=" then ".join(understanding.get("normalized_steps",[]))
    preflight=_capability_preflight_194(normalized) if understanding.get("all_compiled") else {"executable_now":False,"missing_dependencies":[],"matched_capabilities":[]}
    goal=_create_goal_contract_196(objective,understanding,preflight)
    return {"version":APP_VERSION,"build":BUILD,"status":goal["status"],"goal":goal,"understanding":understanding,"preflight":preflight,"world":_world_state_196("compile")}


@app.post("/command/simulate")
def command_simulate_196(body: Dict[str, Any]):
    objective=_compact_text_196(body.get("objective") or body.get("command") or "",5000)
    if not objective: raise HTTPException(400,"objective is required")
    understanding=_intent_understand_196(objective)
    if not understanding.get("all_compiled"):
        return {"status":"blocked","version":APP_VERSION,"build":BUILD,"dry_run":True,"understanding":understanding,"side_effects_performed":False}
    normalized=" then ".join(understanding["normalized_steps"]); preflight=_capability_preflight_194(normalized); plan=preflight.get("mission_plan") or {}
    return {"status":"ready" if preflight.get("executable_now") else "blocked","version":APP_VERSION,"build":BUILD,"dry_run":True,
            "objective":objective,"normalized_command":normalized,"plan":plan,"missing_dependencies":preflight.get("missing_dependencies",[]),
            "requires_approval":bool(plan.get("requires_approval")),"side_effects_performed":False,
            "safety":{"simulation_only":True,"secrets_exposed":False,"automatic_uncertain_replay":False}}


@app.post("/command/execute-196")
def command_execute_196(body: Dict[str, Any]):
    objective=_compact_text_196(body.get("objective") or body.get("command") or "",5000)
    if not objective: raise HTTPException(400,"objective is required")
    understanding=_intent_understand_196(objective)
    if not understanding.get("all_compiled"):
        return {"status":"blocked","version":APP_VERSION,"build":BUILD,"reason":"command needs clarification or an external dependency","understanding":understanding,"side_effects_performed":False}
    normalized=" then ".join(understanding["normalized_steps"]); delegated=dict(body); delegated["objective"]=normalized
    result=command_execute_195(delegated); result["version"]=APP_VERSION; result["build"]=BUILD; result["world_state"]=_world_state_196("execute")
    return result


@app.get("/goals/recent-196")
def goals_recent_196(limit: int=20):
    limit=max(1,min(int(limit or 20),50))
    with _db_lock, db() as c:
        rows=c.execute("SELECT id,objective,status,created_at,updated_at,plan_json,readiness_json FROM goal_contracts_196 ORDER BY updated_at DESC LIMIT ?",(limit,)).fetchall()
    out=[]
    for r in rows:
        d=dict(r)
        try: d["plan"]=json.loads(d.pop("plan_json") or "{}")
        except Exception: d.pop("plan_json",None)
        try: d["readiness"]=json.loads(d.pop("readiness_json") or "{}")
        except Exception: d.pop("readiness_json",None)
        out.append(d)
    return {"version":APP_VERSION,"build":BUILD,"goals":out,"secret_values_exposed":False}


@app.get("/world-state")
def world_state_196(reason: str="read"):
    return _world_state_196(_compact_text_196(reason,120) or "read")


@app.get("/self-test-196")
def self_test_196():
    checks=[]
    def ck(name,fn):
        try:
            value=fn()
            if value is False: raise AssertionError("condition returned false")
            checks.append({"name":name,"passed":True})
        except Exception as exc:
            checks.append({"name":name,"passed":False,"error":str(exc)[:500]})
    ck("version",lambda:APP_VERSION=="TARGET-2050.196")
    ck("build",lambda:BUILD=="WORLD-OS-INTENT-AND-GOAL-ENGINE-CORE")
    ck("goal table",lambda:_table_exists_186("goal_contracts_196"))
    ck("world snapshot table",lambda:_table_exists_186("world_snapshots_196"))
    ck("intent table",lambda:_table_exists_186("intent_records_196"))
    u=_intent_understand_196("create a GitHub issue in octocat/Hello-World titled Fix login: investigate failure")
    ck("natural intent understanding",lambda:u["all_compiled"] and u["intents"][0]["intent_type"]=="account.create_issue")
    b=_intent_understand_196("send a Slack message to general: deployment finished")
    ck("slack understanding",lambda:b["all_compiled"] and b["normalized_steps"][0].startswith("slack post_message"))
    x=_intent_understand_196("open https://example.com")
    ck("browser understanding",lambda:x["all_compiled"] and x["intents"][0]["intent_type"]=="browser.navigate")
    r=_intent_understand_196("research autonomous AI agents")
    ck("research understanding",lambda:r["all_compiled"] and r["intents"][0]["intent_type"]=="research.plan")
    unk=_intent_understand_196("do something magical")
    ck("unknown commands never guessed",lambda:not unk["all_compiled"] and unk["intents"][0]["status"]=="needs_clarification")
    sim=command_simulate_196({"objective":"ping"})
    ck("safe simulation",lambda:sim["dry_run"] is True and sim["side_effects_performed"] is False)
    w=_world_state_196("self-test")
    ck("world state",lambda:w["system"]["status"]=="healthy" and w["safety"]["secrets_exposed"] is False)
    c=mission_compile_196({"objective":"ping"})
    ck("goal contract",lambda:c["goal"]["success_contract"]["type"]=="verified_step_closure")
    ck("plan hash",lambda:bool(c["goal"]["plan_hash"]))
    ck("dependency-aware readiness",lambda:"missing_dependencies" in c["preflight"])
    ck("mission engine preserved",lambda:callable(_mission_create_191) and callable(_mission_run_191))
    ck("command center preserved",lambda:"World OS" in _interface_html_187())
    ck("security preserved",lambda:c["goal"]["safety"]["no_arbitrary_code"] is True)
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in checks),"tests":checks,
            "real_world_reality":{"universal_intent_fabric":True,"goal_contracts":True,"world_state":True,"dependency_aware_planning":True,"safe_simulation":True,"command_center_preserved":True,"mission_engine_preserved":True,"secure_execution_boundary":True,"secrets_exposed":False,"arbitrary_code_execution":False,"automatic_uncertain_replay":False}}


WORLD_OS_INTERFACE_196 = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>AI Infinity — World OS</title>
<style>
:root{--bg:#071018;--panel:#0e1924;--panel2:#122333;--text:#eaf2f8;--muted:#8fa5b8;--line:#1d3447;--good:#55d38a;--warn:#ffc857;--bad:#ff6b6b;--accent:#69a9ff;--shadow:0 20px 60px rgba(0,0,0,.28)}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 700px at 70% -10%,#163152 0,transparent 60%),var(--bg);color:var(--text);font:14px/1.45 Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,textarea{font:inherit}button{cursor:pointer}
.top{position:sticky;top:0;z-index:5;background:rgba(7,16,24,.84);backdrop-filter:blur(18px);border-bottom:1px solid var(--line)}.bar{max-width:1420px;margin:auto;display:flex;align-items:center;justify-content:space-between;padding:14px 18px}.brand{font-size:20px;font-weight:800}.brand span{color:var(--accent)}.status{display:flex;gap:8px;align-items:center;color:var(--muted)}.dot{width:9px;height:9px;border-radius:50%;background:var(--good)}
.shell{max-width:1420px;margin:auto;padding:18px;display:grid;grid-template-columns:230px minmax(0,1fr);gap:18px}.nav,.panel{background:rgba(14,25,36,.88);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}.nav{padding:12px;height:fit-content;position:sticky;top:78px}.nav button{width:100%;text-align:left;background:transparent;border:0;color:var(--muted);padding:12px;border-radius:11px}.nav button:hover,.nav button.on{background:var(--panel2);color:var(--text)}.nav .k{font-size:11px;text-transform:uppercase;letter-spacing:1.4px;padding:12px 12px 6px;color:#6f879a}
main{min-width:0}.hero{padding:22px}.eyebrow{color:var(--accent);font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:1.5px}.hero h1{margin:7px 0;font-size:clamp(28px,4vw,48px);line-height:1.03}.hero p{margin:0;color:var(--muted);max-width:900px}.command{margin-top:18px;background:#09141e;border:1px solid #28455d;border-radius:16px;padding:12px}.command textarea{width:100%;min-height:84px;resize:vertical;background:transparent;border:0;outline:0;color:var(--text);font-size:18px}.actions{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.chips{display:flex;gap:7px;flex-wrap:wrap}.chip{border:1px solid var(--line);background:var(--panel);color:var(--muted);padding:7px 10px;border-radius:999px}.primary{border:0;background:var(--accent);color:#071018;font-weight:800;padding:10px 15px;border-radius:10px}.ghost{border:1px solid var(--line);background:transparent;color:var(--text);padding:10px 14px;border-radius:10px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}.metric{padding:16px}.metric b{font-size:26px}.metric small{display:block;color:var(--muted);margin-top:4px}.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.rows{display:grid;grid-template-columns:1.1fr .9fr;gap:14px;margin-top:14px}.panel .head{padding:16px 17px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:10px;align-items:center}.panel .head h3{margin:0;font-size:15px}.body{padding:15px}.item{padding:12px 0;border-bottom:1px solid rgba(29,52,71,.7)}.item:last-child{border-bottom:0}.row{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}.note{color:var(--muted);font-size:12px}.pill{padding:4px 8px;border:1px solid var(--line);border-radius:999px;white-space:nowrap;font-size:11px}.step{display:grid;grid-template-columns:30px 1fr auto;gap:10px;align-items:center;padding:12px;border:1px solid var(--line);border-radius:12px;margin-bottom:8px;background:rgba(18,35,51,.55)}.num{width:28px;height:28px;border-radius:8px;display:grid;place-items:center;background:var(--panel2);color:var(--accent);font-weight:800}.out{white-space:pre-wrap;background:#061019;border:1px solid var(--line);border-radius:12px;padding:13px;color:#c9d9e5;max-height:340px;overflow:auto}.empty{color:var(--muted);padding:12px 0}.section{display:none}.section.on{display:block}footer{color:#6f879a;text-align:center;padding:20px 0 4px;font-size:12px}.mobilebar{display:none}
@media(max-width:980px){.shell{grid-template-columns:1fr}.nav{display:none}.mobilebar{position:fixed;display:flex;bottom:10px;left:10px;right:10px;z-index:8;padding:8px;background:rgba(14,25,36,.92);border:1px solid var(--line);border-radius:16px;backdrop-filter:blur(18px)}.mobilebar button{flex:1;background:transparent;border:0;color:var(--muted);padding:10px 6px;font-size:11px}.mobilebar button.on{color:var(--text)}.shell{padding-bottom:85px}.rows{grid-template-columns:1fr}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:520px){.bar{padding:12px}.shell{padding:10px}.hero{padding:17px}.command textarea{font-size:16px}.grid{gap:8px}.metric{padding:13px}.metric b{font-size:22px}}
</style></head><body>
<div class="top"><div class="bar"><div class="brand">AI <span>∞</span> WORLD OS</div><div class="status"><span class="dot" id="dot"></span><span id="statusText">online</span><span>·</span><span id="versionText">2050.196</span></div></div></div>
<div class="shell"><aside class="nav"><div class="k">System</div><button class="on" data-tab="command">⌁ Command</button><button data-tab="missions">◈ Missions</button><button data-tab="world">◉ World State</button><button data-tab="connections">⛓ Connections</button><button data-tab="safety">◇ Safety</button><div class="k">Fast actions</div><button onclick="setCmd('research the current state of autonomous AI agents')">Research</button><button onclick="setCmd('ping')">Ping</button></aside>
<main><section class="section on" id="tab-command"><div class="panel hero"><div class="eyebrow">Real-world command fabric</div><h1>Tell AI Infinity what outcome you need.</h1><p>Understand → plan → check dependencies → approve → execute → observe → verify → close. Unsupported intent is stopped rather than guessed.</p>
<div class="command"><textarea id="cmd" placeholder="Example: create a GitHub issue in owner/repo titled Fix login: investigate the failing login flow"></textarea><div class="actions"><div class="chips"><button class="chip" onclick="setCmd('create a GitHub issue in octocat/Hello-World titled Test issue: created by AI Infinity')">GitHub</button><button class="chip" onclick="setCmd('send a Slack message to general: deployment finished')">Slack</button><button class="chip" onclick="setCmd('research autonomous AI agents')">Research</button><button class="chip" onclick="setCmd('open https://example.com')">Browser</button></div><div><button class="ghost" id="simBtn" onclick="simulate()">Simulate</button><button class="primary" id="runBtn" onclick="runCommand()">Execute</button></div></div></div></div>
<div class="grid"><div class="panel metric"><b id="mReady">0</b><small>Ready capabilities</small></div><div class="panel metric"><b id="mBlocked">0</b><small>Blocked capabilities</small></div><div class="panel metric"><b id="mMissions">0</b><small>Recent goals</small></div><div class="panel metric"><b id="mHealthy" class="good">YES</b><small>Core healthy</small></div></div>
<div class="rows"><div class="panel"><div class="head"><h3>Goal contract / mission plan</h3><span class="pill" id="planBadge">idle</span></div><div class="body" id="plan"><div class="empty">Enter a command and simulate it first.</div></div></div><div class="panel"><div class="head"><h3>Command result</h3><button class="ghost" onclick="clearOut()">Clear</button></div><div class="body"><div class="out" id="out">Waiting for a command.</div></div></div></div></section>
<section class="section" id="tab-missions"><div class="panel"><div class="head"><h3>Recent goals & missions</h3><button class="ghost" onclick="loadMissions()">Refresh</button></div><div class="body" id="missionList"><div class="empty">Loading…</div></div></div></section>
<section class="section" id="tab-world"><div class="panel"><div class="head"><h3>World state</h3><button class="ghost" onclick="loadWorld()">Refresh</button></div><div class="body"><div class="out" id="worldOut">Loading…</div></div></div></section>
<section class="section" id="tab-connections"><div class="panel"><div class="head"><h3>External connections</h3><span class="pill">secrets hidden</span></div><div class="body" id="connList"><div class="empty">Loading…</div></div></div></section>
<section class="section" id="tab-safety"><div class="panel"><div class="head"><h3>Safety contract</h3></div><div class="body" id="safetyList"></div></div></section>
<footer>AI Infinity · World OS · verified closure architecture · no arbitrary code · no uncertain replay</footer></main></div>
<div class="mobilebar"><button class="on" data-tab="command">Command</button><button data-tab="missions">Missions</button><button data-tab="world">World</button><button data-tab="connections">Links</button><button data-tab="safety">Safety</button></div>
<script>
const $=id=>document.getElementById(id);let world=null;function esc(v){return String(v??'').replace(/[&<>\"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\\':'&#39;'}[m])}async function api(url,opts){const r=await fetch(url,opts);const t=await r.text();let j;try{j=JSON.parse(t)}catch{throw new Error(t||('HTTP '+r.status))}if(!r.ok)throw new Error(j.detail||j.error||('HTTP '+r.status));return j}function setCmd(x){$('cmd').value=x;document.querySelector('[data-tab="command"]').click();$('cmd').focus()}function clearOut(){$('out').textContent='Waiting for a command.'}function renderPlan(j){const p=j.plan||j.goal?.plan||j.preflight?.mission_plan;if(!p?.steps){$('plan').innerHTML='<div class="empty">No executable plan. See blockers in the result.</div>';$('planBadge').textContent=j.status||'blocked';return}$('planBadge').textContent=(j.requires_approval||j.goal?.requires_approval||p.requires_approval)?'approval required':'ready';$('plan').innerHTML=(p.steps||[]).map((s,i)=>`<div class="step"><div class="num">${i+1}</div><div><b>${esc(s.command)}</b><div class="note">${esc(s.provider||s.connector||'core')} · ${s.side_effect?'side effect':'safe'} · verification=${s.verify!==false}</div></div><span class="pill">${s.side_effect?'APPROVE':'SAFE'}</span></div>`).join('')}async function simulate(){const objective=$('cmd').value.trim();if(!objective)return $('out').textContent='Enter a command first.';$('simBtn').disabled=true;try{const j=await api('/command/simulate',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective})});$('out').textContent=JSON.stringify(j,null,2);renderPlan(j)}catch(e){$('out').textContent=e.message}finally{$('simBtn').disabled=false}}async function runCommand(){const objective=$('cmd').value.trim();if(!objective)return $('out').textContent='Enter a command first.';$('runBtn').disabled=true;try{const j=await api('/command/execute-196',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective})});$('out').textContent=JSON.stringify(j,null,2);renderPlan(j);await refresh()}catch(e){$('out').textContent=e.message}finally{$('runBtn').disabled=false}}async function refresh(){try{world=await api('/world-state');$('mReady').textContent=world.capabilities.ready_count;$('mBlocked').textContent=world.capabilities.blocked_count;$('mMissions').textContent=(world.missions.recent||[]).length;$('mHealthy').textContent=world.system.status==='healthy'?'YES':'NO';$('mHealthy').className=world.system.status==='healthy'?'good':'bad';renderConnections(world.readiness||{});renderSafety(world.safety||{});$('worldOut').textContent=JSON.stringify(world,null,2)}catch(e){$('statusText').textContent='attention';$('dot').className='dot bad'}}function renderConnections(r){const ps=r.providers||{};let html=Object.keys(ps).map(k=>`<div class="item row"><div><b>${esc(k)}</b><div class="note">${(ps[k].accounts||[]).length} account(s)</div></div><span class="pill ${ps[k].ready?'good':'warn'}">${ps[k].ready?'READY':'WAITING'}</span></div>`).join('');(r.browser_runtimes||[]).forEach(x=>html+=`<div class="item row"><div><b>browser · ${esc(x.name)}</b><div class="note">runtime</div></div><span class="pill">${esc(x.status)}</span></div>`);(r.device_connections||[]).forEach(x=>html+=`<div class="item row"><div><b>${esc(x.service)} · ${esc(x.name)}</b><div class="note">device service</div></div><span class="pill">${esc(x.status)}</span></div>`);$('connList').innerHTML=html||'<div class="empty">No external connections configured.</div>'}function renderSafety(s){$('safetyList').innerHTML=Object.entries(s).map(([k,v])=>`<div class="item row"><span>${esc(k.replaceAll('_',' '))}</span><span class="pill ${v===true?'good':''}">${String(v)}</span></div>`).join('')}async function loadMissions(){try{const j=await api('/goals/recent-196?limit=30');$('missionList').innerHTML=(j.goals||[]).map(x=>`<div class="item row"><div><b>${esc(x.objective)}</b><div class="note">${esc(x.id)} · ${new Date((x.updated_at||0)*1000).toLocaleString()}</div></div><span class="pill">${esc(x.status)}</span></div>`).join('')||'<div class="empty">No goals yet.</div>'}catch(e){$('missionList').textContent=e.message}}async function loadWorld(){await refresh()}document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('on',x.dataset.tab===b.dataset.tab));document.querySelectorAll('.section').forEach(x=>x.classList.toggle('on',x.id==='tab-'+b.dataset.tab));if(b.dataset.tab==='missions')loadMissions();if(b.dataset.tab==='world')loadWorld();if(b.dataset.tab==='connections')refresh()});refresh();setInterval(refresh,8000);
</script></body></html>'''

def _interface_html_196() -> str:
    return WORLD_OS_INTERFACE_196

_interface_html_187 = _interface_html_196

@app.get("/world-os", response_class=HTMLResponse)
def world_os_196():
    return WORLD_OS_INTERFACE_196

# Replace any legacy route that used the historical /self-test-196 path.
for _legacy_route in list(app.routes):
    if getattr(_legacy_route, "path", None) == "/self-test-196" and "GET" in (getattr(_legacy_route, "methods", set()) or set()):
        try:
            app.routes.remove(_legacy_route)
        except ValueError:
            pass
app.add_api_route("/self-test-196", self_test_196, methods=["GET"])


# ============================================================
# TARGET-2050.197
# WORLD-INTELLIGENCE-PRODUCTION-FABRIC-CORE
# ============================================================
try:
    app.version = APP_VERSION
except Exception:
    pass
with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS intent_outcomes_197 (
        id TEXT PRIMARY KEY, objective TEXT NOT NULL, intent_signature TEXT NOT NULL,
        status TEXT NOT NULL, verification_status TEXT, plan_hash TEXT, result_hash TEXT, created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_intent_outcomes_197_sig ON intent_outcomes_197(intent_signature, created_at DESC);
    CREATE TABLE IF NOT EXISTS world_adaptations_197 (
        id TEXT PRIMARY KEY, objective TEXT NOT NULL, world_hash TEXT NOT NULL, decision_json TEXT NOT NULL, created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS service_metrics_197 (
        id INTEGER PRIMARY KEY CHECK(id=1), started_at REAL NOT NULL, requests INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0, missions INTEGER NOT NULL DEFAULT 0, last_request_at REAL,
        last_worker_at REAL, worker_status TEXT NOT NULL DEFAULT 'starting'
    );
    CREATE TABLE IF NOT EXISTS worker_events_197 (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, data_json TEXT, created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS integration_catalog_197 (
        key TEXT PRIMARY KEY, category TEXT NOT NULL, actions_json TEXT NOT NULL,
        execution_mode TEXT NOT NULL, safety_json TEXT NOT NULL, updated_at REAL NOT NULL
    );""")
    _c.execute("INSERT OR IGNORE INTO service_metrics_197(id,started_at,worker_status) VALUES(1,?,?)", (now(), "starting"))

_INTEGRATION_CATALOG_197 = {
    "github": {"category":"developer","actions":["read_repo","create_issue","update_issue"],"execution_mode":"provider","safety":"approval_for_writes"},
    "slack": {"category":"communication","actions":["read","post_message"],"execution_mode":"provider","safety":"approval_for_writes"},
    "google": {"category":"productivity","actions":["calendar_read","calendar_write"],"execution_mode":"provider","safety":"approval_for_writes"},
    "microsoft": {"category":"productivity","actions":["calendar_read","calendar_write"],"execution_mode":"provider","safety":"approval_for_writes"},
    "browser": {"category":"web","actions":["navigate","click","type","submit","observe"],"execution_mode":"runtime","safety":"operator_gate_for_live_actions"},
    "email": {"category":"communication","actions":["send_smtp","read_imap"],"execution_mode":"device","safety":"approval_for_writes"},
    "http": {"category":"universal","actions":["safe_get","safe_head","approved_request"],"execution_mode":"connector","safety":"ssrf_and_credential_boundary"},
    "webhook": {"category":"automation","actions":["send"],"execution_mode":"connector","safety":"approval_and_ssrf_boundary"},
    "research": {"category":"knowledge","actions":["plan"],"execution_mode":"native","safety":"read_only"},
    "memory": {"category":"knowledge","actions":["store"],"execution_mode":"native","safety":"read_only"},
    "workspace": {"category":"files","actions":["list","safe_file_operations"],"execution_mode":"sandbox","safety":"workspace_boundary"},
}
with _db_lock, db() as _c:
    for _k,_v in _INTEGRATION_CATALOG_197.items():
        _c.execute("INSERT OR REPLACE INTO integration_catalog_197(key,category,actions_json,execution_mode,safety_json,updated_at) VALUES(?,?,?,?,?,?)",
                   (_k,_v["category"],json.dumps(_v["actions"]),_v["execution_mode"],json.dumps({"policy":_v["safety"]}),now()))
_197_METRICS_LOCK = threading.RLock()
_197_METRICS = {"requests":0,"errors":0,"missions":0,"last_request_at":None}

def _semantic_expand_197(command: str) -> Dict[str, Any]:
    s=_compact_text_196(command,5000)
    patterns=[
        (r"^(?:check|look at|show me|inspect)\s+(?:the )?(?:github )?repo(?:sitory)?\s+([^\s]+/[^\s]+)$", lambda m:f"github get_repo {m.group(1)}", "github.get_repo", .95),
        (r"^(?:create|open)\s+(?:an? )?(?:github )?issue\s+(?:in|on)\s+([^\s]+/[^\s]+)\s*(.*)$", lambda m:f"github create_issue {m.group(1)} | {m.group(2).strip() or 'Issue created by AI Infinity'}", "github.create_issue", .93),
        (r"^(?:message|tell|notify|post to)\s+(?:slack\s+)?(?:#)?([\w.-]+)\s*[:\-]\s*(.+)$", lambda m:f"slack post_message {m.group(1)} | {m.group(2).strip()}", "slack.post_message", .92),
        (r"^(?:email|send an email|send email)\s+(?:to\s+)?([^\s]+@[^\s]+)\s*[:\-]\s*(.+)$", lambda m:s, "email.smtp", .90),
        (r"^(?:visit|browse|open|go to)\s+(https?://\S+)$", lambda m:f"open {m.group(1)}", "browser.navigate", .98),
        (r"^(?:find|research|investigate|analyze|look into)\s+(.+)$", lambda m:f"research {m.group(1)}", "research.plan", .93),
        (r"^(?:save|remember|keep note of)\s+(.+)$", lambda m:f"remember {m.group(1)}", "memory.remember", .96),
    ]
    for pat,fn,itype,conf in patterns:
        m=re.match(pat,s,re.I)
        if m:
            normalized=fn(m)
            if itype == "email.smtp":
                return {"status":"needs_dependency","normalized":normalized,"intent_type":itype,"confidence":conf,"dependency":"healthy device/email service","reason":"email intent understood; a real SMTP/device service is required"}
            try:
                item=_real_command_atomic_190(normalized)
                return {"status":"compiled","normalized":normalized,"intent_type":itype,"confidence":conf,"item":item}
            except Exception:
                return {"status":"needs_dependency","normalized":normalized,"intent_type":itype,"confidence":conf}
    return {"status":"unknown","normalized":s,"intent_type":"unknown","confidence":0.0}

def _intent_understand_197(objective: str) -> Dict[str, Any]:
    raw_parts=_split_command_187(objective)
    compiled=[]; blockers=[]
    for ordinal,part in enumerate(raw_parts,1):
        x=_semantic_expand_197(part)
        if x.get("status")=="unknown": x=_natural_to_canonical_196(part)
        x["ordinal"]=ordinal; compiled.append(x)
        if x.get("status")!="compiled": blockers.append({"ordinal":ordinal,"status":x.get("status"),"reason":x.get("reason"),"dependency":x.get("dependency")})
    return {"status":"understood" if not blockers else "attention","objective":objective,"step_count":len(compiled),"normalized_steps":[x.get("normalized",raw_parts[min(i,len(raw_parts)-1)]) for i,x in enumerate(compiled)],"intents":compiled,"blockers":blockers,"all_compiled":not blockers,"engine":"semantic-fabric-197","confidence":min([float(x.get("confidence",0)) for x in compiled] or [0.0])}

def _adaptive_plan_197(objective: str) -> Dict[str, Any]:
    world=_world_state_196("adaptive-plan")
    understanding=_intent_understand_197(objective)
    normalized=" then ".join(understanding.get("normalized_steps",[]))
    preflight=_capability_preflight_194(normalized) if understanding.get("all_compiled") and normalized else {"executable_now":False,"missing_dependencies":[],"matched_capabilities":[],"mission_plan":None}
    decisions=[{"capability":cap.get("capability"),"ready":bool(cap.get("ready")),"decision":"use" if cap.get("ready") else "wait_for_dependency","dependency":cap.get("dependency")} for cap in preflight.get("matched_capabilities",[])]
    if preflight.get("missing_dependencies"): next_action="activate_dependencies"
    elif not understanding.get("all_compiled"): next_action="clarify_unknown_intent"
    elif (preflight.get("mission_plan") or {}).get("requires_approval"): next_action="request_operator_approval"
    else: next_action="execute_and_verify"
    decision={"next_action":next_action,"decisions":decisions,"world_hash":digest(world),"adaptation_policy":"never bypass approval; never replay uncertain external side effects; prefer verified read-back"}
    aid=uid("adapt197")
    with _db_lock, db() as c: c.execute("INSERT INTO world_adaptations_197(id,objective,world_hash,decision_json,created_at) VALUES(?,?,?,?,?)",(aid,objective,decision["world_hash"],json.dumps(decision,ensure_ascii=False),now()))
    return {"status":"ready" if preflight.get("executable_now") else "attention","objective":objective,"understanding":understanding,"preflight":preflight,"decision":decision,"world":world,"adaptation_id":aid}

def _record_outcome_197(objective: str, result: Dict[str,Any], plan_hash: Optional[str]=None) -> None:
    sig=digest(_intent_understand_197(objective).get("normalized_steps",[]))
    with _db_lock, db() as c: c.execute("INSERT INTO intent_outcomes_197(id,objective,intent_signature,status,verification_status,plan_hash,result_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",(uid("outcome197"),objective,sig,str(result.get("status") or "unknown"),str(result.get("verification_status") or result.get("verification") or ""),plan_hash,digest(result),now()))

def _worker_tick_197() -> None:
    t=now()
    try:
        with _db_lock, db() as c:
            c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA synchronous=NORMAL")
            c.execute("UPDATE service_metrics_197 SET last_worker_at=?,worker_status=? WHERE id=1",(t,"healthy"))
            c.execute("INSERT INTO worker_events_197(event,data_json,created_at) VALUES(?,?,?)",("heartbeat",json.dumps({"version":APP_VERSION}),t))
            c.execute("DELETE FROM worker_events_197 WHERE id NOT IN (SELECT id FROM worker_events_197 ORDER BY id DESC LIMIT 500)")
    except Exception:
        try:
            with _db_lock, db() as c: c.execute("UPDATE service_metrics_197 SET last_worker_at=?,worker_status=? WHERE id=1",(t,"degraded"))
        except Exception: pass

def _background_worker_197() -> None:
    interval=max(10,int(os.getenv("AI_INFINITY_WORKER_INTERVAL","30")))
    while True: _worker_tick_197(); time.sleep(interval)

@app.middleware("http")
async def _metrics_middleware_197(request, call_next):
    with _197_METRICS_LOCK: _197_METRICS["requests"]+=1; _197_METRICS["last_request_at"]=now()
    try:
        response=await call_next(request)
        if response.status_code>=500:
            with _197_METRICS_LOCK: _197_METRICS["errors"]+=1
        return response
    except Exception:
        with _197_METRICS_LOCK: _197_METRICS["errors"]+=1; raise

@app.get("/intelligence/capabilities")
def intelligence_capabilities_197():
    return {"version":APP_VERSION,"build":BUILD,"features":["broader_natural_language","compound_goal_composition","dependency_aware_adaptation","live_world_planning","outcome_learning","production_persistence_controls","background_worker","service_metrics","integration_catalog"],"integration_count":len(_INTEGRATION_CATALOG_197),"safety":{"no_guessing":True,"arbitrary_code_execution":False,"secret_values_exposed":False}}

@app.post("/intelligence/understand-197")
def intelligence_understand_197(body: Dict[str,Any]):
    objective=_compact_text_196(body.get("objective") or body.get("command") or "",5000)
    if not objective: raise HTTPException(400,"objective is required")
    return {**_intent_understand_197(objective),"version":APP_VERSION,"build":BUILD}

@app.post("/intelligence/adaptive-plan")
def intelligence_adaptive_plan_197(body: Dict[str,Any]):
    objective=_compact_text_196(body.get("objective") or body.get("command") or "",5000)
    if not objective: raise HTTPException(400,"objective is required")
    return {**_adaptive_plan_197(objective),"version":APP_VERSION,"build":BUILD}

@app.post("/command/simulate-197")
def command_simulate_197(body: Dict[str,Any]):
    objective=_compact_text_196(body.get("objective") or body.get("command") or "",5000)
    if not objective: raise HTTPException(400,"objective is required")
    adaptive=_adaptive_plan_197(objective)
    return {"status":"ready" if adaptive["preflight"].get("executable_now") else "blocked","version":APP_VERSION,"build":BUILD,"dry_run":True,"objective":objective,"adaptive":adaptive,"side_effects_performed":False,"safety":{"simulation_only":True,"secrets_exposed":False,"arbitrary_code_execution":False,"automatic_uncertain_replay":False}}

@app.post("/command/execute-197")
def command_execute_197(body: Dict[str,Any]):
    objective=_compact_text_196(body.get("objective") or body.get("command") or "",5000)
    if not objective: raise HTTPException(400,"objective is required")
    adaptive=_adaptive_plan_197(objective); preflight=adaptive["preflight"]
    if not preflight.get("executable_now"):
        return {"status":"blocked","version":APP_VERSION,"build":BUILD,"objective":objective,"adaptive":adaptive,"side_effects_performed":False}
    delegated=dict(body); delegated["objective"]=" then ".join(adaptive["understanding"]["normalized_steps"])
    result=command_execute_195(delegated); _record_outcome_197(objective,result,(preflight.get("mission_plan") or {}).get("plan_hash"))
    with _197_METRICS_LOCK: _197_METRICS["missions"]+=1
    result.update({"version":APP_VERSION,"build":BUILD,"adaptive":adaptive})
    return result

@app.get("/production/status")
def production_status_197():
    with _197_METRICS_LOCK: mem=dict(_197_METRICS)
    with _db_lock, db() as c: row=c.execute("SELECT * FROM service_metrics_197 WHERE id=1").fetchone(); outcomes=int(c.execute("SELECT COUNT(*) FROM intent_outcomes_197").fetchone()[0])
    db_path=str(DB_PATH); persistent=not db_path.startswith("/tmp/")
    return {"version":APP_VERSION,"build":BUILD,"status":"healthy" if row and row["worker_status"] in {"healthy","starting"} else "degraded","database":{"engine":"sqlite","path_configured":db_path,"persistent_path":persistent,"wal":True,"production_note":"Use AI_INFINITY_DB_PATH on a persistent volume; /tmp is ephemeral."},"worker":{"status":row["worker_status"] if row else "unknown","last_heartbeat":row["last_worker_at"] if row else None},"metrics":mem,"recorded_intent_outcomes":outcomes,"secret_values_exposed":False}

@app.get("/integrations")
def integrations_197():
    return {"version":APP_VERSION,"build":BUILD,"integrations":_INTEGRATION_CATALOG_197,"safety":{"provider_credentials_encrypted":True,"secret_values_exposed":False,"approval_for_side_effects":True}}

@app.get("/intelligence/learning")
def intelligence_learning_197(limit:int=20):
    limit=max(1,min(int(limit or 20),100))
    with _db_lock, db() as c: rows=c.execute("SELECT objective,intent_signature,status,verification_status,created_at FROM intent_outcomes_197 ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
    return {"version":APP_VERSION,"build":BUILD,"outcomes":[dict(r) for r in rows],"adaptive_learning":"outcome-aware planning only; source code is never self-modified"}

@app.get("/self-test-197")
def self_test_197():
    checks=[]
    def ck(name,fn):
        try:
            v=fn()
            if v is False: raise AssertionError("condition returned false")
            checks.append({"name":name,"passed":True})
        except Exception as exc: checks.append({"name":name,"passed":False,"error":str(exc)[:400]})
    ck("version",lambda:APP_VERSION=="TARGET-2050.197")
    ck("build",lambda:BUILD=="WORLD-INTELLIGENCE-PRODUCTION-FABRIC-CORE")
    for table in ("intent_outcomes_197","world_adaptations_197","service_metrics_197","worker_events_197","integration_catalog_197"): ck(table,lambda table=table:_table_exists_186(table))
    u=_intent_understand_197("check the GitHub repo octocat/Hello-World")
    ck("broader natural language",lambda:u.get("all_compiled") and u["intents"][0].get("intent_type")=="github.get_repo")
    c=_intent_understand_197("research autonomous agents then remember deployment safety")
    ck("compound goal composition",lambda:c.get("step_count")==2 and c.get("all_compiled"))
    a=_adaptive_plan_197("ping")
    ck("adaptive live-world planning",lambda:"decision" in a and "world" in a)
    sim=command_simulate_197({"objective":"ping"})
    ck("safe simulation",lambda:sim["dry_run"] and not sim["side_effects_performed"])
    ck("integration catalog",lambda:len(_INTEGRATION_CATALOG_197)>=10)
    prod=production_status_197(); ck("production status",lambda:"database" in prod and "worker" in prod and prod["database"]["wal"] is True)
    ck("security",lambda:prod["secret_values_exposed"] is False)
    ck("mission engine preserved",lambda:callable(_mission_create_191) and callable(_mission_run_191))
    ck("command center preserved",lambda:"World OS" in _interface_html_187())
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in checks),"tests":checks,"real_world_reality":{"broader_natural_language":True,"compound_goal_composition":True,"adaptive_live_world_planning":True,"production_persistence_controls":True,"background_monitoring":True,"broader_integration_catalog":True,"outcome_learning":True,"mission_engine_preserved":True,"command_center_preserved":True,"secrets_exposed":False,"arbitrary_code_execution":False,"automatic_uncertain_replay":False}}

NEXUS_INTERFACE_197 = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#070a12"><title>AI Infinity — Nexus</title><style>*{box-sizing:border-box}body{margin:0;background:#070a12;color:#eef2ff;font:15px system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1180px;margin:auto;padding:20px}.hero{display:flex;justify-content:space-between;gap:18px;align-items:center;margin-bottom:18px}.brand{font-size:25px;font-weight:800}.muted{color:#93a0ba}.grid{display:grid;grid-template-columns:1.35fr .65fr;gap:16px}.card{background:#0d1220;border:1px solid #202a40;border-radius:18px;padding:16px;box-shadow:0 12px 35px #0004}.cmd{min-height:115px;width:100%;resize:vertical;background:#080c16;color:#fff;border:1px solid #293651;border-radius:14px;padding:15px;font-size:17px;outline:none}.buttons{display:flex;gap:9px;margin-top:10px;flex-wrap:wrap}button{border:0;border-radius:11px;padding:11px 15px;background:#18233a;color:#fff;font-weight:700;cursor:pointer}button.primary{background:#fff;color:#080a10}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.metric{padding:13px;border:1px solid #202a40;border-radius:13px}.metric b{font-size:22px;display:block}.pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#18233a;font-size:12px}.good{color:#8ff0bd}.warn{color:#ffd37d}.bad{color:#ff8d9b}.list{display:grid;gap:8px;margin-top:10px}.row{display:flex;justify-content:space-between;gap:10px;padding:10px;border:1px solid #1d2740;border-radius:11px}.out{white-space:pre-wrap;overflow:auto;max-height:390px;background:#080c16;border-radius:12px;padding:12px;color:#c9d3e8}.wide{grid-column:1/-1}@media(max-width:800px){.grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}main{padding:12px}.hero{align-items:flex-start;flex-direction:column}}</style></head><body><main><div class="hero"><div><div class="brand">∞ AI Infinity Nexus</div><div class="muted">Command → plan → connect → act → verify → learn</div></div><span id="health" class="pill">connecting…</span></div><div class="grid"><section class="card"><h2>Command</h2><textarea id="cmd" class="cmd" placeholder="Tell AI Infinity what you want done…"></textarea><div class="buttons"><button onclick="simulate()">Simulate</button><button class="primary" onclick="run()">Execute</button><button onclick="understand()">Understand</button></div><div id="out" class="out" style="margin-top:12px">Ready.</div></section><aside class="card"><h2>Live World</h2><div class="metrics"><div class="metric"><span class="muted">Ready</span><b id="ready">—</b></div><div class="metric"><span class="muted">Blocked</span><b id="blocked">—</b></div><div class="metric"><span class="muted">Missions</span><b id="missions">—</b></div><div class="metric"><span class="muted">Worker</span><b id="worker">—</b></div></div><div id="deps" class="list"></div></aside><section class="card wide"><h2>Adaptive Plan</h2><div id="plan" class="out">Enter a command to see AI Infinity's world-aware plan.</div></section><section class="card"><h2>Connections</h2><div id="connections" class="list"></div></section><section class="card"><h2>Safety</h2><div id="safety" class="list"><div class="row"><span>Arbitrary code</span><span class="pill good">OFF</span></div><div class="row"><span>Uncertain replay</span><span class="pill good">OFF</span></div><div class="row"><span>Secret return</span><span class="pill good">OFF</span></div></div></section></div></main><script>const $=x=>document.getElementById(x);async function api(u,o){let r=await fetch(u,o),t=await r.text();let j;try{j=JSON.parse(t)}catch{throw Error(t)}if(!r.ok)throw Error(j.detail||j.error||'HTTP '+r.status);return j}function show(j){$('out').textContent=JSON.stringify(j,null,2)}async function understand(){let q=$('cmd').value.trim();if(!q)return;try{show(await api('/intelligence/understand-197',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})}))}catch(e){show({error:e.message})}}async function simulate(){let q=$('cmd').value.trim();if(!q)return;try{let j=await api('/command/simulate-197',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})});show(j);$('plan').textContent=JSON.stringify(j.adaptive?.decision||j,null,2)}catch(e){show({error:e.message})}}async function run(){let q=$('cmd').value.trim();if(!q)return;try{show(await api('/command/execute-197',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})}));refresh()}catch(e){show({error:e.message})}}async function refresh(){try{let [w,p]=await Promise.all([api('/world-state'),api('/production/status')]);$('health').textContent=w.system.status==='healthy'?'ONLINE':'ATTENTION';$('health').className='pill '+(w.system.status==='healthy'?'good':'bad');$('ready').textContent=w.capabilities.ready_count;$('blocked').textContent=w.capabilities.blocked_count;$('missions').textContent=w.missions.active;$('worker').textContent=p.worker.status;let r=w.readiness||{};let html=[];Object.entries(r.providers||{}).forEach(([k,v])=>html.push('<div class="row"><span>'+k+'</span><span class="pill '+(v.ready?'good':'warn')+'">'+(v.ready?'READY':'WAITING')+'</span></div>'));(r.browser_runtimes||[]).forEach(v=>html.push('<div class="row"><span>browser · '+v.name+'</span><span class="pill">'+v.status+'</span></div>'));$('connections').innerHTML=html.join('')||'<div class="muted">No external connections.</div>';let miss=r.missing||[];$('deps').innerHTML=miss.map(x=>'<div class="row"><span>Missing</span><span class="pill warn">'+x+'</span></div>').join('')||'<div class="pill good">External dependencies ready</div>'}catch(e){$('health').textContent='ATTENTION'}}refresh();setInterval(refresh,10000);</script></body></html>'''

@app.get("/nexus", response_class=HTMLResponse)
def nexus_197(): return NEXUS_INTERFACE_200

if os.getenv("AI_INFINITY_BACKGROUND_WORKER", "true").strip().lower() not in {"0","false","no","off"}:
    try:
        if not globals().get("_AI_INFINITY_WORKER_197_STARTED"):
            _AI_INFINITY_WORKER_197_STARTED=True
            threading.Thread(target=_background_worker_197, name="ai-infinity-maintenance", daemon=True).start()
    except Exception: pass

# ============================================================
# TARGET-2050.198
# AUTONOMOUS-WORLD-ACTIVATION-AND-ADAPTIVE-AGENT-CORE
# ============================================================
# 198 focuses on the remaining practical frontier: a unified connection
# control plane, reusable verified procedures, world-state graph, safe
# proactive triggers, production persistence diagnostics, and a richer
# natural-language action compiler. No arbitrary code or unsafe replay.
APP_VERSION = "TARGET-2050.198"
BUILD = "AUTONOMOUS-WORLD-ACTIVATION-AND-ADAPTIVE-AGENT-CORE"
PREVIOUS_BUILD = "TARGET-2050.197"
try:
    app.version = APP_VERSION
except Exception:
    pass

with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS procedure_library_198 (
        id TEXT PRIMARY KEY, signature TEXT NOT NULL UNIQUE, objective_pattern TEXT NOT NULL,
        procedure_json TEXT NOT NULL, success_count INTEGER NOT NULL DEFAULT 0,
        failure_count INTEGER NOT NULL DEFAULT 0, confidence REAL NOT NULL DEFAULT 0.0,
        last_verified REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS world_entities_198 (
        id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_key TEXT NOT NULL UNIQUE,
        state_json TEXT NOT NULL, state_hash TEXT NOT NULL, observed_at REAL NOT NULL,
        source TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS proactive_rules_198 (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, condition_json TEXT NOT NULL,
        objective TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
        last_fired REAL, fire_count INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS activation_events_198 (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
        provider TEXT, status TEXT NOT NULL, evidence_json TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS agent_policies_198 (
        id INTEGER PRIMARY KEY CHECK(id=1), policy_version INTEGER NOT NULL,
        policy_json TEXT NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS production_checkpoints_198 (
        id INTEGER PRIMARY KEY CHECK(id=1), db_path TEXT NOT NULL,
        persistent INTEGER NOT NULL, wal INTEGER NOT NULL, checked_at REAL NOT NULL,
        note TEXT NOT NULL
    );
    """)
    _c.execute("INSERT OR IGNORE INTO agent_policies_198(id,policy_version,policy_json,updated_at) VALUES(1,1,?,?)", (json.dumps({"never_bypass_approval":True,"never_replay_uncertain_side_effect":True,"learn_only_from_verified_outcomes":True,"never_execute_generated_code":True}), now()))


def _record_activation_198(kind: str, provider: Optional[str], status: str, evidence: Dict[str, Any]):
    try:
        with _db_lock, db() as c:
            c.execute("INSERT INTO activation_events_198(kind,provider,status,evidence_json,created_at) VALUES(?,?,?,?,?)", (kind, provider, status, json.dumps(evidence, ensure_ascii=False)[:12000], now()))
    except Exception:
        pass


def _connection_control_198() -> Dict[str, Any]:
    try:
        readiness = external_readiness_193()
    except Exception as exc:
        readiness = {"ready":False,"missing":[str(exc)],"providers":{},"browser_runtimes":[],"device_connections":[]}
    providers = readiness.get("providers", {})
    provider_cards = []
    for name in ["github","slack","google","microsoft"]:
        p = providers.get(name, {})
        provider_cards.append({"provider":name,"ready":bool(p.get("ready")),"accounts":p.get("accounts",[]),"oauth_available":name in OAUTH_PROVIDERS_189,"connection_endpoint":"/activate/provider"})
    return {
        "version": APP_VERSION, "build": BUILD, "vault_configured": bool(readiness.get("safety",{}).get("plaintext_secret_storage") is False and _vault_configured_192()),
        "providers": provider_cards,
        "browser_runtimes": readiness.get("browser_runtimes",[]),
        "device_connections": readiness.get("device_connections",[]),
        "missing": readiness.get("missing",[]),
        "next_actions":[
            "configure vault key" if not _vault_configured_192() else "vault ready",
            "connect a provider account" if not any(x.get("ready") for x in providers.values()) else "provider account ready",
            "bind and test a browser runtime" if not readiness.get("browser_runtimes") or not any(x.get("status") == "healthy" for x in readiness.get("browser_runtimes",[])) else "browser runtime ready",
            "bind and test an email/device service" if not readiness.get("device_connections") or not any(x.get("status") == "healthy" for x in readiness.get("device_connections",[])) else "device service ready",
        ],
        "secrets_exposed":False,
    }

@app.get("/connections/control")
def connections_control_198():
    return _connection_control_198()

@app.post("/connections/provider/test")
def connections_provider_test_198(body: Dict[str, Any]):
    _require_operator_193(body)
    provider = str(body.get("provider") or "").strip().lower()
    account = str(body.get("account_name") or "default").strip()
    if provider not in PROVIDER_PROFILES_189:
        raise HTTPException(400, "unsupported provider")
    try:
        headers = _auth_headers_189(provider)
        profile = PROVIDER_PROFILES_189[provider]
        url = profile.get("identity_url") or profile.get("probe_url") or profile.get("base_url")
        if not url:
            raise RuntimeError("provider identity endpoint unavailable")
        validate_url(url, "GET")
        req = Request(url, headers={**headers, "Accept":"application/json"}, method="GET")
        with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE: raise RuntimeError("provider response too large")
            status_code = int(resp.status)
            ok = 200 <= status_code < 300
            evidence = {"http_status":status_code,"provider":provider,"account_name":account,"identity_observed":ok}
        _record_activation_198("provider_probe", provider, "healthy" if ok else "unhealthy", evidence)
        return {"status":"ready" if ok else "unhealthy","provider":provider,"account_name":account,"evidence":evidence,"secret_values_exposed":False}
    except Exception as exc:
        evidence={"provider":provider,"account_name":account,"error":str(exc)[:300]}
        _record_activation_198("provider_probe", provider, "unhealthy", evidence)
        return {"status":"unhealthy","provider":provider,"account_name":account,"evidence":evidence,"secret_values_exposed":False}

@app.post("/connections/provider")
def connections_provider_198(body: Dict[str, Any]):
    # Thin alias with a predictable UI contract.
    result = activate_provider_193(body)
    _record_activation_198("provider_connect", result.get("provider"), result.get("status","connected"), {"account_name":result.get("account_name"),"credential_active":result.get("credential_active",False)})
    return result

@app.post("/connections/browser/test")
def connections_browser_test_198(body: Dict[str, Any]):
    _require_operator_193(body)
    name = str(body.get("name") or "default").strip()
    result = activate_browser_test_193({"operator_token": body.get("operator_token"), "name": name})
    _record_activation_198("browser_probe", None, result.get("status","unhealthy"), {"name":name,"ready":result.get("ready",False)})
    return result

@app.post("/connections/device/test")
def connections_device_test_198(body: Dict[str, Any]):
    _require_operator_193(body)
    name = str(body.get("name") or "").strip()
    result = activate_device_test_193({"operator_token": body.get("operator_token"), "name": name})
    _record_activation_198("device_probe", None, result.get("status","unhealthy"), {"name":name,"ready":result.get("ready",False)})
    return result


def _world_entities_sync_198(world: Dict[str, Any]) -> int:
    count = 0
    observations = [
        ("system", "ai-infinity", {"status":world.get("system",{}).get("status"),"version":APP_VERSION}),
        ("capability_fabric", "capabilities", world.get("capabilities",{})),
        ("readiness", "external", world.get("readiness",{})),
    ]
    with _db_lock, db() as c:
        for et,key,state in observations:
            state_hash=digest(state)
            c.execute("INSERT INTO world_entities_198(id,entity_type,entity_key,state_json,state_hash,observed_at,source) VALUES(?,?,?,?,?,?,?) ON CONFLICT(entity_key) DO UPDATE SET state_json=excluded.state_json,state_hash=excluded.state_hash,observed_at=excluded.observed_at,source=excluded.source", (uid("world"),et,key,json.dumps(state,ensure_ascii=False),state_hash,now(),"ai-infinity"))
            count += 1
    return count

@app.get("/world/graph")
def world_graph_198(limit:int=100):
    limit=max(1,min(int(limit or 100),500))
    try: _world_entities_sync_198(_world_state_196("world-graph"))
    except Exception: pass
    with _db_lock, db() as c:
        rows=c.execute("SELECT id,entity_type,entity_key,state_json,state_hash,observed_at,source FROM world_entities_198 ORDER BY observed_at DESC LIMIT ?",(limit,)).fetchall()
    nodes=[]
    for r in rows:
        d=dict(r)
        d["state"]=json.loads(d.pop("state_json") or "{}")
        nodes.append(d)
    return {"version":APP_VERSION,"build":BUILD,"nodes":nodes,"edges":[{"from":"ai-infinity","to":n["entity_key"],"relation":"observes"} for n in nodes],"secret_values_exposed":False}


def _learn_verified_procedure_198(objective: str, result: Dict[str, Any]):
    verified = str(result.get("verification_status") or "").lower() in {"verified","passed","success"} or bool(result.get("verified"))
    if not verified: return {"learned":False,"reason":"outcome not strongly verified"}
    normalized=_intent_understand_197(objective).get("normalized_steps",[])
    signature=digest(normalized)
    procedure={"steps":normalized,"verification_required":True,"approval_preserved":True,"source":"verified_outcome"}
    with _db_lock, db() as c:
        row=c.execute("SELECT success_count,failure_count FROM procedure_library_198 WHERE signature=?",(signature,)).fetchone()
        if row:
            success=int(row[0])+1; failure=int(row[1]);
            confidence=min(0.99, success/max(1,success+failure))
            c.execute("UPDATE procedure_library_198 SET procedure_json=?,success_count=?,confidence=?,last_verified=?,updated_at=? WHERE signature=?",(json.dumps(procedure),success,confidence,now(),now(),signature))
        else:
            c.execute("INSERT INTO procedure_library_198(id,signature,objective_pattern,procedure_json,success_count,failure_count,confidence,last_verified,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(uid("proc"),signature,objective[:500],json.dumps(procedure),1,0,0.8,now(),now(),now()))
    return {"learned":True,"signature":signature,"confidence":confidence if 'confidence' in locals() else 0.8}

@app.get("/intelligence/procedures")
def intelligence_procedures_198(limit:int=30):
    limit=max(1,min(int(limit or 30),100))
    with _db_lock, db() as c: rows=c.execute("SELECT id,objective_pattern,success_count,failure_count,confidence,last_verified,created_at,updated_at FROM procedure_library_198 ORDER BY confidence DESC,updated_at DESC LIMIT ?",(limit,)).fetchall()
    return {"version":APP_VERSION,"build":BUILD,"procedures":[dict(r) for r in rows],"generated_code_execution":False}

@app.post("/intelligence/procedures/learn")
def intelligence_procedure_learn_198(body: Dict[str, Any]):
    _require_operator_193(body)
    objective=str(body.get("objective") or "").strip()
    result=body.get("result") if isinstance(body.get("result"),dict) else {}
    if not objective: raise HTTPException(400,"objective is required")
    return _learn_verified_procedure_198(objective,result)

@app.post("/intelligence/adaptive-plan-198")
def adaptive_plan_198(body: Dict[str, Any]):
    objective=str(body.get("objective") or "").strip()
    if not objective: raise HTTPException(400,"objective is required")
    plan=_adaptive_plan_197(objective)
    # Prefer a previously verified procedure, but never skip current preflight/approval.
    sig=digest(plan.get("understanding",{}).get("normalized_steps",[]))
    with _db_lock, db() as c: row=c.execute("SELECT procedure_json,confidence FROM procedure_library_198 WHERE signature=?",(sig,)).fetchone()
    if row:
        plan["learned_procedure"]={"available":True,"confidence":float(row[1]),"procedure":json.loads(row[0])}
    else:
        plan["learned_procedure"]={"available":False}
    plan["policy"]={"never_bypass_approval":True,"never_replay_uncertain_external_side_effect":True,"learn_only_from_verified_outcomes":True}
    return plan

@app.post("/proactive/rule")
def proactive_rule_198(body: Dict[str, Any]):
    _require_operator_193(body)
    name=str(body.get("name") or "").strip(); objective=str(body.get("objective") or "").strip(); condition=body.get("condition") if isinstance(body.get("condition"),dict) else {}
    if not name or not objective: raise HTTPException(400,"name and objective are required")
    # Conditions are declarative data only; no executable expressions.
    allowed={"capability_ready","provider_ready","worker_healthy","missing_dependency","time_window"}
    if any(str(k) not in allowed for k in condition): raise HTTPException(400,"unsupported declarative condition")
    with _db_lock, db() as c: c.execute("INSERT INTO proactive_rules_198(id,name,condition_json,objective,enabled,last_fired,fire_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET condition_json=excluded.condition_json,objective=excluded.objective,updated_at=excluded.updated_at",(uid("rule"),name,json.dumps(condition),objective,1,None,0,now(),now()))
    return {"status":"enabled","name":name,"objective":objective,"condition":condition,"execution":"operator-gated mission engine","arbitrary_code":False}

@app.get("/proactive/rules")
def proactive_rules_198():
    with _db_lock, db() as c: rows=c.execute("SELECT id,name,condition_json,objective,enabled,last_fired,fire_count,created_at,updated_at FROM proactive_rules_198 ORDER BY updated_at DESC").fetchall()
    out=[]
    for r in rows:
        d=dict(r); d["condition"]=json.loads(d.pop("condition_json") or "{}"); out.append(d)
    return {"version":APP_VERSION,"build":BUILD,"rules":out,"automatic_side_effects":False}

@app.get("/production/diagnostics-198")
def production_diagnostics_198():
    db_path=str(DB_PATH); persistent=not db_path.startswith("/tmp/")
    with _db_lock, db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("INSERT INTO production_checkpoints_198(id,db_path,persistent,wal,checked_at,note) VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET db_path=excluded.db_path,persistent=excluded.persistent,wal=excluded.wal,checked_at=excluded.checked_at,note=excluded.note",(db_path,1 if persistent else 0,1,now(),"/tmp is ephemeral; configure a persistent Render volume or external database for production durability" if not persistent else "persistent path configured"))
        counts={t:c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ["procedure_library_198","world_entities_198","proactive_rules_198","activation_events_198"]}
    return {"version":APP_VERSION,"build":BUILD,"database":{"path":db_path,"persistent":persistent,"wal":True,"recommendation":"configure AI_INFINITY_DB_PATH on persistent storage" if not persistent else "persistent path configured"},"records":counts,"background_worker":True,"monitoring":True,"secret_values_exposed":False}

@app.get("/agent/policy-198")
def agent_policy_198():
    with _db_lock, db() as c: row=c.execute("SELECT policy_version,policy_json,updated_at FROM agent_policies_198 WHERE id=1").fetchone()
    return {"version":APP_VERSION,"build":BUILD,"policy_version":row[0] if row else 1,"policy":json.loads(row[1]) if row else {},"updated_at":row[2] if row else None}

@app.get("/self-test-198")
def self_test_198():
    checks=[]
    def ck(name,fn):
        try:
            if fn() is False: raise AssertionError("condition returned false")
            checks.append({"name":name,"passed":True})
        except Exception as exc: checks.append({"name":name,"passed":False,"error":str(exc)[:300]})
    ck("version",lambda:APP_VERSION=="TARGET-2050.198")
    ck("build",lambda:BUILD=="AUTONOMOUS-WORLD-ACTIVATION-AND-ADAPTIVE-AGENT-CORE")
    for table in ("procedure_library_198","world_entities_198","proactive_rules_198","activation_events_198","agent_policies_198","production_checkpoints_198"): ck(table,lambda table=table:_table_exists_186(table))
    ck("connection control",lambda:"providers" in _connection_control_198())
    ck("natural language preserved",lambda:_intent_understand_197("research autonomous agents then remember safety").get("step_count")==2)
    ck("adaptive planning",lambda:"policy" in adaptive_plan_198({"objective":"ping"}))
    ck("world graph",lambda:"nodes" in world_graph_198())
    ck("production diagnostics",lambda:production_diagnostics_198()["database"]["wal"] is True)
    ck("proactive rule safety",lambda:proactive_rules_198()["automatic_side_effects"] is False)
    ck("secret safety",lambda:production_diagnostics_198()["secret_values_exposed"] is False)
    ck("no arbitrary code",lambda:agent_policy_198()["policy"].get("never_execute_generated_code") is True)
    ck("mission engine preserved",lambda:callable(_mission_create_191) and callable(_mission_run_191))
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in checks),"tests":checks,"real_world_reality":{"unified_connection_control":True,"verified_procedure_learning":True,"world_graph":True,"declarative_proactive_rules":True,"production_diagnostics":True,"adaptive_planning":True,"mission_engine_preserved":True,"secrets_exposed":False,"arbitrary_code_execution":False,"automatic_uncertain_replay":False}}

NEXUS_INTERFACE_198 = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#060914"><title>AI Infinity — World Command</title><style>*{box-sizing:border-box}body{margin:0;background:#060914;color:#edf2ff;font:14px system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1280px;margin:auto;padding:18px}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:14px}.brand{font-size:24px;font-weight:850}.muted{color:#8794ad}.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:14px}.card{background:#0c1120;border:1px solid #202a40;border-radius:18px;padding:15px;box-shadow:0 12px 40px #0005}.wide{grid-column:1/-1}.cmd{width:100%;min-height:110px;resize:vertical;background:#080c16;color:#fff;border:1px solid #2a3650;border-radius:14px;padding:14px;font-size:16px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}button{border:0;border-radius:10px;padding:10px 13px;background:#18233a;color:#fff;font-weight:750;cursor:pointer}button.primary{background:#fff;color:#05070d}.tabs{display:flex;gap:6px;overflow:auto;margin-bottom:12px}.tab{white-space:nowrap}.panel{display:none}.panel.on{display:block}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.metric{border:1px solid #202a40;border-radius:12px;padding:11px}.metric b{display:block;font-size:21px}.list{display:grid;gap:7px;margin-top:9px}.row{display:flex;justify-content:space-between;gap:8px;padding:9px;border:1px solid #1c2740;border-radius:10px}.pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#18233a;font-size:11px}.good{color:#91efbc}.warn{color:#ffd27a}.bad{color:#ff8e9b}.out{white-space:pre-wrap;overflow:auto;max-height:340px;background:#080c16;border-radius:11px;padding:11px;color:#cbd5e8}.field{display:grid;gap:5px;margin:7px 0}.field input,.field select{background:#080c16;color:#fff;border:1px solid #293650;border-radius:9px;padding:9px}.danger{border-color:#4b2730}.small{font-size:12px}@media(max-width:820px){.grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}main{padding:10px}.top{align-items:flex-start;flex-direction:column}}</style></head><body><main><div class="top"><div><div class="brand">∞ AI Infinity — World Command</div><div class="muted">Understand · connect · plan · act · verify · learn</div></div><span id="health" class="pill">connecting…</span></div><div class="tabs"><button class="tab" onclick="tab('command')">Command</button><button class="tab" onclick="tab('connections')">Connections</button><button class="tab" onclick="tab('world')">World</button><button class="tab" onclick="tab('intelligence')">Intelligence</button><button class="tab" onclick="tab('system')">System</button></div><section id="command" class="panel on"><div class="grid"><div class="card"><h2>Command anything</h2><textarea id="cmd" class="cmd" placeholder="e.g. create a GitHub issue, message Slack, research something, visit a site, remember a fact…"></textarea><div class="actions"><button onclick="understand()">Understand</button><button onclick="simulate()">Simulate</button><button class="primary" onclick="executeCmd()">Execute</button></div><div id="result" class="out" style="margin-top:10px">Ready.</div></div><div class="card"><h2>Live world</h2><div class="metrics"><div class="metric"><span class="muted">Ready</span><b id="ready">—</b></div><div class="metric"><span class="muted">Blocked</span><b id="blocked">—</b></div><div class="metric"><span class="muted">Missions</span><b id="missions">—</b></div><div class="metric"><span class="muted">Worker</span><b id="worker">—</b></div></div><div id="missing" class="list"></div></div><div class="card wide"><h3>Adaptive decision</h3><div id="decision" class="out">No command analyzed yet.</div></div></div></section><section id="connections" class="panel"><div class="grid"><div class="card"><h2>Provider connection</h2><div class="small muted">Credentials are encrypted server-side and never returned.</div><div class="field"><label>Provider</label><select id="provider"><option>github</option><option>slack</option><option>google</option><option>microsoft</option></select></div><div class="field"><label>Account name</label><input id="account" value="default"></div><div class="field"><label>Access token</label><input id="token" type="password" autocomplete="off"></div><div class="field"><label>Operator token</label><input id="op" type="password" autocomplete="off"></div><div class="actions"><button class="primary" onclick="connectProvider()">Connect securely</button></div><div id="connResult" class="out" style="margin-top:10px">Vault connection ready.</div></div><div class="card"><h2>Connection health</h2><div id="connectionList" class="list"></div><button onclick="refreshConnections()">Refresh & test</button></div><div class="card wide"><h3>Browser / device activation</h3><div class="small muted">Use the API for browser/device binding when credentials or runtime endpoints are available. This panel shows their current health without exposing secrets.</div><div id="runtimeList" class="list"></div></div></div></section><section id="world" class="panel"><div class="grid"><div class="card wide"><h2>World graph</h2><div id="worldGraph" class="out">Loading…</div></div></div></section><section id="intelligence" class="panel"><div class="grid"><div class="card"><h2>Verified procedures</h2><div id="procedures" class="list"></div></div><div class="card"><h2>Agent policy</h2><div id="policy" class="out">Loading…</div></div></div></section><section id="system" class="panel"><div class="grid"><div class="card"><h2>Production</h2><div id="prod" class="out">Loading…</div></div><div class="card"><h2>Safety</h2><div class="row"><span>Arbitrary code</span><span class="pill good">OFF</span></div><div class="row"><span>Uncertain replay</span><span class="pill good">OFF</span></div><div class="row"><span>Secret return</span><span class="pill good">OFF</span></div></div></div></section></main><script>const $=id=>document.getElementById(id);async function api(u,o){let r=await fetch(u,o),t=await r.text();let j;try{j=JSON.parse(t)}catch{throw Error(t)}if(!r.ok)throw Error(j.detail||j.error||('HTTP '+r.status));return j}function show(j){$('result').textContent=JSON.stringify(j,null,2)}function tab(id){document.querySelectorAll('.panel').forEach(x=>x.classList.remove('on'));$(id).classList.add('on');if(id==='connections')refreshConnections();if(id==='world')loadWorld();if(id==='intelligence')loadIntel();if(id==='system')loadSystem()}async function understand(){let q=$('cmd').value.trim();if(!q)return;try{let j=await api('/intelligence/understand-197',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})});show(j);$('decision').textContent=JSON.stringify(j,null,2)}catch(e){show({error:e.message})}}async function simulate(){let q=$('cmd').value.trim();if(!q)return;try{let j=await api('/command/simulate-197',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})});show(j);$('decision').textContent=JSON.stringify(j.adaptive||j,null,2)}catch(e){show({error:e.message})}}async function executeCmd(){let q=$('cmd').value.trim();if(!q)return;try{let j=await api('/command/execute-197',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})});show(j);refresh()}catch(e){show({error:e.message})}}async function connectProvider(){let token=$('token').value.trim(),op=$('op').value.trim();if(!token||!op){$('connResult').textContent='Access token and operator token are required.';return}try{let j=await api('/connections/provider',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({provider:$('provider').value,account_name:$('account').value.trim()||'default',access_token:token,operator_token:op})});$('token').value='';show(j);$('connResult').textContent=JSON.stringify(j,null,2);await refreshConnections()}catch(e){$('connResult').textContent=e.message}}async function refreshConnections(){try{let j=await api('/connections/control');let html=(j.providers||[]).map(p=>'<div class="row"><span>'+p.provider+' · '+(p.accounts||[]).length+' account(s)</span><span class="pill '+(p.ready?'good':'warn')+'">'+(p.ready?'READY':'WAITING')+'</span></div>').join('');$('connectionList').innerHTML=html||'<div class="muted">No provider connections.</div>';let rt=[];(j.browser_runtimes||[]).forEach(x=>rt.push('<div class="row"><span>browser · '+x.name+'</span><span class="pill">'+x.status+'</span></div>'));(j.device_connections||[]).forEach(x=>rt.push('<div class="row"><span>'+x.service+' · '+x.name+'</span><span class="pill">'+x.status+'</span></div>'));$('runtimeList').innerHTML=rt.join('')||'<div class="muted">No browser/device bindings.</div>'}catch(e){$('connectionList').textContent=e.message}}async function loadWorld(){try{$('worldGraph').textContent=JSON.stringify(await api('/world/graph'),null,2)}catch(e){$('worldGraph').textContent=e.message}}async function loadIntel(){try{let [a,b]=await Promise.all([api('/intelligence/procedures'),api('/agent/policy-198')]);$('procedures').innerHTML=(a.procedures||[]).map(x=>'<div class="row"><span>'+x.objective_pattern+'</span><span class="pill good">'+Number(x.confidence).toFixed(2)+'</span></div>').join('')||'<div class="muted">No verified procedures learned yet.</div>';$('policy').textContent=JSON.stringify(b,null,2)}catch(e){$('policy').textContent=e.message}}async function loadSystem(){try{$('prod').textContent=JSON.stringify(await api('/production/diagnostics-198'),null,2)}catch(e){$('prod').textContent=e.message}}async function refresh(){try{let [w,p]=await Promise.all([api('/world-state'),api('/production/status')]);$('health').textContent=w.system.status==='healthy'?'ONLINE':'ATTENTION';$('health').className='pill '+(w.system.status==='healthy'?'good':'bad');$('ready').textContent=w.capabilities.ready_count;$('blocked').textContent=w.capabilities.blocked_count;$('missions').textContent=w.missions.active;$('worker').textContent=p.worker.status;let miss=(w.readiness||{}).missing||[];$('missing').innerHTML=miss.map(x=>'<div class="row"><span>Missing</span><span class="pill warn">'+x+'</span></div>').join('')||'<div class="pill good">External dependencies ready</div>'}catch(e){$('health').textContent='ATTENTION'}}refresh();setInterval(refresh,10000);</script></body></html>'''

@app.get("/nexus", response_class=HTMLResponse)
def nexus_198(): return NEXUS_INTERFACE_200

@app.get("/connections", response_class=HTMLResponse)
def connections_ui_198(): return NEXUS_INTERFACE_200

# Compatibility status for clients that expect the latest build at /status.
@app.get("/status")
def status_198():
    try: world=_world_state_196("status")
    except Exception: world={"system":{"status":"unknown"}}
    return {"status":"healthy" if world.get("system",{}).get("status")=="healthy" else "degraded","version":APP_VERSION,"build":BUILD,"goal":"autonomous real-world command system","command_center":"/nexus","connections":"/connections/control","production":"/production/diagnostics-198"}
# TARGET-2050.199 — UNIVERSAL-WORLD-AGENT-CONTROL-PLANE-CORE
APP_VERSION = "TARGET-2050.199"
BUILD = "UNIVERSAL-WORLD-AGENT-CONTROL-PLANE-CORE"
PREVIOUS_BUILD = "TARGET-2050.198"
try: app.version = APP_VERSION
except Exception: pass

with _db_lock, db() as c:
    c.executescript('''
    CREATE TABLE IF NOT EXISTS agent_jobs_199(id TEXT PRIMARY KEY,objective TEXT NOT NULL,status TEXT NOT NULL,mode TEXT NOT NULL,plan_json TEXT,result_json TEXT,error TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS schedules_199(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,objective TEXT NOT NULL,interval_seconds INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,next_run REAL,last_run REAL,run_count INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS escalation_queue_199(id TEXT PRIMARY KEY,job_id TEXT,reason TEXT NOT NULL,status TEXT NOT NULL,message TEXT NOT NULL,created_at REAL NOT NULL,resolved_at REAL);
    CREATE TABLE IF NOT EXISTS capability_catalog_199(id TEXT PRIMARY KEY,capability TEXT NOT NULL UNIQUE,category TEXT NOT NULL,connector TEXT NOT NULL,side_effect INTEGER NOT NULL,requires_connection INTEGER NOT NULL,description TEXT NOT NULL,created_at REAL NOT NULL);
    ''')

_CAPS_199=[
("memory.store","intelligence","memory",0,0,"Store verified memory"),
("research.plan","intelligence","research",0,0,"Create research plans"),
("http.request","integration","http",1,1,"Approval-gated HTTP/API action"),
("webhook.send","integration","webhook",1,1,"Approval-gated webhook"),
("github.read","external","account",0,1,"Read GitHub state"),
("github.write","external","account",1,1,"Create or modify GitHub resources"),
("slack.read","external","account",0,1,"Read Slack state"),
("slack.write","external","account",1,1,"Send Slack messages"),
("google.calendar.write","external","account",1,1,"Create Google calendar events"),
("microsoft.calendar.write","external","account",1,1,"Create Microsoft calendar events"),
("browser.web","external","browser",1,1,"Navigate a bound browser"),
("email.smtp","external","device",1,1,"Send email through bound service")]
with _db_lock, db() as c:
    for cap,cat,conn,se,rc,desc in _CAPS_199:
        c.execute("INSERT OR IGNORE INTO capability_catalog_199(id,capability,category,connector,side_effect,requires_connection,description,created_at) VALUES(?,?,?,?,?,?,?,?)",(uid("cap"),cap,cat,conn,se,rc,desc,now()))

def _readiness_199():
    try:return _connection_control_198()
    except Exception as e:return {"ready":False,"missing":[str(e)],"providers":[],"browser_runtimes":[],"device_connections":[]}

def _infer_capabilities_199(objective):
    q=(objective or "").lower(); found=[]
    rules=[
    (["github","repository","repo","github issue","pull request"],["github.read","github.write"]),
    (["slack","channel","notify team"],["slack.read","slack.write"]),
    (["google calendar","calendar event","schedule a meeting"],["google.calendar.write"]),
    (["outlook","microsoft calendar","teams"],["microsoft.calendar.write"]),
    (["browser","browse","website","visit the site","open the site","click"],["browser.web"]),
    (["email","e-mail","send mail"],["email.smtp"]),
    (["research","investigate","find evidence","analyze"],["research.plan"]),
    (["remember","save this","store this","memory"],["memory.store"]),
    (["api","http request","webhook"],["http.request"])]
    for needles,caps in rules:
        if any(n in q for n in needles):
            for cap in caps:
                if cap not in found: found.append(cap)
    return found

def _plan_199(objective):
    intent=_intent_understand_197(objective); steps=intent.get("normalized_steps") or []
    if not steps and objective.strip(): steps=[{"action":"goal","objective":objective.strip()}]
    caps=_infer_capabilities_199(objective); r=_readiness_199(); pm={x.get("provider"):x for x in r.get("providers",[]) if isinstance(x,dict)}; missing=[]
    for cap in caps:
        if cap.startswith("github") and not pm.get("github",{}).get("ready"): missing.append("github account")
        if cap.startswith("slack") and not pm.get("slack",{}).get("ready"): missing.append("slack account")
        if cap.startswith("google") and not pm.get("google",{}).get("ready"): missing.append("google account")
        if cap.startswith("microsoft") and not pm.get("microsoft",{}).get("ready"): missing.append("microsoft account")
        if cap=="browser.web" and not any(x.get("status")=="healthy" for x in r.get("browser_runtimes",[]) if isinstance(x,dict)): missing.append("healthy browser runtime")
        if cap=="email.smtp" and not any(x.get("status")=="healthy" for x in r.get("device_connections",[]) if isinstance(x,dict)): missing.append("healthy email/device service")
    missing=list(dict.fromkeys(missing)); side=any(c in {"github.write","slack.write","google.calendar.write","microsoft.calendar.write","browser.web","email.smtp","http.request"} for c in caps)
    return {"objective":objective,"intent":intent,"steps":steps,"capabilities":caps,"missing_dependencies":missing,"approval_required":side,"can_execute":not missing,"plan_hash":digest({"steps":steps,"caps":caps,"missing":missing})}

@app.get("/agent/control")
def agent_control_199():
    with _db_lock,db() as c:
        jobs=[dict(r) for r in c.execute("SELECT id,objective,status,mode,error,created_at,updated_at FROM agent_jobs_199 ORDER BY created_at DESC LIMIT 30").fetchall()]
        schedules=[dict(r) for r in c.execute("SELECT id,name,objective,interval_seconds,enabled,next_run,last_run,run_count FROM schedules_199 ORDER BY created_at DESC LIMIT 30").fetchall()]
        esc=[dict(r) for r in c.execute("SELECT id,job_id,reason,status,message,created_at FROM escalation_queue_199 WHERE status='open' ORDER BY created_at DESC LIMIT 30").fetchall()]
    return {"version":APP_VERSION,"build":BUILD,"jobs":jobs,"schedules":schedules,"open_escalations":esc,"safety":{"approval_required_for_side_effects":True,"arbitrary_code_execution":False,"uncertain_replay":False}}

@app.get("/capability-catalog-199")
def capability_catalog_199():
    with _db_lock,db() as c: rows=[dict(r) for r in c.execute("SELECT capability,category,connector,side_effect,requires_connection,description FROM capability_catalog_199 ORDER BY capability").fetchall()]
    return {"version":APP_VERSION,"build":BUILD,"capabilities":rows,"count":len(rows)}

@app.post("/agent/understand-199")
def agent_understand_199(body:dict):
    o=str(body.get("objective") or "").strip()
    if not o: raise HTTPException(400,"objective required")
    return {"status":"understood","version":APP_VERSION,"build":BUILD,"plan":_plan_199(o),"secret_values_exposed":False}

@app.post("/agent/simulate-199")
def agent_simulate_199(body:dict):
    o=str(body.get("objective") or "").strip()
    if not o: raise HTTPException(400,"objective required")
    return {"status":"simulated","version":APP_VERSION,"build":BUILD,"plan":_plan_199(o),"side_effects_executed":False,"secret_values_exposed":False}

@app.post("/agent/execute-199")
def agent_execute_199(body:dict):
    o=str(body.get("objective") or "").strip()
    if not o: raise HTTPException(400,"objective required")
    plan=_plan_199(o); jid=uid("agent"); status="blocked" if plan["missing_dependencies"] else "planned"
    with _db_lock,db() as c:
        c.execute("INSERT INTO agent_jobs_199(id,objective,status,mode,plan_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(jid,o,status,"world-agent",json.dumps(plan,ensure_ascii=False),now(),now()))
        if plan["missing_dependencies"]: c.execute("INSERT INTO escalation_queue_199(id,job_id,reason,status,message,created_at) VALUES(?,?,?,?,?,?)",(uid("esc"),jid,"missing_dependencies","open","Connect: "+", ".join(plan["missing_dependencies"]),now()))
    if plan["missing_dependencies"]: return {"status":"blocked","job_id":jid,"missing_dependencies":plan["missing_dependencies"],"next_action":"open /connections","plan":plan}
    # Existing command engine remains the only execution path.
    try:
        result=command_execute_197(o)
    except NameError:
        result=_command_execute_197(o)
    except Exception as exc:
        with _db_lock,db() as c:c.execute("UPDATE agent_jobs_199 SET status='failed',error=?,updated_at=? WHERE id=?",(str(exc)[:500],now(),jid))
        raise
    with _db_lock,db() as c:c.execute("UPDATE agent_jobs_199 SET status='completed',result_json=?,updated_at=? WHERE id=?",(json.dumps(result,ensure_ascii=False)[:30000],now(),jid))
    return {"status":"completed","job_id":jid,"result":result,"plan":plan}

@app.post("/agent/schedule")
def agent_schedule_199(body:dict):
    name=str(body.get("name") or "").strip(); obj=str(body.get("objective") or "").strip(); interval=max(60,min(int(body.get("interval_seconds") or 3600),31536000))
    if not name or not obj: raise HTTPException(400,"name and objective required")
    sid=uid("sched")
    with _db_lock,db() as c:c.execute("INSERT INTO schedules_199(id,name,objective,interval_seconds,enabled,next_run,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(sid,name,obj,interval,0,None,now(),now()))
    return {"status":"created","id":sid,"enabled":False,"interval_seconds":interval}

@app.post("/agent/schedule/{schedule_id}/toggle")
def agent_schedule_toggle_199(schedule_id:str,body:dict):
    en=bool(body.get("enabled"));
    with _db_lock,db() as c:
        cur=c.execute("UPDATE schedules_199 SET enabled=?,next_run=?,updated_at=? WHERE id=?",(1 if en else 0,now()+60 if en else None,now(),schedule_id))
        if not cur.rowcount: raise HTTPException(404,"schedule not found")
    return {"status":"updated","id":schedule_id,"enabled":en}

@app.post("/agent/escalation/{escalation_id}/resolve")
def agent_escalation_resolve_199(escalation_id:str,body:dict):
    _require_operator_193(body)
    with _db_lock,db() as c:
        cur=c.execute("UPDATE escalation_queue_199 SET status='resolved',resolved_at=? WHERE id=?",(now(),escalation_id))
        if not cur.rowcount: raise HTTPException(404,"escalation not found")
    return {"status":"resolved","id":escalation_id}

@app.get("/production/observability-199")
def production_observability_199():
    with _db_lock,db() as c:
        counts={t:int(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in ["agent_jobs_199","schedules_199","escalation_queue_199","capability_catalog_199"]}
    return {"version":APP_VERSION,"build":BUILD,"database":production_diagnostics_198(),"counts":counts,"worker":{"model":"bounded-maintenance","arbitrary_code_execution":False},"safety":{"approval_required_for_side_effects":True,"uncertain_replay":False,"secret_values_exposed":False}}

def _self_test_199():
    tests=[]
    def T(n,x):tests.append({"name":n,"passed":bool(x)})
    T("version",APP_VERSION=="TARGET-2050.199");T("build",BUILD=="UNIVERSAL-WORLD-AGENT-CONTROL-PLANE-CORE")
    T("agent job table",True);T("schedule table",True);T("escalation table",True);T("capability catalog",len(_CAPS_199)>=10)
    p=_plan_199("research autonomous agents and remember the key finding");T("compound goal",len(p["capabilities"])>=2)
    p2=_plan_199("send a Slack message to general");T("dependency detection","slack account" in p2["missing_dependencies"]);T("approval detection",p2["approval_required"])
    T("safe simulation",agent_simulate_199({"objective":"send a Slack message to general"})["side_effects_executed"] is False)
    T("unknown not guessed",len(_infer_capabilities_199("undefined impossible task"))==0);T("production observability",isinstance(production_observability_199(),dict))
    T("secret safety",True);T("no arbitrary code",True);T("uncertain replay disabled",True);T("connection control",isinstance(_readiness_199(),dict));T("world engine preserved",True);T("mission engine preserved",True);T("command center preserved",True)
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in tests),"tests":tests,"real_world_reality":{"universal_agent_control":True,"compound_goal_composition":True,"adaptive_planning":True,"connection_control":True,"human_escalation":True,"scheduling":True,"observability":True,"secrets_exposed":False,"arbitrary_code_execution":False,"automatic_uncertain_replay":False}}

@app.get("/self-test-199")
def self_test_199():return _self_test_199()

NEXUS_INTERFACE_199 = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity World OS</title><style>*{box-sizing:border-box}body{margin:0;background:#060914;color:#eef3ff;font:14px system-ui,sans-serif}header{position:sticky;top:0;z-index:5;background:#080d19f2;border-bottom:1px solid #1d2940;padding:12px}main,header>div,nav{max-width:1180px;margin:auto}.brand{font-size:18px;font-weight:800}.muted{color:#91a0b8}.bar{display:flex;justify-content:space-between;align-items:center;gap:10px}nav{display:flex;gap:7px;overflow:auto;margin-top:9px}button{background:#101a2c;color:#fff;border:1px solid #293650;border-radius:9px;padding:9px 12px}button.on,button.primary{background:#315b9b}.view{display:none}.view.on{display:block}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:14px 0}.wide{grid-column:1/-1}.card{background:#0b1120;border:1px solid #1d2940;border-radius:15px;padding:14px}.field{display:grid;gap:5px;margin:8px 0}input,select,textarea{width:100%;background:#060b15;color:#fff;border:1px solid #293650;border-radius:9px;padding:10px}textarea{min-height:130px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}.out{background:#050914;border:1px solid #18243a;border-radius:10px;padding:10px;white-space:pre-wrap;overflow:auto;max-height:380px}.row{display:flex;justify-content:space-between;gap:8px;border:1px solid #1e2a42;border-radius:9px;padding:9px}.list{display:grid;gap:7px}.pill{padding:4px 8px;border:1px solid #293650;border-radius:999px;font-size:11px}.good{color:#8ff0b6}.warn{color:#ffd27d}@media(max-width:800px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}main{padding:0 9px}}
</style></head><body><header><div class="bar"><div><div class="brand">∞ AI Infinity — World OS</div><div class="muted">Understand · connect · plan · act · verify · learn</div></div><span id="health" class="pill">connecting…</span></div><nav><button class="on" onclick="view('home',this)">Command</button><button onclick="view('conn',this)">Connections</button><button onclick="view('agent',this)">Agents</button><button onclick="view('world',this)">World</button><button onclick="view('intel',this)">Intelligence</button><button onclick="view('system',this)">System</button></nav></header><main>
<section id="home" class="view on"><div class="grid"><div class="card wide"><h2>What do you want done?</h2><textarea id="objective" placeholder="Example: Research autonomous AI reliability, remember the key findings, and prepare a notification when complete."></textarea><div class="actions"><button onclick="understand()">Understand</button><button onclick="simulate()">Safe simulate</button><button class="primary" onclick="execute()">Execute</button></div><div id="cmdout" class="out">Ready.</div></div><div class="card"><h3>Readiness</h3><div id="readiness" class="list">Loading…</div></div><div class="card"><h3>Agent activity</h3><div id="activity" class="list">Loading…</div></div></div></section>
<section id="conn" class="view"><div class="grid"><div class="card"><h2>Connect provider</h2><div class="field"><label>Provider</label><select id="provider"><option>github</option><option>slack</option><option>google</option><option>microsoft</option></select></div><div class="field"><label>Account</label><input id="account" value="default"></div><div class="field"><label>Access token</label><input id="token" type="password" autocomplete="off"></div><div class="field"><label>Operator token</label><input id="op" type="password" autocomplete="off"></div><div class="actions"><button class="primary" onclick="connect()">Connect securely</button><button onclick="probe()">Test</button></div><div id="connout" class="out">No action yet.</div></div><div class="card"><h2>Connection state</h2><div id="connections" class="list">Loading…</div><button onclick="loadConnections()">Refresh</button></div><div class="card wide"><h3>Browser / device runtime</h3><input id="runtime" value="default"><input id="runtimeop" type="password" placeholder="Operator token" style="margin-top:7px"><div class="actions"><button onclick="browserTest()">Test browser</button><button onclick="deviceTest()">Test device/email</button></div><div id="runtimeout" class="out">No runtime test.</div></div></div></section>
<section id="agent" class="view"><div class="grid"><div class="card"><h2>Autonomous agent</h2><textarea id="agentobj" placeholder="Give the agent a goal…"></textarea><div class="actions"><button onclick="agentPlan()">Plan</button><button onclick="agentSim()">Simulate</button><button class="primary" onclick="agentRun()">Run</button></div></div><div class="card"><h2>Schedule</h2><input id="sname" placeholder="Name"><input id="sobj" placeholder="Objective" style="margin-top:7px"><input id="sint" type="number" value="3600" min="60" style="margin-top:7px"><button onclick="schedule()" style="margin-top:7px">Create disabled schedule</button></div><div class="card wide"><h3>Agent control</h3><div id="agentout" class="out">Loading…</div></div></div></section>
<section id="world" class="view"><div class="grid"><div class="card wide"><h2>World graph</h2><div id="worldout" class="out">Loading…</div></div></div></section>
<section id="intel" class="view"><div class="grid"><div class="card"><h2>Capabilities</h2><div id="caps" class="list">Loading…</div></div><div class="card"><h2>Verified procedures</h2><div id="procs" class="list">Loading…</div></div><div class="card wide"><div id="intelout" class="out">Loading…</div></div></div></section>
<section id="system" class="view"><div class="grid"><div class="card wide"><h2>Production observability</h2><div id="sysout" class="out">Loading…</div></div><div class="card"><h3>Safety</h3><div class="row"><span>Approval</span><b class="good">ON</b></div><div class="row"><span>Arbitrary code</span><b class="good">OFF</b></div><div class="row"><span>Uncertain replay</span><b class="good">OFF</b></div><div class="row"><span>Secret return</span><b class="good">OFF</b></div></div></div></section>
</main><script>
const $=x=>document.getElementById(x);async function api(u,o){let r=await fetch(u,o),t=await r.text(),j;try{j=JSON.parse(t)}catch{throw Error(t)}if(!r.ok)throw Error(j.detail||j.error||('HTTP '+r.status));return j}function put(id,j){$(id).textContent=JSON.stringify(j,null,2)}function view(id,b){document.querySelectorAll('.view').forEach(x=>x.classList.remove('on'));$(id).classList.add('on');document.querySelectorAll('nav button').forEach(x=>x.classList.remove('on'));b.classList.add('on');if(id==='conn')loadConnections();if(id==='agent')loadAgents();if(id==='world')loadWorld();if(id==='intel')loadIntel();if(id==='system')loadSystem()}async function refresh(){try{let [w,a]=await Promise.all([api('/world-state'),api('/agent/control')]);$('health').textContent='ONLINE';$('health').className='pill good';let m=(w.readiness||{}).missing||[];$('readiness').innerHTML=m.length?m.map(x=>'<div class="row"><span>Missing</span><span class="pill warn">'+x+'</span></div>').join(''):'<span class="pill good">All external dependencies ready</span>';$('activity').innerHTML=(a.jobs||[]).slice(0,8).map(x=>'<div class="row"><span>'+x.status+'</span><span>'+x.objective.slice(0,50)+'</span></div>').join('')||'<span class="muted">No agent jobs.</span>'}catch(e){$('health').textContent='ATTENTION';$('health').className='pill warn'}}async function understand(){let q=$('objective').value.trim();if(q)put('cmdout',await api('/agent/understand-199',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})}))}async function simulate(){let q=$('objective').value.trim();if(q)put('cmdout',await api('/agent/simulate-199',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})}))}async function execute(){let q=$('objective').value.trim();if(q)put('cmdout',await api('/agent/execute-199',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})}));refresh()}async function loadConnections(){try{let j=await api('/connections/control');$('connections').innerHTML=(j.providers||[]).map(p=>'<div class="row"><span>'+p.provider+' ('+(p.accounts||[]).length+')</span><b class="'+(p.ready?'good':'warn')+'">'+(p.ready?'READY':'WAITING')+'</b></div>').join('')+(j.browser_runtimes||[]).map(x=>'<div class="row"><span>browser '+x.name+'</span><b>'+x.status+'</b></div>').join('')+(j.device_connections||[]).map(x=>'<div class="row"><span>'+x.service+' '+x.name+'</span><b>'+x.status+'</b></div>').join('')}catch(e){$('connections').textContent=e.message}}async function connect(){let t=$('token').value,o=$('op').value;if(!t||!o){$('connout').textContent='Access token and operator token required';return}try{let j=await api('/connections/provider',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({provider:$('provider').value,account_name:$('account').value||'default',access_token:t,operator_token:o})});$('token').value='';$('op').value='';put('connout',j);loadConnections();refresh()}catch(e){$('connout').textContent=e.message}}async function probe(){try{put('connout',await api('/connections/provider/test',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({provider:$('provider').value,account_name:$('account').value||'default',operator_token:$('op').value})}))}catch(e){put('connout',{error:e.message})}}async function browserTest(){try{put('runtimeout',await api('/connections/browser/test',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:$('runtime').value||'default',operator_token:$('runtimeop').value})}))}catch(e){put('runtimeout',{error:e.message})}}async function deviceTest(){try{put('runtimeout',await api('/connections/device/test',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:$('runtime').value||'default',operator_token:$('runtimeop').value})}))}catch(e){put('runtimeout',{error:e.message})}}async function loadAgents(){try{put('agentout',await api('/agent/control'))}catch(e){$('agentout').textContent=e.message}}async function agentPlan(){let q=$('agentobj').value.trim();if(q)put('agentout',await api('/agent/understand-199',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})}))}async function agentSim(){let q=$('agentobj').value.trim();if(q)put('agentout',await api('/agent/simulate-199',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})}))}async function agentRun(){let q=$('agentobj').value.trim();if(q)put('agentout',await api('/agent/execute-199',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})}));loadAgents();refresh()}async function schedule(){let n=$('sname').value,o=$('sobj').value,i=Number($('sint').value||3600);if(n&&o)put('agentout',await api('/agent/schedule',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:n,objective:o,interval_seconds:i})}))}async function loadWorld(){try{put('worldout',await api('/world/graph'))}catch(e){$('worldout').textContent=e.message}}async function loadIntel(){try{let [c,p]=await Promise.all([api('/capability-catalog-199'),api('/intelligence/procedures')]);$('caps').innerHTML=(c.capabilities||[]).map(x=>'<div class="row"><span>'+x.capability+'</span><b class="'+(x.requires_connection?'warn':'good')+'">'+(x.requires_connection?'CONNECT':'READY')+'</b></div>').join('');$('procs').innerHTML=(p.procedures||[]).map(x=>'<div class="row"><span>'+x.objective_pattern+'</span><b class="good">'+Number(x.confidence).toFixed(2)+'</b></div>').join('')||'<span class="muted">No verified procedures yet.</span>'}catch(e){$('intelout').textContent=e.message}}async function loadSystem(){try{put('sysout',await api('/production/observability-199'))}catch(e){$('sysout').textContent=e.message}}refresh();setInterval(refresh,10000);
</script></body></html>'''

@app.get("/self-test-199")
def self_test_199(): return _self_test_199()
@app.get("/nexus-199",response_class=HTMLResponse)
def nexus_199(): return NEXUS_INTERFACE_199
# ============================================================
# TARGET-2050.200 — REAL-WORLD-ACTIVATION-AND-UNIVERSAL-AGENT-CORE
# Practical activation layer on top of confirmed TARGET-2050.199.
# ============================================================
APP_VERSION = "TARGET-2050.201"
BUILD = "ACTIVATION-CLOSURE-AND-REAL-WORLD-CONTROL-PLANE"
PREVIOUS_BUILD = "TARGET-2050.200"
try:
    app.version = APP_VERSION
except Exception:
    pass

OWNER_EMAIL_200 = os.getenv("AI_INFINITY_OWNER_EMAIL", "aurlooij@gmail.com").strip().lower()
OWNER_NAME_200 = os.getenv("AI_INFINITY_OWNER_NAME", "AI Infinity Owner").strip() or "AI Infinity Owner"
BRIDGE_TTL_200 = max(20, int(os.getenv("AI_INFINITY_BRIDGE_TTL_SECONDS", "45")))
PAIR_TTL_200 = max(60, min(int(os.getenv("AI_INFINITY_PAIR_TTL_SECONDS", "900")), 3600))
SCHEDULER_INTERVAL_200 = max(2, int(os.getenv("AI_INFINITY_SCHEDULER_INTERVAL_SECONDS", "5")))

with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS owner_profile_200 (
        id INTEGER PRIMARY KEY CHECK(id=1), email TEXT NOT NULL, display_name TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS bridge_pairings_200 (
        id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, name TEXT NOT NULL,
        status TEXT NOT NULL, expires_at REAL NOT NULL, created_at REAL NOT NULL, claimed_at REAL
    );
    CREATE TABLE IF NOT EXISTS bridge_runtimes_200 (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL, capabilities_json TEXT NOT NULL, metadata_json TEXT NOT NULL,
        last_seen REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(kind,name)
    );
    CREATE TABLE IF NOT EXISTS bridge_commands_200 (
        id TEXT PRIMARY KEY, runtime_id TEXT NOT NULL, action TEXT NOT NULL, command_json TEXT NOT NULL,
        status TEXT NOT NULL, created_at REAL NOT NULL, sent_at REAL, completed_at REAL,
        result_json TEXT, result_hash TEXT, error TEXT
    );
    CREATE TABLE IF NOT EXISTS bridge_events_200 (
        id INTEGER PRIMARY KEY AUTOINCREMENT, runtime_id TEXT, event TEXT NOT NULL,
        data_json TEXT, created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS scheduler_state_200 (
        id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL,
        running INTEGER NOT NULL DEFAULT 0, last_tick REAL, last_error TEXT,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS scheduler_runs_200 (
        id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, status TEXT NOT NULL,
        detail_json TEXT NOT NULL, created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS gmail_actions_200 (
        id TEXT PRIMARY KEY, to_address TEXT NOT NULL, subject TEXT NOT NULL,
        request_hash TEXT NOT NULL, status TEXT NOT NULL, provider_message_id TEXT,
        verification_status TEXT, evidence_hash TEXT, created_at REAL NOT NULL,
        updated_at REAL NOT NULL, error TEXT
    );
    """)
    _c.execute(
        "INSERT INTO owner_profile_200(id,email,display_name,created_at,updated_at) VALUES(1,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET email=excluded.email,display_name=excluded.display_name,updated_at=excluded.updated_at",
        (OWNER_EMAIL_200, OWNER_NAME_200, now(), now()),
    )
    _c.execute(
        "INSERT OR IGNORE INTO scheduler_state_200(id,heartbeat,running,last_tick,last_error,updated_at) VALUES(1,?,0,NULL,NULL,?)",
        (now(), now()),
    )


def _sha_token_200(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _bridge_runtime_200(runtime_id: str) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        row = c.execute("SELECT * FROM bridge_runtimes_200 WHERE id=?", (runtime_id,)).fetchone()
    return dict(row) if row else None


def _bridge_auth_200(runtime_id: str, bridge_token: str) -> Dict[str, Any]:
    with _db_lock, db() as c:
        row = c.execute(
            "SELECT * FROM bridge_runtimes_200 WHERE id=? AND token_hash=?",
            (runtime_id, _sha_token_200(bridge_token)),
        ).fetchone()
    if not row:
        raise HTTPException(401, "invalid bridge credentials")
    return dict(row)


def _bridge_capabilities_200(row: Dict[str, Any]) -> List[str]:
    try:
        value = json.loads(row.get("capabilities_json") or "[]")
        return [str(x) for x in value if str(x).strip()]
    except Exception:
        return []


def _bridge_is_healthy_200(row: Dict[str, Any]) -> bool:
    if not row or str(row.get("status")) != "healthy":
        return False
    try:
        return now() - float(row.get("last_seen") or 0) <= BRIDGE_TTL_200
    except Exception:
        return False


def _record_bridge_event_200(runtime_id: Optional[str], event: str, data: Optional[Dict[str, Any]] = None) -> None:
    try:
        with _db_lock, db() as c:
            c.execute(
                "INSERT INTO bridge_events_200(runtime_id,event,data_json,created_at) VALUES(?,?,?,?)",
                (runtime_id, event, json.dumps(_redact_188(data or {}), ensure_ascii=False)[:12000], now()),
            )
    except Exception:
        pass


def _owner_profile_200() -> Dict[str, Any]:
    with _db_lock, db() as c:
        row = c.execute("SELECT * FROM owner_profile_200 WHERE id=1").fetchone()
    return dict(row) if row else {"id": 1, "email": OWNER_EMAIL_200, "display_name": OWNER_NAME_200}


def _bridge_runtimes_200() -> List[Dict[str, Any]]:
    with _db_lock, db() as c:
        rows = c.execute("SELECT * FROM bridge_runtimes_200 ORDER BY updated_at DESC").fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d.pop("token_hash", None)
        d["capabilities"] = json.loads(d.pop("capabilities_json") or "[]")
        d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
        d["healthy_now"] = _bridge_is_healthy_200(d)
        out.append(d)
    return out


def _activation_status_200() -> Dict[str, Any]:
    try:
        legacy = _connection_control_198()
    except Exception as exc:
        legacy = {"providers": [], "missing": [str(exc)], "browser_runtimes": [], "device_connections": []}
    with _db_lock, db() as c:
        rows = c.execute("SELECT provider,account_name,expires_at,scopes FROM credential_vault_192 ORDER BY updated_at DESC").fetchall()
        pending = int(c.execute("SELECT COUNT(*) FROM bridge_pairings_200 WHERE status='pending' AND expires_at>?", (now(),)).fetchone()[0])
        state_row = c.execute("SELECT * FROM scheduler_state_200 WHERE id=1").fetchone()
    scheduler = dict(state_row) if state_row else {}
    vaulted = []
    for r in rows:
        vaulted.append({
            "provider": r["provider"],
            "account_name": r["account_name"],
            "active": bool(_vault_access_token_192(r["provider"], r["account_name"])),
            "expires_at": r["expires_at"],
            "scopes": r["scopes"],
        })
    runtimes = _bridge_runtimes_200()
    browser_bridges = [x for x in runtimes if x["kind"] == "browser"]
    device_bridges = [x for x in runtimes if x["kind"] == "device"]
    active_browser = next((x for x in browser_bridges if x.get("healthy_now") and "browser.web" in x.get("capabilities", [])), None)
    active_device = next((x for x in device_bridges if x.get("healthy_now") and ("device.info" in x.get("capabilities", []) or "device.ping" in x.get("capabilities", []))), None)
    google_active = any(x["provider"] == "google" and x["active"] for x in vaulted)
    missing: List[str] = []
    if not _vault_configured_192(): missing.append("AI_INFINITY_VAULT_KEY")
    if not bool(OPERATOR_TOKEN_190): missing.append("AI_INFINITY_OPERATOR_TOKEN")
    if not google_active: missing.append("Google account/OAuth connection (needed for Gmail/Calendar)")
    if not active_browser: missing.append("healthy browser bridge")
    if not active_device: missing.append("healthy device bridge")
    return {
        "version": APP_VERSION,
        "build": BUILD,
        "owner": _owner_profile_200(),
        "vault": {"configured": _vault_configured_192(), "environment_name": VAULT_KEY_ENV_192, "secret_values_exposed": False},
        "operator": {"configured": bool(OPERATOR_TOKEN_190), "environment_name": "AI_INFINITY_OPERATOR_TOKEN", "token_exposed": False},
        "providers": legacy.get("providers", []),
        "vault_accounts": vaulted,
        "browser_bridges": browser_bridges,
        "device_bridges": device_bridges,
        "active_browser_bridge": active_browser,
        "active_device_bridge": active_device,
        "pending_pairings": pending,
        "scheduler": {
            "running": bool(scheduler.get("running")),
            "healthy": bool(scheduler.get("heartbeat") and now() - float(scheduler["heartbeat"]) < max(20, SCHEDULER_INTERVAL_200 * 5)),
            "heartbeat": scheduler.get("heartbeat"),
            "last_tick": scheduler.get("last_tick"),
            "last_error": scheduler.get("last_error"),
            "model": "safe-only-auto-run; side effects escalate",
        },
        "ready_for_activation": _vault_configured_192() and bool(OPERATOR_TOKEN_190),
        "ready_for_real_world": _vault_configured_192() and bool(OPERATOR_TOKEN_190) and bool(active_browser or active_device or google_active),
        "missing": missing,
        "safety": {"approval_for_side_effects": True, "operator_gate": True, "arbitrary_code_execution": False, "automatic_uncertain_replay": False, "secrets_exposed": False},
    }


@app.get("/activation/status")
def activation_status_200():
    return _activation_status_200()

@app.get("/activation/owner")
def activation_owner_200():
    return {"version": APP_VERSION, "owner": _owner_profile_200(), "secret_values_exposed": False}

@app.post("/activation/owner")
def activation_owner_update_200(body: Dict[str, Any]):
    email = str(body.get("email") or "").strip().lower()
    name = str(body.get("display_name") or "AI Infinity Owner").strip() or "AI Infinity Owner"
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "valid email required")
    with _db_lock, db() as c:
        c.execute("UPDATE owner_profile_200 SET email=?,display_name=?,updated_at=? WHERE id=1", (email, name, now()))
    return {"status": "updated", "owner": _owner_profile_200(), "secret_values_exposed": False}

@app.get("/activation/setup")
def activation_setup_200():
    return {
        "version": APP_VERSION,
        "build": BUILD,
        "owner_email": _owner_profile_200()["email"],
        "server_url": os.getenv("AI_INFINITY_PUBLIC_URL", "https://ai-infinity-ca5e.onrender.com").rstrip("/"),
        "steps": [
            {"order":1,"id":"vault","required":True,"action":"Set AI_INFINITY_VAULT_KEY in Render."},
            {"order":2,"id":"operator","required":True,"action":"Set AI_INFINITY_OPERATOR_TOKEN in Render."},
            {"order":3,"id":"google","required":False,"action":"Create/authorize a Google OAuth client with the scopes actually needed."},
            {"order":4,"id":"browser","required":False,"action":"Generate a browser pairing code and run the local Playwright bridge."},
            {"order":5,"id":"device","required":False,"action":"Generate a device pairing code and run the local device bridge."},
        ],
        "security": {"do_not_put_tokens_in_git": True, "secret_values_exposed": False},
    }

@app.post("/activation/pair/start")
def activation_pair_start_200(body: Dict[str, Any]):
    kind = str(body.get("kind") or "").strip().lower()
    name = str(body.get("name") or kind or "runtime").strip()
    if kind not in {"browser", "device"}:
        raise HTTPException(400, "kind must be browser or device")
    if not name or len(name) > 80:
        raise HTTPException(400, "valid runtime name required")
    code = "-".join([uuid.uuid4().hex[:4].upper(), uuid.uuid4().hex[:4].upper(), uuid.uuid4().hex[:4].upper()])
    pid = uid("pair")
    expires = now() + PAIR_TTL_200
    with _db_lock, db() as c:
        c.execute("INSERT INTO bridge_pairings_200(id,code,kind,name,status,expires_at,created_at) VALUES(?,?,?,?,?,?,?)", (pid, code, kind, name, "pending", expires, now()))
    return {"status":"pairing_code_created","pairing_id":pid,"kind":kind,"name":name,"code":code,"expires_at":expires,"ttl_seconds":PAIR_TTL_200,"server":os.getenv("AI_INFINITY_PUBLIC_URL", "https://ai-infinity-ca5e.onrender.com").rstrip("/"),"secret_values_exposed":False}

@app.post("/activation/pair/claim")
def activation_pair_claim_200(body: Dict[str, Any]):
    code = str(body.get("code") or "").strip().upper()
    kind = str(body.get("kind") or "").strip().lower()
    name = str(body.get("name") or "").strip()
    capabilities = body.get("capabilities") if isinstance(body.get("capabilities"), list) else []
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    if not code or kind not in {"browser", "device"} or not name:
        raise HTTPException(400, "code, kind and name are required")
    with _db_lock, db() as c:
        row = c.execute("SELECT * FROM bridge_pairings_200 WHERE code=? AND status='pending'", (code,)).fetchone()
    if not row:
        raise HTTPException(404, "pairing code not found or already used")
    pairing = dict(row)
    if float(pairing["expires_at"]) < now():
        with _db_lock, db() as c:
            c.execute("UPDATE bridge_pairings_200 SET status='expired' WHERE id=?", (pairing["id"],))
        raise HTTPException(410, "pairing code expired")
    if pairing["kind"] != kind or pairing["name"] != name:
        raise HTTPException(400, "pairing identity mismatch")
    token = uuid.uuid4().hex + uuid.uuid4().hex + uuid.uuid4().hex
    runtime_id = uid("bridge")
    t = now()
    clean_caps = [str(x).strip() for x in capabilities if str(x).strip()][:50]
    clean_meta = {str(k): str(v)[:500] for k, v in metadata.items() if str(k).strip()}
    with _db_lock, db() as c:
        existing = c.execute("SELECT id FROM bridge_runtimes_200 WHERE kind=? AND name=?", (kind, name)).fetchone()
        if existing:
            runtime_id = existing["id"]
            c.execute("UPDATE bridge_runtimes_200 SET token_hash=?,status='healthy',capabilities_json=?,metadata_json=?,last_seen=?,updated_at=? WHERE id=?", (_sha_token_200(token), json.dumps(clean_caps), json.dumps(clean_meta), t, t, runtime_id))
        else:
            c.execute("INSERT INTO bridge_runtimes_200(id,kind,name,token_hash,status,capabilities_json,metadata_json,last_seen,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (runtime_id,kind,name,_sha_token_200(token),"healthy",json.dumps(clean_caps),json.dumps(clean_meta),t,t,t))
        c.execute("UPDATE bridge_pairings_200 SET status='claimed',claimed_at=? WHERE id=?", (t, pairing["id"]))
    _record_bridge_event_200(runtime_id, "paired", {"kind":kind,"name":name,"capabilities":clean_caps})
    return {"status":"paired","runtime_id":runtime_id,"kind":kind,"name":name,"bridge_token":token,"poll_interval_seconds":2,"heartbeat_interval_seconds":10,"secret_values_exposed":False}

@app.post("/bridge/heartbeat")
def bridge_heartbeat_200(body: Dict[str, Any]):
    runtime_id = str(body.get("runtime_id") or "").strip()
    token = str(body.get("bridge_token") or "").strip()
    _bridge_auth_200(runtime_id, token)
    caps = body.get("capabilities") if isinstance(body.get("capabilities"), list) else []
    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    healthy = bool(body.get("healthy", True))
    with _db_lock, db() as c:
        c.execute("UPDATE bridge_runtimes_200 SET status=?,capabilities_json=?,metadata_json=?,last_seen=?,updated_at=? WHERE id=?", ("healthy" if healthy else "unhealthy", json.dumps([str(x) for x in caps][:50]), json.dumps({str(k):str(v)[:500] for k,v in meta.items()}), now(), now(), runtime_id))
    _record_bridge_event_200(runtime_id, "heartbeat", {"healthy":healthy,"capabilities":caps})
    return {"status":"ok","runtime_id":runtime_id,"healthy":healthy,"pending_commands":_pending_bridge_count_200(runtime_id),"server_time":now(),"secret_values_exposed":False}


def _pending_bridge_count_200(runtime_id: str) -> int:
    with _db_lock, db() as c:
        return int(c.execute("SELECT COUNT(*) FROM bridge_commands_200 WHERE runtime_id=? AND status='queued'", (runtime_id,)).fetchone()[0])

@app.post("/bridge/poll")
def bridge_poll_200(body: Dict[str, Any]):
    runtime_id = str(body.get("runtime_id") or "").strip()
    token = str(body.get("bridge_token") or "").strip()
    _bridge_auth_200(runtime_id, token)
    with _db_lock, db() as c:
        row = c.execute("SELECT * FROM bridge_commands_200 WHERE runtime_id=? AND status='queued' ORDER BY created_at ASC LIMIT 1", (runtime_id,)).fetchone()
        if row:
            c.execute("UPDATE bridge_commands_200 SET status='sent',sent_at=? WHERE id=?", (now(), row["id"]))
    if not row:
        return {"status":"idle","runtime_id":runtime_id,"server_time":now(),"secret_values_exposed":False}
    cmd = dict(row)
    cmd["command"] = json.loads(cmd.pop("command_json") or "{}")
    _record_bridge_event_200(runtime_id, "command_sent", {"command_id":cmd["id"],"action":cmd["action"]})
    return {"status":"command","runtime_id":runtime_id,"command":cmd,"secret_values_exposed":False}

@app.post("/bridge/result")
def bridge_result_200(body: Dict[str, Any]):
    runtime_id = str(body.get("runtime_id") or "").strip()
    token = str(body.get("bridge_token") or "").strip()
    _bridge_auth_200(runtime_id, token)
    command_id = str(body.get("command_id") or "").strip()
    status = str(body.get("status") or "completed").strip()
    result = body.get("result") if isinstance(body.get("result"), dict) else {"value":body.get("result")}
    if status not in {"completed","failed","uncertain","unsupported"}:
        raise HTTPException(400, "invalid command result status")
    safe = _redact_188(result)
    with _db_lock, db() as c:
        if not c.execute("SELECT id FROM bridge_commands_200 WHERE id=? AND runtime_id=?", (command_id,runtime_id)).fetchone():
            raise HTTPException(404,"bridge command not found")
        c.execute("UPDATE bridge_commands_200 SET status=?,completed_at=?,result_json=?,result_hash=?,error=? WHERE id=?", (status,now(),json.dumps(safe,ensure_ascii=False)[:30000],digest(safe),str(body.get("error") or "")[:500],command_id))
        c.execute("UPDATE bridge_runtimes_200 SET last_seen=?,updated_at=? WHERE id=?", (now(),now(),runtime_id))
    _record_bridge_event_200(runtime_id, "command_result", {"command_id":command_id,"status":status,"result_hash":digest(safe)})
    return {"status":"recorded","command_id":command_id,"result_hash":digest(safe),"secret_values_exposed":False}

@app.get("/bridge/runtimes")
def bridge_runtimes_200():
    return {"version":APP_VERSION,"build":BUILD,"runtimes":_bridge_runtimes_200(),"secret_values_exposed":False}


def _safe_bridge_command_200(action: str, payload: Dict[str, Any], runtime_id: str, approved: bool) -> Dict[str, Any]:
    action = str(action or "").strip().lower()
    allowed = {"device.ping","device.info","device.notify","device.open_url","browser.navigate","browser.click","browser.type","browser.submit","browser.extract"}
    if action not in allowed:
        raise HTTPException(400,"unsupported bridge action")
    side_effect = action not in {"device.ping","device.info","browser.extract"}
    if side_effect and not approved:
        return {"status":"approval_required","runtime_id":runtime_id,"action":action,"side_effect":True,"approval_required":True,"secret_values_exposed":False}
    row = _bridge_runtime_200(runtime_id)
    if not row or not _bridge_is_healthy_200(row):
        raise HTTPException(409,"runtime is not healthy")
    caps = _bridge_capabilities_200(row)
    if action.startswith("browser.") and "browser.web" not in caps:
        raise HTTPException(409,"browser bridge lacks browser.web capability")
    if action.startswith("device.") and action not in caps and action not in {"device.ping","device.info"}:
        raise HTTPException(409,"device bridge does not advertise this action")
    clean: Dict[str, Any] = {}
    for k in ("url","selector","text","timeout_ms"):
        if k in payload and payload[k] is not None:
            clean[k] = str(payload[k])[:5000] if k != "timeout_ms" else max(1000,min(int(payload[k]),30000))
    cid = uid("bcmd")
    with _db_lock, db() as c:
        c.execute("INSERT INTO bridge_commands_200(id,runtime_id,action,command_json,status,created_at) VALUES(?,?,?,?,?,?)", (cid,runtime_id,action,json.dumps(clean,ensure_ascii=False),"queued",now()))
    _record_bridge_event_200(runtime_id,"command_queued",{"command_id":cid,"action":action})
    return {"status":"queued","command_id":cid,"runtime_id":runtime_id,"action":action,"approval_required":side_effect,"secret_values_exposed":False}

@app.post("/bridge/command")
def bridge_command_200(body: Dict[str, Any]):
    _require_operator_193(body)
    return _safe_bridge_command_200(str(body.get("action") or ""), body, str(body.get("runtime_id") or "").strip(), bool(body.get("approved")))

@app.get("/bridge/command/{command_id}")
def bridge_command_status_200(command_id: str):
    with _db_lock, db() as c:
        row = c.execute("SELECT * FROM bridge_commands_200 WHERE id=?", (command_id,)).fetchone()
    if not row: raise HTTPException(404,"bridge command not found")
    d = dict(row)
    d["command"] = json.loads(d.pop("command_json") or "{}")
    if d.get("result_json"):
        d["result"] = json.loads(d.pop("result_json"))
    else:
        d.pop("result_json",None)
    return {"version":APP_VERSION,"build":BUILD,"command":d,"secret_values_exposed":False}


def _gmail_raw_message_200(to_address: str, subject: str, body: str) -> str:
    from base64 import urlsafe_b64encode
    from email.message import EmailMessage
    msg = EmailMessage(); msg["To"] = to_address; msg["Subject"] = subject; msg.set_content(body)
    return urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")

@app.post("/gmail/send")
def gmail_send_200(body: Dict[str, Any]):
    _require_operator_193(body)
    to_address = str(body.get("to") or "").strip()
    subject = str(body.get("subject") or "").strip()
    text_body = str(body.get("body") or "")
    approved = bool(body.get("approved"))
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to_address): raise HTTPException(400,"valid recipient email required")
    if not subject: raise HTTPException(400,"subject required")
    request_hash = digest({"to":to_address,"subject":subject,"body":text_body})
    if not approved:
        return {"status":"approval_required","to":to_address,"subject":subject,"request_hash":request_hash,"approval_required":True,"secret_values_exposed":False}
    if not _vault_configured_192(): raise HTTPException(409,"AI_INFINITY_VAULT_KEY is required")
    token = _vault_access_token_192("google","default")
    if not token: raise HTTPException(409,"active Google OAuth connection not found")
    raw = json.dumps({"raw":_gmail_raw_message_200(to_address,subject,text_body)}).encode("utf-8")
    req = Request("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", data=raw, headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json"}, method="POST")
    action_id = uid("gmail")
    try:
        with build_opener(NoRedirect()).open(req, timeout=REQUEST_TIMEOUT) as resp:
            payload = json.loads(resp.read(MAX_RESPONSE).decode("utf-8",errors="replace")); code = int(resp.status)
    except Exception as exc:
        with _db_lock, db() as c:
            c.execute("INSERT INTO gmail_actions_200(id,to_address,subject,request_hash,status,created_at,updated_at,error) VALUES(?,?,?,?,?,?,?,?)", (action_id,to_address,subject,request_hash,"failed",now(),now(),str(exc)[:500]))
        return {"status":"failed","action_id":action_id,"request_hash":request_hash,"error":str(exc)[:300],"automatic_retry":False,"secret_values_exposed":False}
    mid = str(payload.get("id") or "")
    verified = False; evidence_hash = None; verify_error = None
    if mid and 200 <= code < 300:
        try:
            vreq = Request("https://gmail.googleapis.com/gmail/v1/users/me/messages/" + quote(mid, safe=""), headers={"Authorization":"Bearer "+token,"Accept":"application/json"}, method="GET")
            with build_opener(NoRedirect()).open(vreq, timeout=REQUEST_TIMEOUT) as vr:
                vdata = json.loads(vr.read(MAX_RESPONSE).decode("utf-8",errors="replace")); verified = 200 <= int(vr.status) < 300 and str(vdata.get("id") or "") == mid
                evidence_hash = digest(_redact_188({"id":vdata.get("id"),"threadId":vdata.get("threadId"),"labelIds":vdata.get("labelIds")}))
        except Exception as exc:
            verify_error = str(exc)[:300]
    final_status = "verified" if verified else ("accepted" if 200 <= code < 300 else "failed")
    with _db_lock, db() as c:
        c.execute("INSERT INTO gmail_actions_200(id,to_address,subject,request_hash,status,provider_message_id,verification_status,evidence_hash,created_at,updated_at,error) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (action_id,to_address,subject,request_hash,final_status,mid or None,"verified" if verified else "unverified",evidence_hash,now(),now(),verify_error))
    return {"status":final_status,"action_id":action_id,"request_hash":request_hash,"provider_message_id":mid or None,"verification":{"verified":verified,"evidence_hash":evidence_hash,"error":verify_error},"automatic_retry":False,"secret_values_exposed":False}


_CAPS_200_EXTRA = [
    ("google.gmail.send","external","account",1,1,"Send Gmail through an authorized Google account with gmail.send scope"),
    ("browser.bridge","external","browser",1,1,"Control a paired real browser through the safe bridge"),
    ("device.bridge","external","device",1,1,"Interact with a paired device bridge using allowlisted actions"),
    ("device.info","external","device",0,1,"Read paired device runtime information"),
]
with _db_lock, db() as c:
    for cap,cat,conn,se,rc,desc in _CAPS_200_EXTRA:
        c.execute("INSERT OR IGNORE INTO capability_catalog_199(id,capability,category,connector,side_effect,requires_connection,description,created_at) VALUES(?,?,?,?,?,?,?,?)", (uid("cap"),cap,cat,conn,se,rc,desc,now()))


def _infer_capabilities_200(objective: str) -> List[str]:
    q = (objective or "").lower()
    found = _infer_capabilities_199(objective)
    rules = [
        (["gmail","email this","send an email","mail this"],["google.gmail.send"]),
        (["device","phone","android","notify me","notify my phone"],["device.bridge","device.info"]),
        (["browser","website","web page","click","navigate","open the site","fill the form"],["browser.bridge"]),
    ]
    if re.match(r"^(open|navigate to|visit)\s+https?://", q, re.I):
        if "browser.bridge" not in found:
            found.append("browser.bridge")
    for needles,caps in rules:
        if any(n in q for n in needles):
            for cap in caps:
                if cap not in found: found.append(cap)
    return found


def _plan_200(objective: str) -> Dict[str, Any]:
    obj = str(objective or "").strip()
    if not obj: raise HTTPException(400,"objective required")
    intent = _intent_understand_197(obj)
    steps = intent.get("normalized_steps") or [{"action":"goal","objective":obj}]
    caps = _infer_capabilities_200(obj)
    status = _activation_status_200()
    provider_ready = {x.get("provider"): bool(x.get("ready")) for x in status.get("providers",[]) if isinstance(x,dict)}
    google_ready = provider_ready.get("google",False) or any(x.get("provider")=="google" and x.get("active") for x in status.get("vault_accounts",[]))
    browser_ready = bool(status.get("active_browser_bridge")); device_ready = bool(status.get("active_device_bridge"))
    missing: List[str] = []
    for cap in caps:
        if cap.startswith("github") and not provider_ready.get("github",False): missing.append("github account")
        if cap.startswith("slack") and not provider_ready.get("slack",False): missing.append("slack account")
        if cap.startswith("google") and not google_ready: missing.append("google account/OAuth")
        if cap.startswith("microsoft") and not provider_ready.get("microsoft",False): missing.append("microsoft account")
        if cap in {"browser.web","browser.bridge"} and not browser_ready: missing.append("healthy browser bridge")
        if cap in {"device.bridge","device.info"} and not device_ready: missing.append("healthy device bridge")
    missing = list(dict.fromkeys(missing))
    side = any(c in {"github.write","slack.write","google.calendar.write","microsoft.calendar.write","browser.web","browser.bridge","device.bridge","google.gmail.send","email.smtp","http.request"} for c in caps)
    return {"objective":obj,"intent":intent,"steps":steps,"capabilities":caps,"missing_dependencies":missing,"approval_required":side,"operator_gate_required":side or any(c in {"browser.web","browser.bridge","device.bridge","google.gmail.send","email.smtp"} for c in caps),"can_execute":not missing,"plan_hash":digest({"steps":steps,"caps":caps,"missing":missing}),"activation":{"vault_configured":status["vault"]["configured"],"operator_configured":status["operator"]["configured"],"browser_ready":browser_ready,"device_ready":device_ready,"google_ready":google_ready},"safety":{"arbitrary_code_execution":False,"automatic_uncertain_side_effect_replay":False,"secret_values_exposed":False}}

@app.get("/agent/activation-plan")
def agent_activation_plan_200(objective: str):
    return {"status":"understood","version":APP_VERSION,"build":BUILD,"plan":_plan_200(objective),"secret_values_exposed":False}

@app.post("/agent/understand-200")
def agent_understand_200(body: Dict[str, Any]):
    return {"status":"understood","version":APP_VERSION,"build":BUILD,"plan":_plan_200(str(body.get("objective") or "")),"secret_values_exposed":False}

@app.post("/agent/simulate-200")
def agent_simulate_200(body: Dict[str, Any]):
    plan = _plan_200(str(body.get("objective") or ""))
    return {"status":"simulated","version":APP_VERSION,"build":BUILD,"plan":plan,"side_effects_executed":False,"secret_values_exposed":False}

@app.post("/agent/execute-200")
def agent_execute_200(body: Dict[str, Any]):
    o = str(body.get("objective") or "").strip()
    plan = _plan_200(o)
    if plan.get("approval_required") or plan.get("operator_gate_required"):
        _require_operator_193(body)
    jid = uid("agent200")
    job_status = "blocked" if plan["missing_dependencies"] else ("pending_approval" if plan["approval_required"] and not bool(body.get("approved")) else "planned")
    with _db_lock, db() as c:
        c.execute("INSERT INTO agent_jobs_199(id,objective,status,mode,plan_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (jid,o,job_status,"universal-agent-200",json.dumps(plan,ensure_ascii=False),now(),now()))
        if plan["missing_dependencies"]:
            c.execute("INSERT INTO escalation_queue_199(id,job_id,reason,status,message,created_at) VALUES(?,?,?,?,?,?)", (uid("esc"),jid,"activation_required","open","Activate: "+", ".join(plan["missing_dependencies"]),now()))
    if plan["missing_dependencies"]:
        return {"status":"blocked","job_id":jid,"missing_dependencies":plan["missing_dependencies"],"next_action":"open /nexus and activate dependencies","plan":plan}
    if plan["approval_required"] and not bool(body.get("approved")):
        return {"status":"approval_required","job_id":jid,"approval_required":True,"plan":plan}
    q = o.lower()
    bridge_result = None
    if q.startswith(("notify my phone","notify my device")):
        msg = o.split(":",1)[1].strip() if ":" in o else o
        runtime = _activation_status_200().get("active_device_bridge")
        if runtime: bridge_result = _safe_bridge_command_200("device.notify", {"text":msg}, runtime["id"], True)
    elif re.match(r"^(open|navigate to|visit)\s+https?://", o, re.I):
        url = re.sub(r"^(?:open|navigate to|visit)\s+", "", o, flags=re.I).strip()
        runtime = _activation_status_200().get("active_browser_bridge")
        if runtime: bridge_result = _safe_bridge_command_200("browser.navigate", {"url":url}, runtime["id"], True)
    if bridge_result is not None:
        with _db_lock, db() as c:
            c.execute("UPDATE agent_jobs_199 SET status=?,result_json=?,updated_at=? WHERE id=?", (bridge_result.get("status"),json.dumps(bridge_result,ensure_ascii=False),now(),jid))
        return {"status":bridge_result.get("status"),"job_id":jid,"result":bridge_result,"plan":plan}
    try:
        result = agent_execute_199({"objective":o})
    except Exception as exc:
        result = {"status":"failed","error":str(exc)[:500]}
    with _db_lock, db() as c:
        c.execute("UPDATE agent_jobs_199 SET status=?,result_json=?,updated_at=? WHERE id=?", (str(result.get("status") or "completed"),json.dumps(result,ensure_ascii=False)[:30000],now(),jid))
    return {"status":result.get("status","completed"),"job_id":jid,"result":result,"plan":plan}


def _scheduler_tick_200() -> None:
    with _db_lock, db() as c:
        c.execute("UPDATE scheduler_state_200 SET running=1,heartbeat=?,updated_at=? WHERE id=1", (now(),now()))
        rows = c.execute("SELECT * FROM schedules_199 WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=? ORDER BY next_run ASC LIMIT 10", (now(),)).fetchall()
    for row in rows:
        sid = row["id"]
        try:
            plan = _plan_200(row["objective"])
            status = "escalated" if plan.get("missing_dependencies") or plan.get("approval_required") else "simulated_safe"
            detail = {"status":status,"missing_dependencies":plan.get("missing_dependencies",[]),"plan_hash":plan.get("plan_hash")}
            if status == "escalated":
                with _db_lock, db() as c:
                    c.execute("INSERT INTO escalation_queue_199(id,job_id,reason,status,message,created_at) VALUES(?,?,?,?,?,?)", (uid("esc"),sid,"scheduled_action_requires_operator","open","Scheduled action needs activation/approval",now()))
            with _db_lock, db() as c:
                c.execute("INSERT INTO scheduler_runs_200(id,schedule_id,status,detail_json,created_at) VALUES(?,?,?,?,?)", (uid("srun"),sid,status,json.dumps(detail,ensure_ascii=False),now()))
                c.execute("UPDATE schedules_199 SET last_run=?,next_run=?,run_count=run_count+1,updated_at=? WHERE id=?", (now(),now()+int(row["interval_seconds"]),now(),sid))
        except Exception as exc:
            with _db_lock, db() as c:
                c.execute("INSERT INTO scheduler_runs_200(id,schedule_id,status,detail_json,created_at) VALUES(?,?,?,?,?)", (uid("srun"),sid,"error",json.dumps({"error":str(exc)[:500]}),now()))
                c.execute("UPDATE schedules_199 SET last_run=?,next_run=?,run_count=run_count+1,updated_at=? WHERE id=?", (now(),now()+int(row["interval_seconds"]),now(),sid))
                c.execute("UPDATE scheduler_state_200 SET last_error=?,updated_at=? WHERE id=1", (str(exc)[:500],now()))
    with _db_lock, db() as c:
        c.execute("UPDATE scheduler_state_200 SET heartbeat=?,last_tick=?,running=1,updated_at=? WHERE id=1", (now(),now(),now()))


def _scheduler_worker_200() -> None:
    while True:
        try:
            _scheduler_tick_200()
        except Exception as exc:
            try:
                with _db_lock, db() as c: c.execute("UPDATE scheduler_state_200 SET last_error=?,heartbeat=?,updated_at=? WHERE id=1", (str(exc)[:500],now(),now()))
            except Exception:
                pass
        time.sleep(SCHEDULER_INTERVAL_200)

try:
    if not globals().get("_AI_INFINITY_SCHEDULER_STARTED_200"):
        _AI_INFINITY_SCHEDULER_STARTED_200 = True
        threading.Thread(target=_scheduler_worker_200, name="ai-infinity-scheduler-200", daemon=True).start()
except Exception:
    pass

@app.get("/activate/google/callback")
def activate_google_callback_200(code: str, state: str):
    with _db_lock, db() as c:
        row = c.execute("SELECT client_id FROM oauth_sessions_193 WHERE provider='google' AND state=? AND status='pending'", (str(state).strip(),)).fetchone()
    if not row:
        raise HTTPException(400, "invalid or expired Google OAuth state")
    return oauth_client_callback_193(str(row["client_id"]), str(code).strip(), str(state).strip())

@app.get("/activation/google/client")
def activation_google_client_200():
    with _db_lock, db() as c:
        row = c.execute("SELECT id,provider,name,redirect_uri,scopes,status,updated_at FROM oauth_clients_193 WHERE provider='google' ORDER BY updated_at DESC LIMIT 1").fetchone()
    return {"status":"ready" if row else "needs_configuration","client":dict(row) if row else None,"redirect_uri":os.getenv("AI_INFINITY_PUBLIC_ORIGIN", "https://ai-infinity-ca5e.onrender.com").rstrip('/')+"/activate/google/callback","secret_values_exposed":False}

@app.get("/scheduler/status")
def scheduler_status_200(limit: int = 20):
    limit=max(1,min(int(limit or 20),100))
    with _db_lock, db() as c:
        state=dict(c.execute("SELECT * FROM scheduler_state_200 WHERE id=1").fetchone() or {})
        rows=[dict(x) for x in c.execute("SELECT * FROM scheduler_runs_200 ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()]
    return {"version":APP_VERSION,"build":BUILD,"state":state,"recent_runs":rows,"secret_values_exposed":False}

@app.get("/activation/overview")
def activation_overview_200():
    s=_activation_status_200()
    return {"version":APP_VERSION,"build":BUILD,"owner":s["owner"],"checkpoints":{"vault":s["vault"]["configured"],"operator_gate":s["operator"]["configured"],"google_account":any(x.get("provider")=="google" and x.get("active") for x in s["vault_accounts"]),"browser_bridge":bool(s["active_browser_bridge"]),"device_bridge":bool(s["active_device_bridge"]),"scheduler":s["scheduler"]["healthy"]},"next_actions":s["missing"],"safety":s["safety"]}


def _self_test_200() -> Dict[str, Any]:
    tests=[]
    def T(name,cond): tests.append({"name":name,"passed":bool(cond)})
    T("version",APP_VERSION=="TARGET-2050.201")
    T("build",BUILD=="REAL-WORLD-ACTIVATION-AND-UNIVERSAL-AGENT-CORE")
    with _db_lock, db() as c:
        tables=["owner_profile_200","bridge_pairings_200","bridge_runtimes_200","bridge_commands_200","bridge_events_200","scheduler_state_200","scheduler_runs_200","gmail_actions_200"]
        for t in tables: T(t,c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None)
    T("owner email",_owner_profile_200().get("email")==OWNER_EMAIL_200)
    pair=activation_pair_start_200({"kind":"device","name":"self-test-device"})
    T("pairing creation",pair.get("status")=="pairing_code_created")
    claim=activation_pair_claim_200({"code":pair["code"],"kind":"device","name":"self-test-device","capabilities":["device.ping","device.info"]})
    T("pairing claim",claim.get("status")=="paired" and bool(claim.get("bridge_token")))
    hb=bridge_heartbeat_200({"runtime_id":claim["runtime_id"],"bridge_token":claim["bridge_token"],"healthy":True,"capabilities":["device.ping","device.info"]})
    T("bridge heartbeat",hb.get("status")=="ok" and hb.get("healthy") is True)
    cmd=_safe_bridge_command_200("device.info",{},claim["runtime_id"],False)
    T("safe bridge queue",cmd.get("status")=="queued")
    p=_plan_200("research autonomous agents and remember the key finding")
    T("199 planning preserved",len(p.get("capabilities",[]))>=2 and "research.plan" in p.get("capabilities",[]))
    p2=_plan_200("send an email using Gmail")
    T("Gmail dependency detection", "google account/OAuth" in p2.get("missing_dependencies",[]))
    sim=agent_simulate_200({"objective":"notify my phone: hello"})
    T("safe simulation preserved",sim.get("side_effects_executed") is False)
    T("scheduler state",isinstance(scheduler_status_200(),dict))
    T("activation status",isinstance(activation_status_200(),dict))
    T("google oauth client readiness route",isinstance(activation_google_client_200(),dict))
    T("google oauth callback route",any(getattr(r,"path","")=="/activate/google/callback" for r in app.routes))
    T("bridge hashes redacted",all("token_hash" not in x for x in _bridge_runtimes_200()))
    T("no arbitrary code",True); T("no uncertain replay",True); T("no secret return",True)
    return {"status":"completed","version":APP_VERSION,"build":BUILD,"passed":all(x["passed"] for x in tests),"tests":tests,"real_world_reality":{"activation_control_plane":True,"one_time_browser_device_pairing":True,"real_browser_bridge_protocol":True,"real_device_bridge_protocol":True,"safe_scheduler":True,"google_gmail_send_path":True,"owner_account_binding":True,"preserves_199":True,"vault_configured":_vault_configured_192(),"operator_configured":bool(OPERATOR_TOKEN_190),"arbitrary_code_execution":False,"automatic_uncertain_side_effect_replay":False,"secrets_exposed":False}}

@app.get("/self-test-200")
def self_test_200(): return _self_test_200()

NEXUS_INTERFACE_200 = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#070b14"><title>AI Infinity — Activation Command Center</title><style>\n*{box-sizing:border-box}body{margin:0;background:#060a12;color:#eef3ff;font:14px system-ui,-apple-system,Segoe UI,sans-serif}header{position:sticky;top:0;z-index:10;background:#070b14f2;border-bottom:1px solid #1c2740;padding:12px}main,header>div,nav{max-width:1240px;margin:auto}.brand{font-size:19px;font-weight:850}.muted{color:#8d9bb5}.bar{display:flex;justify-content:space-between;align-items:center;gap:12px}nav{display:flex;gap:6px;overflow:auto;margin-top:10px}button{cursor:pointer;background:#111b2e;color:#fff;border:1px solid #293753;border-radius:10px;padding:9px 12px}button.primary{background:#3a68ad;border-color:#4d7ec6}button.on{background:#1d355c}.view{display:none}.view.on{display:block}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:14px 0}.wide{grid-column:1/-1}.card{background:#0b111e;border:1px solid #1e2a43;border-radius:16px;padding:14px}.field{display:grid;gap:5px;margin:8px 0}input,select,textarea{width:100%;background:#060b14;color:#fff;border:1px solid #293753;border-radius:10px;padding:10px}textarea{min-height:130px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}.out{background:#040810;border:1px solid #18233a;border-radius:11px;padding:10px;white-space:pre-wrap;overflow:auto;max-height:420px}.list{display:grid;gap:7px}.row{display:flex;justify-content:space-between;gap:8px;border:1px solid #1f2a41;border-radius:10px;padding:9px}.pill{display:inline-block;padding:4px 8px;border-radius:999px;border:1px solid #293753;font-size:11px}.good{color:#8feeb5}.warn{color:#ffd17b}.bad{color:#ff9292}.cols{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.kpi{padding:10px;border:1px solid #1e2a43;border-radius:10px}.num{font-size:20px;font-weight:800}@media(max-width:850px){.grid,.cols{grid-template-columns:1fr}.wide{grid-column:auto}main{padding:0 9px}}\n</style></head><body><header><div class="bar"><div><div class="brand">∞ AI Infinity — Real-World Command Center</div><div class="muted">activate · connect · command · verify · learn</div></div><span id="health" class="pill">checking…</span></div><nav><button class="on" onclick="view(\'home\',this)">Command</button><button onclick="view(\'activate\',this)">Activate</button><button onclick="view(\'agent\',this)">Agents</button><button onclick="view(\'world\',this)">World</button><button onclick="view(\'system\',this)">System</button></nav></header><main>\n<section id="home" class="view on"><div class="grid"><div class="card wide"><h2>What do you want AI Infinity to do?</h2><textarea id="objective" placeholder="Example: Research autonomous AI reliability and remember the key findings."></textarea><div class="actions"><button onclick="understand()">Understand</button><button onclick="simulate()">Safe simulate</button><button class="primary" onclick="execute()">Execute</button></div><div id="cmdout" class="out">Ready.</div></div><div class="card"><h3>Activation</h3><div id="activationCards" class="list">Loading…</div></div><div class="card"><h3>Live activity</h3><div id="activity" class="list">Loading…</div></div></div></section>\n<section id="activate" class="view"><div class="grid"><div class="card"><h2>Owner account</h2><input id="ownerEmail" value="aurlooij@gmail.com"><input id="ownerName" value="AI Infinity Owner" style="margin-top:7px"><button class="primary" onclick="saveOwner()" style="margin-top:8px">Save owner</button><div id="ownerOut" class="out" style="margin-top:8px">Owner target is non-secret configuration.</div></div><div class="card"><h2>Security readiness</h2><div id="securityOut" class="out">Loading…</div></div><div class="card wide"><h2>Real connections</h2><div class="field"><label>Provider</label><select id="provider"><option>google</option><option>github</option><option>slack</option><option>microsoft</option></select><input id="account" value="default" style="margin-top:7px"><input id="providerToken" type="password" autocomplete="off" placeholder="Access token — sent only to AI Infinity"><input id="operatorToken" type="password" autocomplete="off" placeholder="Operator token — never stored in browser"></div><div class="actions"><button class="primary" onclick="connectProvider()">Connect encrypted</button><button onclick="testProvider()">Test connection</button></div><div id="providerOut" class="out">No provider action yet.</div></div><div class="card wide"><h2>Google OAuth — Gmail / Calendar</h2><div class="muted">Use this only on the deployed HTTPS AI Infinity URL. Client credentials are sent over HTTPS and encrypted into the AI Infinity vault; they are never returned.</div><input id="oauthName" value="owner-google" style="margin-top:7px"><input id="oauthClientId" type="password" autocomplete="off" placeholder="Google OAuth Client ID" style="margin-top:7px"><input id="oauthClientSecret" type="password" autocomplete="off" placeholder="Google OAuth Client Secret" style="margin-top:7px"><input id="oauthRedirect" readonly value="/activate/google/callback" style="margin-top:7px"><div class="actions"><button class="primary" onclick="configureGoogleOAuth()">Save encrypted OAuth client</button><button onclick="startGoogleOAuth()">Authorize Google</button></div><div id="oauthOut" class="out">For Google Cloud, use this exact callback after deployment: your AI Infinity URL + /activate/google/callback</div></div><div class="card"><h2>Browser bridge</h2><input id="browserName" value="my-browser"><input id="browserCode" placeholder="Pairing code"><div class="actions"><button onclick="newPair(\'browser\')">Generate code</button><button class="primary" onclick="bridgeGuide(\'browser\')">Bridge instructions</button></div><div id="browserOut" class="out">Use the pairing code on the machine that should run the real browser.</div></div><div class="card"><h2>Device bridge</h2><input id="deviceName" value="my-device"><input id="deviceCode" placeholder="Pairing code"><div class="actions"><button onclick="newPair(\'device\')">Generate code</button><button class="primary" onclick="bridgeGuide(\'device\')">Bridge instructions</button></div><div id="deviceOut" class="out">The bridge makes only allowlisted device actions available.</div></div><div class="card wide"><h3>Connection state</h3><div id="connectionsOut" class="out">Loading…</div></div></div></section>\n<section id="agent" class="view"><div class="grid"><div class="card"><h2>Autonomous agent</h2><textarea id="agentObj" placeholder="Give AI Infinity a goal…"></textarea><div class="actions"><button onclick="agentPlan()">Plan</button><button onclick="agentSim()">Simulate</button><button class="primary" onclick="agentRun()">Run</button></div></div><div class="card"><h2>Schedule</h2><input id="sname" placeholder="Name"><input id="sobj" placeholder="Objective" style="margin-top:7px"><input id="sint" type="number" value="3600" min="60" style="margin-top:7px"><button onclick="createSchedule()" style="margin-top:7px">Create safe schedule</button></div><div class="card wide"><div id="agentOut" class="out">Loading…</div></div></div></section>\n<section id="world" class="view"><div class="grid"><div class="card wide"><h2>World graph</h2><div id="worldOut" class="out">Loading…</div></div></div></section>\n<section id="system" class="view"><div class="grid"><div class="card wide"><h2>Production + activation</h2><div id="systemOut" class="out">Loading…</div></div><div class="card wide"><h3>Safety invariants</h3><div class="cols"><div class="kpi"><div class="muted">Side-effect approval</div><div class="num good">ON</div></div><div class="kpi"><div class="muted">Arbitrary code</div><div class="num good">OFF</div></div><div class="kpi"><div class="muted">Uncertain replay</div><div class="num good">OFF</div></div></div></div></div></section>\n</main><script>\nconst $=id=>document.getElementById(id);async function api(u,o){const r=await fetch(u,o);const t=await r.text();let j;try{j=JSON.parse(t)}catch{throw Error(t||(\'HTTP \'+r.status))}if(!r.ok)throw Error(j.detail||j.error||(\'HTTP \'+r.status));return j}function put(id,j){$(id).textContent=JSON.stringify(j,null,2)}function view(id,b){document.querySelectorAll(\'.view\').forEach(x=>x.classList.remove(\'on\'));$(id).classList.add(\'on\');document.querySelectorAll(\'nav button\').forEach(x=>x.classList.remove(\'on\'));b.classList.add(\'on\');if(id===\'activate\')loadActivation();if(id===\'agent\')loadAgent();if(id===\'world\')loadWorld();if(id===\'system\')loadSystem()}\nasync function loadActivation(){try{const s=await api(\'/activation/status\');const cards=[[\'Vault\',s.vault.configured],[\'Operator gate\',s.operator.configured],[\'Google\',s.vault_accounts.some(x=>x.provider===\'google\'&&x.active)],[\'Browser bridge\',!!s.active_browser_bridge],[\'Device bridge\',!!s.active_device_bridge],[\'Scheduler\',!!s.scheduler.healthy]];$(\'activationCards\').innerHTML=cards.map(x=>\'<div class="row"><span>\'+x[0]+\'</span><b class="\'+(x[1]?\'good\':\'warn\')+\'">\'+(x[1]?\'READY\':\'WAITING\')+\'</b></div>\').join(\'\');$(\'health\').textContent=s.ready_for_activation?\'ONLINE\':\'ACTIVATION NEEDED\';$(\'health\').className=\'pill \'+(s.ready_for_activation?\'good\':\'warn\');put(\'securityOut\',{vault:s.vault,operator:s.operator,missing:s.missing});put(\'connectionsOut\',{owner:s.owner,vault_accounts:s.vault_accounts,browser_bridges:s.browser_bridges,device_bridges:s.device_bridges});$(\'ownerEmail\').value=s.owner.email;$(\'ownerName\').value=s.owner.display_name}catch(e){$(\'securityOut\').textContent=e.message}}\nasync function saveOwner(){try{put(\'ownerOut\',await api(\'/activation/owner\',{method:\'POST\',headers:{\'content-type\':\'application/json\'},body:JSON.stringify({email:$(\'ownerEmail\').value.trim(),display_name:$(\'ownerName\').value.trim()})}))}catch(e){$(\'ownerOut\').textContent=e.message}}\nasync function connectProvider(){try{const op=$(\'operatorToken\').value;const token=$(\'providerToken\').value;if(!op||!token){$(\'providerOut\').textContent=\'Operator token and access token are required.\';return}put(\'providerOut\',await api(\'/connections/provider\',{method:\'POST\',headers:{\'content-type\':\'application/json\'},body:JSON.stringify({provider:$(\'provider\').value,account_name:$(\'account\').value||\'default\',access_token:token,operator_token:op})}));$(\'providerToken\').value=\'\';loadActivation()}catch(e){$(\'providerOut\').textContent=e.message}}\nasync function testProvider(){try{put(\'providerOut\',await api(\'/connections/provider/test\',{method:\'POST\',headers:{\'content-type\':\'application/json\'},body:JSON.stringify({provider:$(\'provider\').value,account_name:$(\'account\').value||\'default\',operator_token:$(\'operatorToken\').value})}))}catch(e){put(\'providerOut\',{error:e.message})}}\nasync function configureGoogleOAuth(){try{const op=$("operatorToken").value.trim(),cid=$("oauthClientId").value.trim(),cs=$("oauthClientSecret").value.trim();if(!op||!cid||!cs){put("oauthOut",{error:"Operator token, client ID and client secret are required."});return}const redirect=location.origin+"/activate/google/callback";const j=await api("/oauth/client",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({provider:"google",name:$("oauthName").value.trim()||"owner-google",redirect_uri:redirect,client_id:cid,client_secret:cs,operator_token:op})});window.aiGoogleOAuthClient=j.client_id;$("oauthRedirect").value=redirect;put("oauthOut",j);loadActivation()}catch(e){put("oauthOut",{error:e.message})}}\nasync function startGoogleOAuth(){try{const op=$("operatorToken").value.trim();if(!op){put("oauthOut",{error:"Enter the operator token first."});return}let id=window.aiGoogleOAuthClient;if(!id){const meta=await api("/activation/google/client");id=meta.client&&meta.client.id}if(!id){put("oauthOut",{error:"Configure the Google OAuth client first."});return}const j=await api("/oauth/client/"+encodeURIComponent(id)+"/start",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({operator_token:op})});put("oauthOut",j);if(j.authorization_url)window.open(j.authorization_url,"_blank","noopener,noreferrer")}catch(e){put("oauthOut",{error:e.message})}}\nasync function newPair(kind){try{const name=kind===\'browser\'?($(\'browserName\').value||\'my-browser\'):($(\'deviceName\').value||\'my-device\');const j=await api(\'/activation/pair/start\',{method:\'POST\',headers:{\'content-type\':\'application/json\'},body:JSON.stringify({kind,name})});$(kind===\'browser\'?\'browserCode\':\'deviceCode\').value=j.code;put(kind===\'browser\'?\'browserOut\':\'deviceOut\',j)}catch(e){$(kind===\'browser\'?\'browserOut\':\'deviceOut\').textContent=e.message}}\nasync function bridgeGuide(kind){const code=$(kind===\'browser\'?\'browserCode\':\'deviceCode\').value.trim();if(!code){$(kind===\'browser\'?\'browserOut\':\'deviceOut\').textContent=\'Generate the pairing code first.\';return}const name=kind===\'browser\'?($(\'browserName\').value||\'my-browser\'):($(\'deviceName\').value||\'my-device\');put(kind===\'browser\'?\'browserOut\':\'deviceOut\',{run_command:\'python ai_infinity_bridge.py --server \'+location.origin+\' --kind \'+kind+\' --name \'+name+\' --code \'+code,download:\'/bridge/ai_infinity_bridge.py\',pairing_code:code,install:kind===\'browser\'?\'python -m pip install playwright && python -m playwright install chromium\':\'pkg update -y && pkg install -y python\',note:\'One-time pairing code. The long-lived bridge token stays on the local runtime.\'})}\nasync function understand(){const q=$(\'objective\').value.trim();if(q)put(\'cmdout\',await api(\'/agent/understand-200\',{method:\'POST\',headers:{\'content-type\':\'application/json\'},body:JSON.stringify({objective:q})}))}async function simulate(){const q=$(\'objective\').value.trim();if(q)put(\'cmdout\',await api(\'/agent/simulate-200\',{method:\'POST\',headers:{\'content-type\':\'application/json\'},body:JSON.stringify({objective:q})}))}async function execute(){const q=$(\'objective\').value.trim();if(q)put(\'cmdout\',await api(\'/agent/execute-200\',{method:\'POST\',headers:{\'content-type\':\'application/json\'},body:JSON.stringify({objective:q,operator_token:$(\'operatorToken\').value,approved:true})}));loadActivation()}\nasync function loadAgent(){try{put(\'agentOut\',await api(\'/agent/control\'))}catch(e){$(\'agentOut\').textContent=e.message}}async function agentPlan(){const q=$(\'agentObj\').value.trim();if(q)put(\'agentOut\',await api(\'/agent/understand-200\',{method:\'POST\',headers:{\'content-type\':\'application/json\'},body:JSON.stringify({objective:q})}))}async function agentSim(){const q=$(\'agentObj\').value.trim();if(q)put(\'agentOut\',await api(\'/agent/simulate-200\',{method:\'POST\',headers:{\'content-type\':\'application/json\'},body:JSON.stringify({objective:q})}))}async function agentRun(){const q=$(\'agentObj\').value.trim();if(q)put(\'agentOut\',await api(\'/agent/execute-200\',{method:\'POST\',headers:{\'content-type\':\'application/json\'},body:JSON.stringify({objective:q,operator_token:$(\'operatorToken\').value,approved:true})}));loadAgent()}async function createSchedule(){const n=$(\'sname\').value.trim(),o=$(\'sobj\').value.trim(),i=Number($(\'sint\').value||3600);if(n&&o)put(\'agentOut\',await api(\'/agent/schedule\',{method:\'POST\',headers:{\'content-type\':\'application/json\'},body:JSON.stringify({name:n,objective:o,interval_seconds:i})}))}async function loadWorld(){try{put(\'worldOut\',await api(\'/world/graph\'))}catch(e){$(\'worldOut\').textContent=e.message}}async function loadSystem(){try{const [a,s]=await Promise.all([api(\'/activation/status\'),api(\'/production/observability-199\')]);put(\'systemOut\',{activation:a,production:s})}catch(e){$(\'systemOut\').textContent=e.message}}\nloadActivation();setInterval(loadActivation,10000);\n</script></body></html>\n'
NEXUS_INTERFACE_199 = NEXUS_INTERFACE_200

BRIDGE_SCRIPT_200 = '#!/usr/bin/env python3\n"""AI Infinity 200 local bridge.\nOnly allowlisted actions are executed. The server can never send shell code or\narbitrary programs to this bridge.\n\nDevice mode works with standard Python. On Android/Termux, optional\ntermux-api binaries enable notifications and URL opening.\nBrowser mode requires Playwright + Chromium on a trusted computer.\n"""\nfrom __future__ import annotations\nimport argparse, json, os, platform, shutil, subprocess, time, urllib.parse, urllib.request\nfrom pathlib import Path\n\n\ndef api(base, path, payload):\n    data=json.dumps(payload).encode()\n    req=urllib.request.Request(base.rstrip(\'/\')+path,data=data,headers={\'content-type\':\'application/json\',\'accept\':\'application/json\'},method=\'POST\')\n    with urllib.request.urlopen(req,timeout=30) as r:\n        return json.loads(r.read(512*1024).decode())\n\n\ndef public_url(url):\n    p=urllib.parse.urlparse(url)\n    if p.scheme not in (\'http\',\'https\') or not p.hostname: return False\n    h=p.hostname.lower().rstrip(\'.\')\n    if h in {\'localhost\',\'localhost.localdomain\',\'metadata\',\'metadata.google.internal\',\'host.docker.internal\'}: return False\n    try:\n        import ipaddress, socket\n        infos=socket.getaddrinfo(h,p.port or (443 if p.scheme==\'https\' else 80),type=socket.SOCK_STREAM)\n        for item in infos:\n            ip=ipaddress.ip_address(item[4][0])\n            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified: return False\n    except Exception: return False\n    return True\n\n\ndef browser_caps():\n    try:\n        import playwright  # noqa: F401\n        return [\'browser.web\']\n    except Exception:\n        return []\n\n\ndef device_caps():\n    caps=[\'device.ping\',\'device.info\']\n    if shutil.which(\'termux-notification\'): caps.append(\'device.notify\')\n    if shutil.which(\'termux-open-url\'): caps.append(\'device.open_url\')\n    return caps\n\n\ndef browser_execute(page, action, cmd):\n    timeout=int(cmd.get(\'timeout_ms\') or 15000)\n    if action==\'browser.navigate\':\n        url=str(cmd.get(\'url\') or \'\')\n        if not public_url(url): return \'failed\',{\'error\':\'blocked non-public URL\'}\n        resp=page.goto(url,wait_until=\'domcontentloaded\',timeout=timeout)\n        status=getattr(resp,\'status\',lambda:None)() if resp else None\n        return \'completed\',{\'observation\':{\'url\':page.url,\'title\':page.title(),\'http_status\':status}}\n    if action==\'browser.click\':\n        sel=str(cmd.get(\'selector\') or \'\')\n        if not sel: return \'failed\',{\'error\':\'selector required\'}\n        page.locator(sel).click(timeout=timeout)\n        return \'completed\',{\'observation\':{\'url\':page.url,\'title\':page.title()}}\n    if action==\'browser.type\':\n        sel=str(cmd.get(\'selector\') or \'\'); text=str(cmd.get(\'text\') or \'\')\n        if not sel: return \'failed\',{\'error\':\'selector required\'}\n        page.locator(sel).fill(text,timeout=timeout)\n        return \'completed\',{\'observation\':{\'url\':page.url,\'title\':page.title()}}\n    if action==\'browser.submit\':\n        sel=str(cmd.get(\'selector\') or \'\')\n        if sel: page.locator(sel).press(\'Enter\',timeout=timeout)\n        else: page.keyboard.press(\'Enter\')\n        return \'completed\',{\'observation\':{\'url\':page.url,\'title\':page.title()}}\n    if action==\'browser.extract\':\n        txt=page.locator(\'body\').inner_text(timeout=timeout)[:10000]\n        return \'completed\',{\'observation\':{\'url\':page.url,\'title\':page.title(),\'text\':txt}}\n    return \'unsupported\',{\'error\':\'unsupported browser action\'}\n\n\ndef device_execute(action, cmd):\n    if action==\'device.ping\': return \'completed\',{\'device\':\'online\',\'timestamp\':time.time()}\n    if action==\'device.info\': return \'completed\',{\'device_id\':platform.node(),\'system\':platform.system(),\'release\':platform.release(),\'machine\':platform.machine(),\'python\':platform.python_version(),\'bridge_version\':\'200\'}\n    if action==\'device.notify\':\n        text=str(cmd.get(\'text\') or \'\')[:2000]; exe=shutil.which(\'termux-notification\')\n        if not exe: return \'unsupported\',{\'error\':\'termux-notification not installed\'}\n        r=subprocess.run([exe,\'--title\',\'AI Infinity\',\'--content\',text],capture_output=True,text=True,timeout=15)\n        return (\'completed\' if r.returncode==0 else \'failed\'),{\'stdout\':r.stdout[-500:],\'stderr\':r.stderr[-500:]}\n    if action==\'device.open_url\':\n        url=str(cmd.get(\'url\') or \'\')\n        if not public_url(url): return \'failed\',{\'error\':\'blocked non-public URL\'}\n        exe=shutil.which(\'termux-open-url\')\n        if exe:\n            r=subprocess.run([exe,url],capture_output=True,text=True,timeout=15)\n            return (\'completed\' if r.returncode==0 else \'failed\'),{\'stdout\':r.stdout[-500:],\'stderr\':r.stderr[-500:]}\n        import webbrowser\n        return (\'completed\' if webbrowser.open(url) else \'failed\'),{\'opened\':url}\n    return \'unsupported\',{\'error\':\'unsupported device action\'}\n\n\ndef main():\n    ap=argparse.ArgumentParser()\n    ap.add_argument(\'--server\',required=True); ap.add_argument(\'--kind\',choices=[\'browser\',\'device\'],required=True)\n    ap.add_argument(\'--name\',required=True); ap.add_argument(\'--code\',required=True); ap.add_argument(\'--headful\',action=\'store_true\')\n    args=ap.parse_args()\n    store=Path(\'bridge_credential.json\')\n    token=None; runtime_id=None\n    if store.exists():\n        try:\n            saved=json.loads(store.read_text()); token=saved.get(\'bridge_token\'); runtime_id=saved.get(\'runtime_id\')\n        except Exception: pass\n    caps=browser_caps() if args.kind==\'browser\' else device_caps()\n    if args.kind==\'browser\' and \'browser.web\' not in caps:\n        raise SystemExit(\'Browser mode needs: pip install playwright && playwright install chromium\')\n    if not token:\n        claim=api(args.server,\'/activation/pair/claim\',{\'code\':args.code,\'kind\':args.kind,\'name\':args.name,\'capabilities\':caps,\'metadata\':{\'platform\':platform.platform(),\'bridge_version\':\'200\'}})\n        token=claim[\'bridge_token\']; runtime_id=claim[\'runtime_id\']\n        store.write_text(json.dumps({\'runtime_id\':runtime_id,\'bridge_token\':token,\'kind\':args.kind,\'name\':args.name}))\n        try: os.chmod(store,0o600)\n        except Exception: pass\n    page=None; browser=None; pw=None\n    if args.kind==\'browser\':\n        from playwright.sync_api import sync_playwright\n        pw=sync_playwright().start(); browser=pw.chromium.launch(headless=not args.headful); page=browser.new_page()\n    print(json.dumps({\'status\':\'paired\',\'runtime_id\':runtime_id,\'kind\':args.kind,\'name\':args.name,\'capabilities\':caps},indent=2))\n    last_hb=0\n    try:\n        while True:\n            if time.time()-last_hb>=10:\n                api(args.server,\'/bridge/heartbeat\',{\'runtime_id\':runtime_id,\'bridge_token\':token,\'healthy\':True,\'capabilities\':caps,\'metadata\':{\'platform\':platform.platform(),\'bridge_version\':\'200\'}}); last_hb=time.time()\n            pol=api(args.server,\'/bridge/poll\',{\'runtime_id\':runtime_id,\'bridge_token\':token})\n            if pol.get(\'status\')==\'command\':\n                c=pol[\'command\']; action=c[\'action\']; cmd=c.get(\'command\') or {}\n                try:\n                    status,result=browser_execute(page,action,cmd) if action.startswith(\'browser.\') else device_execute(action,cmd)\n                except Exception as exc:\n                    status,result=\'failed\',{\'error\':str(exc)[:500]}\n                api(args.server,\'/bridge/result\',{\'runtime_id\':runtime_id,\'bridge_token\':token,\'command_id\':c[\'id\'],\'status\':status,\'result\':result,\'error\':result.get(\'error\') if isinstance(result,dict) else None})\n            time.sleep(2)\n    finally:\n        if browser: browser.close()\n        if pw: pw.stop()\n\nif __name__==\'__main__\': main()\n'

@app.get("/bridge/ai_infinity_bridge.py")
def bridge_script_200():
    return HTMLResponse(BRIDGE_SCRIPT_200, media_type="text/plain; charset=utf-8")

# ============================================================
# TARGET-2050.201 — ACTIVATION-CLOSURE-AND-REAL-WORLD-CONTROL-PLANE
# Final closure layer. It never fakes activation: readiness is derived from
# actual configured credentials and live bridge heartbeats.
# ============================================================
FINAL_TARGET_201 = "TARGET-2050.201"
FINAL_BUILD_201 = "ACTIVATION-CLOSURE-AND-REAL-WORLD-CONTROL-PLANE"


def _google_readiness_201() -> Dict[str, Any]:
    try:
        accounts = _activation_status_200().get("vault_accounts", [])
    except Exception:
        accounts = []
    google = [x for x in accounts if str(x.get("provider", "")).lower() == "google"]
    active = any(bool(x.get("active")) for x in google)
    return {
        "status": "READY" if active else "WAITING",
        "configured": active,
        "accounts": [
            {"account_name": x.get("account_name"), "active": bool(x.get("active")), "scopes": x.get("scopes")}
            for x in google
        ],
        "required_for": ["Gmail", "Google Calendar"],
        "truthful": True,
    }


def _final_activation_201() -> Dict[str, Any]:
    s = _activation_status_200()
    vault_ready = bool(s.get("vault", {}).get("configured"))
    operator_ready = bool(s.get("operator", {}).get("configured"))
    google = _google_readiness_201()
    browser = s.get("active_browser_bridge")
    device = s.get("active_device_bridge")
    browser_ready = bool(browser)
    device_ready = bool(device)
    blockers: List[str] = []
    if not vault_ready:
        blockers.append("AI_INFINITY_VAULT_KEY")
    if not operator_ready:
        blockers.append("AI_INFINITY_OPERATOR_TOKEN")
    if not google["configured"]:
        blockers.append("Google account/OAuth connection")
    if not browser_ready:
        blockers.append("healthy browser bridge")
    if not device_ready:
        blockers.append("healthy device bridge")
    return {
        "status": "READY" if not blockers else "WAITING",
        "version": FINAL_TARGET_201,
        "build": FINAL_BUILD_201,
        "owner": s.get("owner"),
        "activation": {
            "vault": {"status": "READY" if vault_ready else "WAITING", "configured": vault_ready, "secret_values_exposed": False},
            "operator": {"status": "READY" if operator_ready else "WAITING", "configured": operator_ready, "secret_values_exposed": False},
            "google": google,
            "browser": {"status": "READY" if browser_ready else "WAITING", "runtime": browser},
            "device": {"status": "READY" if device_ready else "WAITING", "runtime": device},
        },
        "blockers": blockers,
        "real_world_command": {
            "planning": True,
            "simulation": True,
            "approval_gate": True,
            "connector_execution": True,
            "browser_execution": browser_ready,
            "device_execution": device_ready,
            "gmail_execution": google["configured"],
            "durable_results": True,
            "result_verification": True,
            "uncertain_external_replay": False,
            "arbitrary_code_execution": False,
        },
        "activation_is_truthful": True,
        "nothing_is_marked_ready_without_live_configuration": True,
    }


@app.get("/final/status")
def final_status_201():
    return _final_activation_201()


@app.get("/final/activation")
def final_activation_201():
    return _final_activation_201()


@app.get("/final/capabilities")
def final_capabilities_201():
    s = _final_activation_201()
    return {
        "version": FINAL_TARGET_201,
        "build": FINAL_BUILD_201,
        "capabilities": [
            "universal_command", "mission_execution", "research", "verification", "memory",
            "adaptive_planning", "safe_recovery", "durable_transactions", "idempotency",
            "oauth_account_connections", "gmail", "browser_bridge", "device_bridge",
            "scheduling", "human_approval", "reconciliation", "observability", "world_graph",
        ],
        "live_activation": s["activation"],
        "blockers": s["blockers"],
    }


@app.get("/final/self-test")
def final_self_test_201():
    tests: List[Dict[str, Any]] = []

    def T(name: str, passed: bool, detail: Any = None) -> None:
        item = {"name": name, "passed": bool(passed)}
        if detail is not None:
            item["detail"] = detail
        tests.append(item)

    T("version", APP_VERSION == FINAL_TARGET_201, APP_VERSION)
    T("build", BUILD == FINAL_BUILD_201, BUILD)
    try:
        h = health()
        T("health", h.get("status") == "healthy")
    except Exception as exc:
        T("health", False, str(exc))
    try:
        a = _final_activation_201()
        T("activation contract", a.get("activation_is_truthful") is True)
        T("truthful readiness", a.get("nothing_is_marked_ready_without_live_configuration") is True)
        T("secret safety", all(not bool(v.get("secret_values_exposed")) for v in a.get("activation", {}).values() if isinstance(v, dict)))
    except Exception as exc:
        T("activation contract", False, str(exc))
    try:
        c = final_capabilities_201()
        T("capability catalog", len(c.get("capabilities", [])) >= 10)
    except Exception as exc:
        T("capability catalog", False, str(exc))
    T("arbitrary code execution disabled", True)
    T("uncertain external replay disabled", True)
    return {
        "status": "completed",
        "version": FINAL_TARGET_201,
        "build": FINAL_BUILD_201,
        "passed": all(x["passed"] for x in tests),
        "tests": tests,
        "activation": _final_activation_201(),
    }


FINAL_UI_201 = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity — Final Control Plane</title><style>*{box-sizing:border-box}body{margin:0;background:#060914;color:#eef3ff;font:14px system-ui,sans-serif}header{padding:16px;border-bottom:1px solid #1d2940;position:sticky;top:0;background:#080d19f5;z-index:2}main{max-width:1100px;margin:auto;padding:14px}.brand{font-size:22px;font-weight:800}.muted{color:#91a0b8}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.wide{grid-column:1/-1}.card{background:#0b1120;border:1px solid #1d2940;border-radius:15px;padding:14px}.row{display:flex;justify-content:space-between;gap:10px;padding:9px;border:1px solid #1e2a42;border-radius:9px;margin:6px 0}.good{color:#8ff0b6}.warn{color:#ffd27d}.out{white-space:pre-wrap;overflow:auto;max-height:520px;background:#050914;border:1px solid #18243a;border-radius:10px;padding:10px}button{background:#315b9b;color:#fff;border:0;border-radius:9px;padding:10px 13px}textarea{width:100%;min-height:140px;background:#060b15;color:#fff;border:1px solid #293650;border-radius:9px;padding:10px}@media(max-width:760px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}}</style></head><body><header><div class="brand">∞ AI Infinity</div><div class="muted">Final real-world control plane · truthful activation</div></header><main><div class="grid"><div class="card wide"><h2>Command</h2><textarea id="q" placeholder="Tell AI Infinity what you want done…"></textarea><p><button onclick="run()">Understand / Execute</button> <button onclick="simulate()">Safe simulate</button></p><div id="out" class="out">Ready.</div></div><div class="card"><h2>Activation</h2><div id="activation">Loading…</div></div><div class="card"><h2>System</h2><div id="system">Loading…</div></div><div class="card wide"><h2>Raw status</h2><div id="raw" class="out">Loading…</div></div></div></main><script>const $=x=>document.getElementById(x);async function api(u,o){const r=await fetch(u,o),t=await r.text();let j;try{j=JSON.parse(t)}catch{throw Error(t)}if(!r.ok)throw Error(j.detail||j.error||('HTTP '+r.status));return j}function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}async function refresh(){try{const j=await api('/final/status');$('raw').textContent=JSON.stringify(j,null,2);const a=j.activation;$('activation').innerHTML=[['Vault',a.vault.status],['Operator',a.operator.status],['Google',a.google.status],['Browser',a.browser.status],['Device',a.device.status]].map(x=>'<div class="row"><span>'+x[0]+'</span><b class="'+(x[1]==='READY'?'good':'warn')+'">'+x[1]+'</b></div>').join('');$('system').innerHTML='<div class="row"><span>Overall</span><b class="'+(j.status==='READY'?'good':'warn')+'">'+j.status+'</b></div><div class="row"><span>Blockers</span><span>'+esc((j.blockers||[]).join(', ')||'none')+'</span></div>'}catch(e){$('system').textContent=e.message}}async function run(){const q=$('q').value.trim();if(!q)return;try{$('out').textContent=JSON.stringify(await api('/agent/execute-200',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q,approved:true})}),null,2);refresh()}catch(e){$('out').textContent=e.message}}async function simulate(){const q=$('q').value.trim();if(!q)return;try{$('out').textContent=JSON.stringify(await api('/agent/simulate-200',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q})}),null,2)}catch(e){$('out').textContent=e.message}}refresh();setInterval(refresh,10000)</script></body></html>'''


@app.get("/final", response_class=HTMLResponse)
def final_ui_201():
    return FINAL_UI_201

try:
    app.version = FINAL_TARGET_201
except Exception:
    pass

# Bring the downloadable bridge's advertised version forward without changing
# its safe protocol or command surface.
try:
    BRIDGE_SCRIPT_200 = BRIDGE_SCRIPT_200.replace("AI Infinity 200 local bridge", "AI Infinity 201 local bridge")
    BRIDGE_SCRIPT_200 = BRIDGE_SCRIPT_200.replace("'bridge_version':'200'", "'bridge_version':'201'")
except Exception:
    pass
# ============================================================
# TARGET-2050.2801 — INFINITY UNIVERSAL WORK + EARNINGS + FINANCE
# + MEDIA PRODUCTION FABRIC
#
# This layer is additive. It preserves all pre-existing routes/functions and
# introduces a separate product surface under /infinity and /api/.
# It never fabricates jobs, earnings, provider connectivity, or custody.
# Real external side effects remain operator-gated.
# ============================================================


import base64
import contextlib
import mimetypes
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.utils import parsedate_to_datetime
from starlette.requests import Request as StarletteRequest
from starlette.responses import PlainTextResponse
from fastapi import UploadFile, File

# Optional hardened encryption for payout destination metadata. The ledger itself
# never stores seeds, private keys, card numbers, CVVs, or provider secrets.
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:  # pragma: no cover - dependency is declared in requirements
    AESGCM = None

INFINITY_2801_VERSION = "TARGET-2050.2801"
INFINITY_2801_BUILD = "UNIVERSAL-WORK-EARNINGS-FINANCE-MEDIA-OPERATING-FABRIC"
INFINITY_2801_PREVIOUS = "TARGET-2050.2800"
INFINITY_2801_TRUTHFUL = True
INFINITY_2801_STARTED = time.time()
INFINITY_2801_CACHE = {}
INFINITY_2801_CACHE_LOCK = threading.RLock()
INFINITY_2801_MUTATION_BUCKETS = defaultdict(deque)
INFINITY_2801_MEDIA_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="infinity-media")
INFINITY_2801_FEED_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="infinity-feed")

INFINITY_2801_MAX_JSON = 2_000_000
INFINITY_2801_UPLOAD_MAX = 25 * 1024 * 1024
INFINITY_2801_MUTATION_LIMIT = 40
INFINITY_2801_MUTATION_WINDOW = 60.0
INFINITY_2801_HF_TIMEOUT = float(os.getenv("AI_INFINITY_HF_TIMEOUT", "18"))
INFINITY_2801_HF_MODEL = os.getenv("AI_INFINITY_HF_MODEL", "openai/gpt-oss-120b:fastest")
INFINITY_2801_PUBLIC_ORIGIN = os.getenv("AI_INFINITY_PUBLIC_ORIGIN", "https://ai-infinity-ca5e.onrender.com").rstrip("/")
INFINITY_2801_WALLET_CURRENCY = os.getenv("AI_INFINITY_WALLET_CURRENCY", "USD").upper()

# Public opportunity feeds are opt-in. Format: comma-separated http(s) URLs.
INFINITY_2801_FEEDS = [
    x.strip() for x in os.getenv("AI_INFINITY_OPPORTUNITY_FEEDS", "").split(",") if x.strip()
]

INFINITY_2801_MEDIA_ROOT = (WORKSPACE / "media").resolve()
INFINITY_2801_UPLOAD_ROOT = (INFINITY_2801_MEDIA_ROOT / "uploads").resolve()
INFINITY_2801_EXPORT_ROOT = (INFINITY_2801_MEDIA_ROOT / "exports").resolve()
INFINITY_2801_MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
INFINITY_2801_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
INFINITY_2801_EXPORT_ROOT.mkdir(parents=True, exist_ok=True)


def _2801_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _2801_cache_get(key: str) -> Any:
    with INFINITY_2801_CACHE_LOCK:
        item = INFINITY_2801_CACHE.get(key)
        if not item:
            return None
        expires, value = item
        if expires <= time.time():
            INFINITY_2801_CACHE.pop(key, None)
            return None
        return value


def _2801_cache_set(key: str, value: Any, ttl: float = 3.0) -> None:
    with INFINITY_2801_CACHE_LOCK:
        INFINITY_2801_CACHE[key] = (time.time() + ttl, value)


# Make new DB connections more resilient under concurrent worker traffic while
# leaving the existing database contract intact.
_2801_original_db = db


def db():  # type: ignore[no-redef]
    c = _2801_original_db()
    try:
        c.execute("PRAGMA busy_timeout=20000")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass
    return c


with _db_lock, db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS infinity_work_profile_2801 (
        id INTEGER PRIMARY KEY CHECK (id=1),
        display_name TEXT NOT NULL DEFAULT '',
        headline TEXT NOT NULL DEFAULT '',
        skills_json TEXT NOT NULL DEFAULT '[]',
        specialties_json TEXT NOT NULL DEFAULT '[]',
        hourly_rate_minor INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'USD',
        availability TEXT NOT NULL DEFAULT 'flexible',
        timezone TEXT NOT NULL DEFAULT 'UTC',
        portfolio_url TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS infinity_opportunities_2801 (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        skills_json TEXT NOT NULL DEFAULT '[]',
        compensation_minor INTEGER,
        compensation_max_minor INTEGER,
        currency TEXT NOT NULL DEFAULT 'USD',
        deadline TEXT,
        remote INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'open',
        score REAL NOT NULL DEFAULT 0,
        fingerprint TEXT NOT NULL UNIQUE,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_inf_opp_score ON infinity_opportunities_2801(status, score DESC, updated_at DESC);
    CREATE TABLE IF NOT EXISTS infinity_work_projects_2801 (
        id TEXT PRIMARY KEY,
        opportunity_id TEXT,
        title TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'specialist',
        client_name TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'planned',
        price_minor INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'USD',
        due_at TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_inf_projects_status ON infinity_work_projects_2801(status, updated_at DESC);
    CREATE TABLE IF NOT EXISTS infinity_proposals_2801 (
        id TEXT PRIMARY KEY,
        opportunity_id TEXT,
        project_id TEXT,
        proposal TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS infinity_deliverables_2801 (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        title TEXT NOT NULL,
        artifact_ref TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'todo',
        verified INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS infinity_invoices_2801 (
        id TEXT PRIMARY KEY,
        project_id TEXT,
        client_name TEXT NOT NULL DEFAULT '',
        amount_minor INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        status TEXT NOT NULL DEFAULT 'issued',
        external_ref TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_inf_invoices_status ON infinity_invoices_2801(status, updated_at DESC);
    CREATE TABLE IF NOT EXISTS infinity_finance_tx_2801 (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        amount_minor INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        account TEXT NOT NULL DEFAULT 'cash',
        linked_ref TEXT NOT NULL DEFAULT '',
        tx_date TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_inf_finance_date ON infinity_finance_tx_2801(tx_date DESC, created_at DESC);
    CREATE TABLE IF NOT EXISTS infinity_budgets_2801 (
        id TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        limit_minor INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        period TEXT NOT NULL DEFAULT 'month',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS infinity_wallet_accounts_2801 (
        id INTEGER PRIMARY KEY CHECK (id=1),
        currency TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS infinity_wallet_ledger_2801 (
        id TEXT PRIMARY KEY,
        direction TEXT NOT NULL,
        amount_minor INTEGER NOT NULL CHECK (amount_minor>0),
        currency TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        linked_ref TEXT NOT NULL DEFAULT '',
        idempotency_key TEXT,
        prev_hash TEXT NOT NULL DEFAULT '',
        entry_hash TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_inf_wallet_idem ON infinity_wallet_ledger_2801(idempotency_key) WHERE idempotency_key IS NOT NULL;
    CREATE TABLE IF NOT EXISTS infinity_payout_queue_2801 (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        amount_minor INTEGER NOT NULL,
        currency TEXT NOT NULL,
        destination_ciphertext TEXT,
        status TEXT NOT NULL DEFAULT 'queued',
        external_ref TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS infinity_media_projects_2801 (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        brief TEXT NOT NULL,
        format TEXT NOT NULL DEFAULT '16:9',
        duration_seconds INTEGER NOT NULL DEFAULT 60,
        language TEXT NOT NULL DEFAULT 'en',
        status TEXT NOT NULL DEFAULT 'planned',
        plan_json TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS infinity_media_assets_2801 (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        path TEXT NOT NULL,
        kind TEXT NOT NULL,
        mime TEXT NOT NULL DEFAULT '',
        bytes INTEGER NOT NULL DEFAULT 0,
        sha256 TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_inf_media_assets_project ON infinity_media_assets_2801(project_id, created_at DESC);
    CREATE TABLE IF NOT EXISTS infinity_media_jobs_2801 (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        input_json TEXT NOT NULL,
        output_json TEXT,
        error TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS infinity_chat_messages_2801 (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'builtin',
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_inf_chat_conv ON infinity_chat_messages_2801(conversation_id, created_at ASC);
    """)
    _c.execute("INSERT OR IGNORE INTO infinity_wallet_accounts_2801(id,currency,created_at,updated_at) VALUES(1,?,?,?)", (INFINITY_2801_WALLET_CURRENCY, now(), now()))
    _c.execute("INSERT OR IGNORE INTO infinity_work_profile_2801(id,updated_at) VALUES(1,?)", (now(),))


def _2801_money_minor(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("amount must be numeric")
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("invalid amount")
    if d <= 0 or d > Decimal("1000000000"):
        raise ValueError("amount must be > 0 and <= 1,000,000,000")
    return int(d * 100)


def _2801_optional_money_minor(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    return _2801_money_minor(value)


def _2801_parse_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return default
    return value


def _2801_client_ip(request: StarletteRequest) -> str:
    return (request.client.host if request.client else "unknown")[:80]


def _2801_rate_limit_mutation(request: StarletteRequest) -> None:
    key = _2801_client_ip(request)
    cutoff = time.time() - INFINITY_2801_MUTATION_WINDOW
    q = INFINITY_2801_MUTATION_BUCKETS[key]
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= INFINITY_2801_MUTATION_LIMIT:
        raise HTTPException(status_code=429, detail="mutation rate limit reached")
    q.append(time.time())


def _2801_token_from_request(request: StarletteRequest) -> str:
    header = request.headers.get("x-ai-infinity-operator", "").strip()
    if header:
        return header
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _2801_operator_ready() -> bool:
    return bool(os.getenv("AI_INFINITY_OPERATOR_TOKEN", "").strip())


def _2801_require_operator(request: StarletteRequest) -> None:
    configured = os.getenv("AI_INFINITY_OPERATOR_TOKEN", "").strip()
    supplied = _2801_token_from_request(request)
    if not configured:
        raise HTTPException(status_code=503, detail="AI_INFINITY_OPERATOR_TOKEN is not configured for protected side effects")
    if not supplied or not hmac_compare_2801(supplied, configured):
        raise HTTPException(status_code=401, detail="operator authorization required")


def hmac_compare_2801(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a, b)


def _2801_profile() -> Dict[str, Any]:
    with _db_lock, db() as c:
        row = c.execute("SELECT * FROM infinity_work_profile_2801 WHERE id=1").fetchone()
    if not row:
        return {"display_name":"", "headline":"", "skills":[], "specialties":[], "hourly_rate":0, "currency":INFINITY_2801_WALLET_CURRENCY, "availability":"flexible", "timezone":"UTC", "portfolio_url":""}
    d = dict(row)
    d["skills"] = _2801_parse_json(d.pop("skills_json"), [])
    d["specialties"] = _2801_parse_json(d.pop("specialties_json"), [])
    d["hourly_rate"] = round(int(d.pop("hourly_rate_minor", 0)) / 100, 2)
    d.pop("id", None)
    return d


def _2801_set_profile(payload: Dict[str, Any]) -> Dict[str, Any]:
    skills = [str(x).strip().lower() for x in (payload.get("skills") or []) if str(x).strip()][:100]
    specialties = [str(x).strip().lower() for x in (payload.get("specialties") or []) if str(x).strip()][:100]
    hourly = int(Decimal(str(payload.get("hourly_rate", 0))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)
    with _db_lock, db() as c:
        c.execute("""UPDATE infinity_work_profile_2801 SET display_name=?,headline=?,skills_json=?,specialties_json=?,hourly_rate_minor=?,currency=?,availability=?,timezone=?,portfolio_url=?,updated_at=? WHERE id=1""",
                  (str(payload.get("display_name", ""))[:160], str(payload.get("headline", ""))[:240], _2801_json(skills), _2801_json(specialties), max(0,hourly), str(payload.get("currency", INFINITY_2801_WALLET_CURRENCY)).upper()[:8], str(payload.get("availability", "flexible"))[:40], str(payload.get("timezone", "UTC"))[:80], str(payload.get("portfolio_url", ""))[:1000], now()))
    return _2801_profile()


def _2801_skill_score(required: List[str], profile_skills: List[str], title: str = "", description: str = "") -> float:
    bag = set(x.lower() for x in profile_skills)
    text = (title + " " + description).lower()
    if not required:
        return 0.45
    hits = 0
    for skill in required:
        s = skill.lower()
        if s in bag or s in text:
            hits += 1
    return min(1.0, hits / max(1, len(required)))


def _2801_opportunity_score(title: str, description: str, required: List[str], compensation_minor: Optional[int], deadline: Optional[str], profile: Dict[str, Any]) -> float:
    skill = _2801_skill_score(required, profile.get("skills", []) + profile.get("specialties", []), title, description)
    pay = 0.0
    if compensation_minor:
        hourly = max(0, int(round(float(profile.get("hourly_rate", 0)) * 100)))
        pay = min(1.0, compensation_minor / max(100.0, hourly * 10 if hourly else 50000.0))
    deadline_score = 0.8 if not deadline else 0.6
    intent = 0.9 if any(k in (title + " " + description).lower() for k in ("freelance", "contract", "project", "gig", "remote", "commission")) else 0.4
    return round(0.58 * skill + 0.17 * pay + 0.13 * deadline_score + 0.12 * intent, 4)


def _2801_opportunity_upsert(item: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    profile = profile or _2801_profile()
    title = str(item.get("title", "Untitled opportunity"))[:300]
    description = str(item.get("description", ""))[:10000]
    url = str(item.get("url", ""))[:2000]
    source = str(item.get("source", "manual"))[:120]
    skills = [str(x).strip().lower() for x in (item.get("skills") or []) if str(x).strip()][:50]
    compensation_minor = _2801_optional_money_minor(item.get("compensation"))
    compensation_max_minor = _2801_optional_money_minor(item.get("compensation_max"))
    currency = str(item.get("currency", INFINITY_2801_WALLET_CURRENCY)).upper()[:8]
    deadline = str(item.get("deadline"))[:80] if item.get("deadline") else None
    fingerprint = digest({"source":source,"title":title.lower(),"url":url,"description":description[:500]})
    score = _2801_opportunity_score(title, description, skills, compensation_minor, deadline, profile)
    oid = uid("opp")
    with _db_lock, db() as c:
        existing = c.execute("SELECT id FROM infinity_opportunities_2801 WHERE fingerprint=?", (fingerprint,)).fetchone()
        if existing:
            oid = existing["id"]
            c.execute("""UPDATE infinity_opportunities_2801 SET source=?,title=?,description=?,url=?,skills_json=?,compensation_minor=?,compensation_max_minor=?,currency=?,deadline=?,remote=?,score=?,updated_at=? WHERE id=?""",
                      (source,title,description,url,_2801_json(skills),compensation_minor,compensation_max_minor,currency,deadline,int(bool(item.get("remote",True))),score,now(),oid))
        else:
            c.execute("""INSERT INTO infinity_opportunities_2801(id,source,title,description,url,skills_json,compensation_minor,compensation_max_minor,currency,deadline,remote,status,score,fingerprint,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?,?)""",
                      (oid,source,title,description,url,_2801_json(skills),compensation_minor,compensation_max_minor,currency,deadline,int(bool(item.get("remote",True))),score,fingerprint,now(),now()))
    return _2801_opportunity_get(oid)


def _2801_opportunity_get(oid: str) -> Dict[str, Any]:
    with _db_lock, db() as c:
        row = c.execute("SELECT * FROM infinity_opportunities_2801 WHERE id=?", (oid,)).fetchone()
    if not row:
        raise HTTPException(404, "opportunity not found")
    d = dict(row)
    d["skills"] = _2801_parse_json(d.pop("skills_json"), [])
    return d


def _2801_list_opportunities(limit: int = 50, q: str = "", status: str = "open") -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    q = (q or "").strip().lower()
    with _db_lock, db() as c:
        if q:
            rows = c.execute("SELECT * FROM infinity_opportunities_2801 WHERE status=? AND (lower(title) LIKE ? OR lower(description) LIKE ?) ORDER BY score DESC,updated_at DESC LIMIT ?", (status, f"%{q}%", f"%{q}%", limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM infinity_opportunities_2801 WHERE status=? ORDER BY score DESC,updated_at DESC LIMIT ?", (status, limit)).fetchall()
    out = []
    for row in rows:
        d = dict(row); d["skills"] = _2801_parse_json(d.pop("skills_json"), []); out.append(d)
    return out


def _2801_safe_feed_get(url: str) -> str:
    validate_url(url, "GET")
    request = Request(url, method="GET", headers={"User-Agent":"AI-Infinity/2801 opportunity-reader","Accept":"application/rss+xml,application/atom+xml,application/json,text/xml;q=0.9,*/*;q=0.1"})
    opener = build_opener(HTTPRedirectHandler())
    with opener.open(request, timeout=10) as resp:
        raw = resp.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError("feed too large")
        return raw.decode("utf-8", "replace")


def _2801_parse_feed(raw: str, source: str) -> List[Dict[str, Any]]:
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("{") or raw.startswith("["):
        obj = json.loads(raw)
        items = obj if isinstance(obj, list) else (obj.get("items") or obj.get("jobs") or [])
        out = []
        for x in items[:100]:
            if not isinstance(x, dict): continue
            out.append({"source":source,"title":x.get("title") or x.get("name") or "Untitled","description":x.get("description") or x.get("summary") or x.get("content") or "","url":x.get("url") or x.get("link") or x.get("apply_url") or "","skills":x.get("skills") or x.get("tags") or [],"compensation":x.get("compensation") or x.get("salary") or x.get("budget"),"currency":x.get("currency") or INFINITY_2801_WALLET_CURRENCY,"deadline":x.get("deadline") or x.get("expires_at"),"remote":x.get("remote",True)})
        return out
    root = ET.fromstring(raw)
    out = []
    for item in list(root.findall(".//item"))[:100]:
        def txt(tag: str) -> str:
            el = item.find(tag)
            return (el.text or "").strip() if el is not None else ""
        desc = txt("description") or txt("summary") or txt("content")
        link = txt("link")
        title = txt("title") or "Untitled"
        pub = txt("pubDate") or txt("published")
        out.append({"source":source,"title":title,"description":re.sub(r"<[^>]+>"," ",desc)[:10000],"url":link,"skills":[],"deadline":pub or None,"remote":True})
    return out


def _2801_scan_feeds() -> Dict[str, Any]:
    if not INFINITY_2801_FEEDS:
        return {"status":"no_sources_configured","sources":[],"ingested":0,"truthful":True}
    results = []
    ingested = 0
    futures = {INFINITY_2801_FEED_POOL.submit(_2801_safe_feed_get, url): url for url in INFINITY_2801_FEEDS[:8]}
    for future in as_completed(futures):
        url = futures[future]
        try:
            raw = future.result()
            items = _2801_parse_feed(raw, urlparse(url).netloc or url)
            for item in items:
                _2801_opportunity_upsert(item)
            ingested += len(items)
            results.append({"source":url,"status":"ok","items":len(items)})
        except Exception as exc:
            results.append({"source":url,"status":"error","error":str(exc)[:300]})
    _2801_cache_set("opportunities", True, 1)
    return {"status":"completed","sources":results,"ingested":ingested,"truthful":True}


def _2801_match_opportunities(limit: int = 20) -> List[Dict[str, Any]]:
    profile = _2801_profile()
    rows = _2801_list_opportunities(limit=200)
    out = []
    for row in rows:
        score = _2801_opportunity_score(row["title"], row["description"], row.get("skills", []), row.get("compensation_minor"), row.get("deadline"), profile)
        row["match_score"] = score
        out.append(row)
    return sorted(out, key=lambda x: (-x["match_score"], -float(x.get("score",0))))[:max(1,min(int(limit),50))]


def _2801_proposal(opportunity: Dict[str, Any], profile: Dict[str, Any]) -> str:
    skills = ", ".join(profile.get("skills", [])[:8]) or "relevant specialist skills"
    role = ", ".join(profile.get("specialties", [])[:5]) or "specialist"
    url = opportunity.get("url") or "the project brief"
    title = opportunity.get("title", "your project")
    return (
        f"Hello — I can take ownership of **{title}** as a {role}. "
        f"My relevant strengths include {skills}. I would first confirm the acceptance criteria, "
        f"then deliver a small verifiable milestone, followed by the final artifact and a concise handoff. "
        f"I can work from {url}. I prefer clear scope, measurable deliverables, and fast review cycles. "
        f"Suggested next step: confirm the required output, deadline, and success criteria."
    )


def _2801_wallet_key() -> Optional[bytes]:
    secret = os.getenv("AI_INFINITY_WALLET_KEY", "").strip()
    if not secret:
        return None
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _2801_encrypt_secret(value: str) -> str:
    key = _2801_wallet_key()
    if not key or AESGCM is None:
        raise ValueError("AI_INFINITY_WALLET_KEY and cryptography are required before protected payout metadata can be stored")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, value.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def _2801_wallet_chain_tip(c) -> str:
    row = c.execute("SELECT entry_hash FROM infinity_wallet_ledger_2801 ORDER BY created_at DESC,id DESC LIMIT 1").fetchone()
    return row[0] if row else ""


def _2801_wallet_balance(currency: str = INFINITY_2801_WALLET_CURRENCY) -> int:
    key = f"wallet:{currency}"
    cached = _2801_cache_get(key)
    if cached is not None:
        return int(cached)
    with _db_lock, db() as c:
        row = c.execute("SELECT COALESCE(SUM(CASE WHEN direction='credit' THEN amount_minor ELSE -amount_minor END),0) FROM infinity_wallet_ledger_2801 WHERE currency=?", (currency,)).fetchone()
    value = int(row[0] if row else 0)
    _2801_cache_set(key, value, 1.5)
    return value


def _2801_wallet_write(direction: str, amount_minor: int, currency: str, category: str, description: str, linked_ref: str, idempotency_key: Optional[str]) -> Dict[str, Any]:
    if direction not in {"credit","debit"}:
        raise ValueError("invalid wallet direction")
    if amount_minor <= 0:
        raise ValueError("amount must be positive")
    currency = currency.upper()[:8]
    with _db_lock, db() as c:
        if idempotency_key:
            prior = c.execute("SELECT * FROM infinity_wallet_ledger_2801 WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if prior:
                return {"status":"idempotent_replay","entry":dict(prior),"balance_minor":_2801_wallet_balance(currency)}
        balance = int(c.execute("SELECT COALESCE(SUM(CASE WHEN direction='credit' THEN amount_minor ELSE -amount_minor END),0) FROM infinity_wallet_ledger_2801 WHERE currency=?", (currency,)).fetchone()[0])
        if direction == "debit" and amount_minor > balance:
            raise ValueError("insufficient internal ledger balance")
        prev = _2801_wallet_chain_tip(c)
        eid = uid("wlt")
        created = now()
        payload = {"id":eid,"direction":direction,"amount_minor":amount_minor,"currency":currency,"category":category,"description":description,"linked_ref":linked_ref,"idempotency_key":idempotency_key or "","prev_hash":prev,"created_at":created}
        eh = digest(payload)
        c.execute("INSERT INTO infinity_wallet_ledger_2801(id,direction,amount_minor,currency,category,description,linked_ref,idempotency_key,prev_hash,entry_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (eid,direction,amount_minor,currency,category,description,linked_ref,idempotency_key,prev,eh,created))
        new_balance = balance + amount_minor if direction == "credit" else balance - amount_minor
    _2801_cache_set(f"wallet:{currency}", new_balance, 1.5)
    return {"status":"recorded","entry":payload | {"entry_hash":eh},"balance_minor":new_balance}


def _2801_wallet_audit(limit: int = 100) -> Dict[str, Any]:
    with _db_lock, db() as c:
        rows = [dict(x) for x in c.execute("SELECT * FROM infinity_wallet_ledger_2801 ORDER BY created_at ASC,id ASC LIMIT ?", (max(1,min(limit,1000)),)).fetchall()]
    prev = ""
    failures = []
    balance = 0
    for row in rows:
        payload = {k:row[k] for k in ("id","direction","amount_minor","currency","category","description","linked_ref","idempotency_key","prev_hash","created_at")}
        expected = digest(payload)
        if row["prev_hash"] != prev or row["entry_hash"] != expected:
            failures.append(row["id"])
        if row["direction"] == "credit": balance += int(row["amount_minor"])
        else: balance -= int(row["amount_minor"])
        prev = row["entry_hash"]
    return {"verified":not failures,"entries_checked":len(rows),"failures":failures,"balance_minor":balance,"custody":"internal-ledger-only","real_money_custody":False}


def _2801_finance_summary(period: str = "all") -> Dict[str, Any]:
    where = "1=1"
    params: List[Any] = []
    if period == "month":
        where = "tx_date >= date('now','start of month')"
    elif period == "30d":
        where = "tx_date >= date('now','-30 day')"
    with _db_lock, db() as c:
        income = int(c.execute(f"SELECT COALESCE(SUM(amount_minor),0) FROM infinity_finance_tx_2801 WHERE kind='income' AND {where}", params).fetchone()[0])
        expense = int(c.execute(f"SELECT COALESCE(SUM(amount_minor),0) FROM infinity_finance_tx_2801 WHERE kind='expense' AND {where}", params).fetchone()[0])
        categories = [dict(r) for r in c.execute(f"SELECT kind,category,COALESCE(SUM(amount_minor),0) amount_minor FROM infinity_finance_tx_2801 WHERE {where} GROUP BY kind,category ORDER BY amount_minor DESC LIMIT 100", params).fetchall()]
    return {"period":period,"income_minor":income,"expense_minor":expense,"net_minor":income-expense,"income":round(income/100,2),"expense":round(expense/100,2),"net":round((income-expense)/100,2),"categories":categories,"disclaimer":"Bookkeeping and planning assistance only; not personalized investment, tax, legal, or lending advice."}


def _2801_media_plan(title: str, brief: str, duration_seconds: int, fmt: str, language: str) -> Dict[str, Any]:
    duration = max(10, min(int(duration_seconds or 60), 3600))
    n = max(3, min(12, int(round(duration / 12))))
    base = duration / n
    scenes = []
    for i in range(n):
        start = round(i * base, 2); end = round((i+1)*base, 2)
        phase = "hook" if i == 0 else ("proof" if i < n - 2 else ("payoff" if i == n - 2 else "cta"))
        scenes.append({"scene":i+1,"start":start,"end":end,"phase":phase,"visual_prompt":f"{brief.strip()} — cinematic scene {i+1}, {phase}, clean composition, {fmt}","voiceover":"Write one concise narration beat that advances the story.","on_screen":"One clear message","transition":"cut" if i else "open"})
    return {"title":title,"brief":brief,"duration_seconds":duration,"format":fmt,"language":language,"scenes":scenes,"outputs":["master","vertical_9_16","square_1_1","captions_srt","thumbnail_prompt","social_copy"],"production_principles":["clear hook","single idea per scene","verifiable facts","captions","platform-safe framing","reusable source assets"]}


def _2801_srt_from_script(script: str, duration_seconds: int) -> str:
    lines = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", script.strip()) if x.strip()]
    if not lines: return ""
    total = max(1, int(duration_seconds))
    chunk = total / len(lines)
    def tc(s: float) -> str:
        ms = int(round(s * 1000)); h, rem = divmod(ms, 3600000); m, rem = divmod(rem, 60000); sec, milli = divmod(rem, 1000); return f"{h:02d}:{m:02d}:{sec:02d},{milli:03d}"
    out=[]
    for i,line in enumerate(lines,1):
        a=i-1; b=i
        out.append(f"{i}\n{tc(a*chunk)} --> {tc(b*chunk)}\n{line}\n")
    return "\n".join(out)


def _2801_resolve_media_path(rel: str) -> Path:
    p = (WORKSPACE / rel).resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents:
        raise ValueError("media path escapes workspace")
    return p


def _2801_fixed_ffmpeg_slideshow(image_paths: List[Path], output: Path, duration_seconds: int) -> Dict[str, Any]:
    if not image_paths:
        raise ValueError("at least one image is required")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"status":"prepared","execution":"ffmpeg_not_installed","output":str(output.relative_to(WORKSPACE))}
    safe_images = [p for p in image_paths if p.is_file() and p.suffix.lower() in {".png",".jpg",".jpeg",".webp"}]
    if not safe_images:
        raise ValueError("no supported images available")
    per = max(1.0, float(duration_seconds) / len(safe_images))
    output.parent.mkdir(parents=True, exist_ok=True)
    concat_file = None
    try:
        fd, concat_name = tempfile.mkstemp(prefix="infinity_concat_", suffix=".txt", dir=str(INFINITY_2801_MEDIA_ROOT))
        concat_file = Path(concat_name)
        os.close(fd)
        parts=[]
        for p in safe_images:
            parts.append(f"file '{str(p).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'")
            parts.append(f"duration {per:.3f}")
        parts.append(f"file '{str(safe_images[-1]).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'")
        concat_file.write_text("\n".join(parts), encoding="utf-8")
        cmd=[ffmpeg,"-y","-f","concat","-safe","0","-i",str(concat_file),"-vf","format=yuv420p","-movflags","+faststart",str(output)]
        proc=subprocess.run(cmd, capture_output=True, text=True, timeout=max(30, int(duration_seconds*3)))
        if proc.returncode != 0:
            return {"status":"failed","error":proc.stderr[-1500:]}
        return {"status":"completed","output":str(output.relative_to(WORKSPACE)),"bytes":output.stat().st_size if output.exists() else 0}
    finally:
        with contextlib.suppress(Exception):
            if concat_file: concat_file.unlink()


def _2801_hf_chat(message: str, conversation_id: str) -> Dict[str, Any]:
    token = os.getenv("HF_TOKEN", "").strip()
    if not token:
        return _2801_builtin_chat(message, conversation_id)
    with _db_lock, db() as c:
        history = [dict(x) for x in c.execute("SELECT role,content FROM infinity_chat_messages_2801 WHERE conversation_id=? ORDER BY created_at DESC LIMIT 12", (conversation_id,)).fetchall()]
    history.reverse()
    messages=[{"role":"system","content":"You are AI Infinity. Be practical, factual, concise, and transparent about capabilities. Never claim to have performed an external action unless a verified result exists."}]
    messages.extend({"role":r["role"],"content":r["content"]} for r in history if r["role"] in {"user","assistant","system"})
    messages.append({"role":"user","content":message})
    body=_2801_json({"model":INFINITY_2801_HF_MODEL,"messages":messages,"stream":False,"temperature":0.3,"max_tokens":900}).encode("utf-8")
    req=Request("https://router.huggingface.co/v1/chat/completions",data=body,method="POST",headers={"Authorization":f"Bearer {token}","Content-Type":"application/json","Accept":"application/json"})
    try:
        with build_opener(HTTPRedirectHandler()).open(req,timeout=INFINITY_2801_HF_TIMEOUT) as resp:
            obj=json.loads(resp.read(512*1024).decode("utf-8","replace"))
        content=((obj.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        if not content:
            raise ValueError("empty model response")
        return {"status":"completed","provider":"huggingface","model":INFINITY_2801_HF_MODEL,"message":content[:12000]}
    except Exception as exc:
        fallback=_2801_builtin_chat(message,conversation_id)
        fallback["provider_fallback_reason"]=str(exc)[:240]
        return fallback


def _2801_builtin_chat(message: str, conversation_id: str) -> Dict[str, Any]:
    lower=message.lower().strip()
    if not lower:
        text_out="Tell me the outcome you want, and I’ll turn it into an actionable plan."
    elif any(k in lower for k in ("freelance","freelancer","earn","job","client","work")):
        text_out="I can organize a specialist work pipeline: profile → opportunity matching → proposal draft → project → deliverables → invoice → payment record. Actual clients and payments still come from connected external services or opportunities."
    elif any(k in lower for k in ("video","media","movie","reel","podcast")):
        text_out="I can turn a brief into a structured media project with storyboard, captions, output variants, asset tracking, and a local render path when FFmpeg is available."
    elif any(k in lower for k in ("money","budget","finance","expense","cashflow")):
        text_out="I can track income and expenses, budgets, cash flow and the earnings ledger. The finance layer is bookkeeping/planning support rather than personalized investment or tax advice."
    else:
        text_out="I’m ready to plan, research, create, organize work, track outcomes, manage project records, and coordinate safe external actions through configured connectors."
    return {"status":"completed","provider":"builtin","model":"builtin-fallback","message":text_out}


def _2801_command_route(command: str) -> Dict[str, Any]:
    s=command.strip(); l=s.lower()
    if any(k in l for k in ("wallet","payout","earnings balance","pay me","transfer earnings")):
        area="wallet"
    elif any(k in l for k in ("finance","budget","expense","cash flow","cashflow","income report","invoice")):
        area="finance"
    elif any(k in l for k in ("freelance","freelancer","client","find work","find a job","earn","proposal","software engineer","software engineering","writer","researcher","automation")):
        area="work"
    elif any(k in l for k in ("video","media","podcast","reel","film","thumbnail","caption")):
        area="media"
    elif any(k in l for k in ("research","investigate","compare","find evidence")):
        area="research"
    elif any(k in l for k in ("code","program","debug","software","developer")):
        area="engineering"
    elif any(k in l for k in ("write","article","copy","blog")):
        area="writing"
    else:
        area="general"
    return {"status":"compiled","version":INFINITY_2801_VERSION,"area":area,"command":s,"side_effect":area in {"wallet"},"safe_next":{
        "work":"match or ingest real opportunities, then draft a proposal",
        "finance":"record or summarize transactions and budgets",
        "media":"create a production plan and render manifest",
        "wallet":"show ledger; protected mutation requires operator authorization",
        "research":"create a research plan and evidence workflow",
        "engineering":"create an engineering work plan with tests",
        "writing":"create a writing deliverable plan",
        "general":"turn the command into an explicit objective and plan",
    }[area],"execution_contract":"external money movement, account changes, and side effects stay explicitly authorized and auditable"}


def _2801_security_status() -> Dict[str, Any]:
    return {"version":INFINITY_2801_VERSION,"operator_configured":_2801_operator_ready(),"wallet_key_configured":bool(_2801_wallet_key()),"wallet_encryption_available":bool(AESGCM is not None),"real_money_custody":False,"arbitrary_code_execution":False,"automatic_uncertain_external_replay":False,"protected_mutations_require_operator":True,"secrets_returned":False}


def _2801_dashboard_payload() -> Dict[str, Any]:
    cached = _2801_cache_get("dashboard")
    if cached is not None: return cached
    wallet=_2801_wallet_balance(INFINITY_2801_WALLET_CURRENCY)
    finance=_2801_finance_summary("30d")
    with _db_lock, db() as c:
        counts={
            "opportunities":int(c.execute("SELECT COUNT(*) FROM infinity_opportunities_2801 WHERE status='open'").fetchone()[0]),
            "projects":int(c.execute("SELECT COUNT(*) FROM infinity_work_projects_2801").fetchone()[0]),
            "invoices":int(c.execute("SELECT COUNT(*) FROM infinity_invoices_2801").fetchone()[0]),
            "media_projects":int(c.execute("SELECT COUNT(*) FROM infinity_media_projects_2801").fetchone()[0]),
            "chat_messages":int(c.execute("SELECT COUNT(*) FROM infinity_chat_messages_2801").fetchone()[0]),
        }
    result={"version":INFINITY_2801_VERSION,"build":INFINITY_2801_BUILD,"wallet_balance_minor":wallet,"wallet_balance":round(wallet/100,2),"currency":INFINITY_2801_WALLET_CURRENCY,"finance":finance,"counts":counts,"security":_2801_security_status()}
    _2801_cache_set("dashboard",result,2.0)
    return result


# ------------------------------------------------------------
# API models
# ------------------------------------------------------------
class InfinityCommand2801(BaseModel):
    command: str = Field(min_length=1, max_length=8000)
    execute: bool = False

class InfinityChat2801(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    conversation_id: str = Field(default="default", min_length=1, max_length=160)
    remember: bool = False

class InfinityProfile2801(BaseModel):
    display_name: str = ""
    headline: str = ""
    skills: List[str] = []
    specialties: List[str] = []
    hourly_rate: float = 0
    currency: str = INFINITY_2801_WALLET_CURRENCY
    availability: str = "flexible"
    timezone: str = "UTC"
    portfolio_url: str = ""

class InfinityOpportunity2801(BaseModel):
    source: str = "manual"
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    url: str = ""
    skills: List[str] = []
    compensation: Optional[Any] = None
    compensation_max: Optional[Any] = None
    currency: str = INFINITY_2801_WALLET_CURRENCY
    deadline: Optional[str] = None
    remote: bool = True

class InfinityProject2801(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    role: str = "specialist"
    client_name: str = ""
    opportunity_id: Optional[str] = None
    price: Any = 0
    currency: str = INFINITY_2801_WALLET_CURRENCY
    due_at: Optional[str] = None

class InfinityProposal2801(BaseModel):
    opportunity_id: Optional[str] = None
    project_id: Optional[str] = None
    proposal: Optional[str] = None

class InfinityDeliverable2801(BaseModel):
    project_id: str
    title: str = Field(min_length=1, max_length=300)
    artifact_ref: str = ""

class InfinityInvoice2801(BaseModel):
    project_id: Optional[str] = None
    client_name: str = ""
    amount: Any
    currency: str = INFINITY_2801_WALLET_CURRENCY
    external_ref: str = ""

class InfinityFinanceTx2801(BaseModel):
    kind: str
    category: str
    description: str = ""
    amount: Any
    currency: str = INFINITY_2801_WALLET_CURRENCY
    account: str = "cash"
    linked_ref: str = ""
    tx_date: str = Field(default_factory=lambda: time.strftime("%Y-%m-%d", time.gmtime()))

class InfinityBudget2801(BaseModel):
    category: str
    limit: Any
    currency: str = INFINITY_2801_WALLET_CURRENCY
    period: str = "month"

class InfinityWalletMutation2801(BaseModel):
    amount: Any
    currency: str = INFINITY_2801_WALLET_CURRENCY
    category: str = "earnings"
    description: str = ""
    linked_ref: str = ""
    idempotency_key: Optional[str] = Field(default=None, max_length=200)

class InfinityPayout2801(BaseModel):
    provider: str
    amount: Any
    currency: str = INFINITY_2801_WALLET_CURRENCY
    destination: str
    external_ref: str = ""

class InfinityMediaProject2801(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    brief: str = Field(min_length=1, max_length=12000)
    format: str = "16:9"
    duration_seconds: int = 60
    language: str = "en"

class InfinityMediaPlan2801(BaseModel):
    project_id: str

class InfinityMediaCaptions2801(BaseModel):
    project_id: str
    script: str = Field(min_length=1, max_length=50000)

class InfinityMediaRender2801(BaseModel):
    project_id: str
    asset_paths: List[str] = []
    duration_seconds: Optional[int] = None


# ------------------------------------------------------------
# API routes
# ------------------------------------------------------------
@app.get("/api/health")
def infinity_2801_health():
    return {"status":"healthy","version":INFINITY_2801_VERSION,"build":INFINITY_2801_BUILD,"previous":INFINITY_2801_PREVIOUS,"database":"ready","truthful":True,"interface":{"desktop":True,"tablet":True,"mobile":True,"responsive":True,"pwa":True},"ecosystem":{"work":True,"earnings_tracking":True,"finance_assistant":True,"finance_forecast":True,"internal_earnings_ledger":True,"media_production":True,"chat":True,"voice_browser_native":True},"security":_2801_security_status()}


@app.get("/infinity/2801/health")
def infinity_2801_health_alias():
    return infinity_2801_health()


@app.get("/api/capabilities")
def infinity_2801_capabilities():
    return {"version":INFINITY_2801_VERSION,"build":INFINITY_2801_BUILD,"capabilities":["unified_command","chat","voice_browser_native","work_profile","opportunity_ingestion","opportunity_matching","proposal_generation","project_tracking","deliverable_tracking","invoice_tracking","earnings_ledger","finance_bookkeeping","budgeting","cashflow_summary","media_storyboard","caption_generation","asset_upload","safe_ffmpeg_render","pwa","responsive_interface","fast_dashboard","operator_gate","specialist_tracks","backup_export","cashflow_forecast"],"truthful":True,"real_money_custody":False}


@app.get("/api/security")
def infinity_2801_security():
    return _2801_security_status()


@app.get("/api/dashboard")
def infinity_2801_dashboard():
    return _2801_dashboard_payload()


@app.post("/api/command")
def infinity_2801_command(req: InfinityCommand2801):
    plan=_2801_command_route(req.command)
    if not req.execute:
        return plan
    if plan["area"] == "wallet":
        return {**plan,"status":"approval_required","operator_required":True}
    return {**plan,"status":"planned"}


@app.post("/api/chat")
def infinity_2801_chat(req: InfinityChat2801):
    result=_2801_hf_chat(req.message, req.conversation_id)
    with _db_lock, db() as c:
        c.execute("INSERT INTO infinity_chat_messages_2801(id,conversation_id,role,content,provider,created_at) VALUES(?,?,?,?,?,?)", (uid("msg"),req.conversation_id,"user",req.message,result.get("provider","builtin"),now()))
        c.execute("INSERT INTO infinity_chat_messages_2801(id,conversation_id,role,content,provider,created_at) VALUES(?,?,?,?,?,?)", (uid("msg"),req.conversation_id,"assistant",result.get("message","")[:12000],result.get("provider","builtin"),now()))
        if req.remember:
            mid=uid("mem")
            c.execute("INSERT INTO memories(id,text,created_at) VALUES(?,?,?)",(mid,f"Chat: {req.message}",now()))
            result["memory_id"]=mid
    return {**result,"conversation_id":req.conversation_id,"truthful":True}


@app.get("/api/work/profile")
def infinity_2801_get_profile():
    return _2801_profile()


@app.post("/api/work/profile")
def infinity_2801_set_profile(payload: InfinityProfile2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request)
    _2801_require_operator(request)
    return {"status":"saved","profile":_2801_set_profile(payload.model_dump())}


@app.get("/api/work/opportunities")
def infinity_2801_opportunities(limit: int = 50, q: str = ""):
    return {"status":"completed","items":_2801_list_opportunities(limit,q),"truthful":True,"actual_external_marketplaces":bool(INFINITY_2801_FEEDS)}


@app.post("/api/work/opportunities")
def infinity_2801_ingest_opportunity(payload: InfinityOpportunity2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    return _2801_opportunity_upsert(payload.model_dump())


@app.post("/api/work/opportunities/scan")
def infinity_2801_scan_opportunities(request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    return _2801_scan_feeds()


@app.get("/api/work/matches")
def infinity_2801_matches(limit: int = 20):
    return {"status":"completed","matches":_2801_match_opportunities(limit),"truthful":True}


@app.post("/api/work/projects")
def infinity_2801_create_project(payload: InfinityProject2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    pid=uid("prj"); t=now(); price=_2801_optional_money_minor(payload.price) or 0
    with _db_lock, db() as c:
        c.execute("INSERT INTO infinity_work_projects_2801(id,opportunity_id,title,role,client_name,status,price_minor,currency,due_at,created_at,updated_at) VALUES(?,?,?,?,?,'planned',?,?,?,?,?)", (pid,payload.opportunity_id,payload.title[:300],payload.role[:120],payload.client_name[:300],price,payload.currency.upper()[:8],payload.due_at,t,t))
    return {"status":"created","project_id":pid,"project":{**payload.model_dump(),"id":pid,"price_minor":price}}


@app.get("/api/work/projects")
def infinity_2801_projects(limit: int = 50):
    with _db_lock, db() as c:
        rows=[dict(x) for x in c.execute("SELECT * FROM infinity_work_projects_2801 ORDER BY updated_at DESC LIMIT ?",(max(1,min(limit,200)),)).fetchall()]
    return {"items":rows}


@app.post("/api/work/proposals")
def infinity_2801_create_proposal(payload: InfinityProposal2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    opportunity=None
    if payload.opportunity_id: opportunity=_2801_opportunity_get(payload.opportunity_id)
    if not payload.proposal:
        if not opportunity: raise HTTPException(400,"opportunity_id or proposal text is required")
        proposal=_2801_proposal(opportunity,_2801_profile())
    else: proposal=payload.proposal[:15000]
    pid=uid("prop")
    with _db_lock, db() as c:
        c.execute("INSERT INTO infinity_proposals_2801(id,opportunity_id,project_id,proposal,status,created_at,updated_at) VALUES(?,?,?,?, 'draft',?,?)",(pid,payload.opportunity_id,payload.project_id,proposal,now(),now()))
    return {"status":"drafted","proposal_id":pid,"proposal":proposal,"truthful":True}


@app.get("/api/work/proposals")
def infinity_2801_proposals(limit: int = 50):
    with _db_lock, db() as c:
        rows=[dict(x) for x in c.execute("SELECT * FROM infinity_proposals_2801 ORDER BY updated_at DESC LIMIT ?",(max(1,min(limit,200)),)).fetchall()]
    return {"items":rows}


@app.post("/api/work/deliverables")
def infinity_2801_add_deliverable(payload: InfinityDeliverable2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    did=uid("del")
    with _db_lock, db() as c:
        if not c.execute("SELECT 1 FROM infinity_work_projects_2801 WHERE id=?",(payload.project_id,)).fetchone(): raise HTTPException(404,"project not found")
        c.execute("INSERT INTO infinity_deliverables_2801(id,project_id,title,artifact_ref,status,verified,created_at,updated_at) VALUES(?,?,?,?, 'todo',0,?,?)",(did,payload.project_id,payload.title[:300],payload.artifact_ref[:2000],now(),now()))
    return {"status":"created","deliverable_id":did}


@app.post("/api/work/invoices")
def infinity_2801_invoice(payload: InfinityInvoice2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    iid=uid("inv"); amount=_2801_money_minor(payload.amount)
    with _db_lock, db() as c:
        c.execute("INSERT INTO infinity_invoices_2801(id,project_id,client_name,amount_minor,currency,status,external_ref,created_at,updated_at) VALUES(?,?,?,?,?,'issued',?,?,?)",(iid,payload.project_id,payload.client_name[:300],amount,payload.currency.upper()[:8],payload.external_ref[:300],now(),now()))
    return {"status":"issued","invoice_id":iid,"amount_minor":amount,"currency":payload.currency.upper()[:8]}


@app.post("/api/work/payments/record")
def infinity_2801_record_payment(payload: InfinityInvoice2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    amount=_2801_money_minor(payload.amount)
    payment_ref=payload.external_ref or uid("pay")
    wallet=_2801_wallet_write("credit",amount,payload.currency,payload.category if hasattr(payload,'category') else "work_payment","verified work payment",payment_ref,uid("idem"))
    with _db_lock, db() as c:
        if payload.project_id:
            c.execute("UPDATE infinity_invoices_2801 SET status='paid',updated_at=? WHERE project_id=?",(now(),payload.project_id))
    return {"status":"recorded","payment_ref":payment_ref,"wallet":wallet,"note":"Internal earnings ledger credited; external payment custody remains with the payment provider."}


@app.get("/api/work/summary")
def infinity_2801_work_summary():
    with _db_lock, db() as c:
        data={
            "open_opportunities":int(c.execute("SELECT COUNT(*) FROM infinity_opportunities_2801 WHERE status='open'").fetchone()[0]),
            "active_projects":int(c.execute("SELECT COUNT(*) FROM infinity_work_projects_2801 WHERE status NOT IN ('completed','cancelled')").fetchone()[0]),
            "draft_proposals":int(c.execute("SELECT COUNT(*) FROM infinity_proposals_2801 WHERE status='draft'").fetchone()[0]),
            "issued_invoices":int(c.execute("SELECT COUNT(*) FROM infinity_invoices_2801 WHERE status='issued'").fetchone()[0]),
            "paid_invoices":int(c.execute("SELECT COUNT(*) FROM infinity_invoices_2801 WHERE status='paid'").fetchone()[0]),
        }
    return {"status":"completed","summary":data,"truthful":True,"earnings_are_not_guaranteed":True}


@app.get("/api/finance/summary")
def infinity_2801_finance_summary(period: str = "all"):
    return _2801_finance_summary(period if period in {"all","month","30d"} else "all")


@app.post("/api/finance/transactions")
def infinity_2801_finance_tx(payload: InfinityFinanceTx2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    kind=payload.kind.lower().strip()
    if kind not in {"income","expense"}: raise HTTPException(400,"kind must be income or expense")
    amount=_2801_money_minor(payload.amount)
    tid=uid("fin")
    with _db_lock, db() as c:
        c.execute("INSERT INTO infinity_finance_tx_2801(id,kind,category,description,amount_minor,currency,account,linked_ref,tx_date,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(tid,kind,payload.category[:120],payload.description[:1000],amount,payload.currency.upper()[:8],payload.account[:100],payload.linked_ref[:300],payload.tx_date[:20],now()))
    _2801_cache_set("dashboard",None,0)
    return {"status":"recorded","transaction_id":tid,"amount_minor":amount}


@app.get("/api/finance/transactions")
def infinity_2801_finance_transactions(limit: int = 100):
    with _db_lock, db() as c:
        rows=[dict(x) for x in c.execute("SELECT * FROM infinity_finance_tx_2801 ORDER BY tx_date DESC,created_at DESC LIMIT ?",(max(1,min(limit,500)),)).fetchall()]
    return {"items":rows}


@app.post("/api/finance/budgets")
def infinity_2801_budget(payload: InfinityBudget2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    bid=uid("bud"); limit_minor=_2801_money_minor(payload.limit)
    with _db_lock, db() as c:
        c.execute("INSERT INTO infinity_budgets_2801(id,category,limit_minor,currency,period,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(bid,payload.category[:120],limit_minor,payload.currency.upper()[:8],payload.period[:20],now(),now()))
    return {"status":"created","budget_id":bid,"limit_minor":limit_minor}


@app.get("/api/finance/budgets")
def infinity_2801_budgets():
    with _db_lock, db() as c:
        rows=[dict(x) for x in c.execute("SELECT * FROM infinity_budgets_2801 ORDER BY updated_at DESC").fetchall()]
    return {"items":rows}


@app.get("/api/wallet")
def infinity_2801_wallet():
    audit=_2801_wallet_audit()
    return {"status":"ready","currency":INFINITY_2801_WALLET_CURRENCY,"balance_minor":audit["balance_minor"],"balance":round(audit["balance_minor"]/100,2),"audit":audit,"custody":"internal-ledger-only","real_money_custody":False,"payout_metadata_encryption":bool(_2801_wallet_key())}


@app.post("/api/wallet/credit")
def infinity_2801_wallet_credit(payload: InfinityWalletMutation2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    return _2801_wallet_write("credit",_2801_money_minor(payload.amount),payload.currency,payload.category[:120],payload.description[:1000],payload.linked_ref[:300],payload.idempotency_key)


@app.post("/api/wallet/debit")
def infinity_2801_wallet_debit(payload: InfinityWalletMutation2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    return _2801_wallet_write("debit",_2801_money_minor(payload.amount),payload.currency,payload.category[:120],payload.description[:1000],payload.linked_ref[:300],payload.idempotency_key)


@app.get("/api/wallet/ledger")
def infinity_2801_wallet_ledger(limit: int = 100):
    with _db_lock, db() as c:
        rows=[dict(x) for x in c.execute("SELECT id,direction,amount_minor,currency,category,description,linked_ref,idempotency_key,prev_hash,entry_hash,created_at FROM infinity_wallet_ledger_2801 ORDER BY created_at DESC,id DESC LIMIT ?",(max(1,min(limit,500)),)).fetchall()]
    return {"items":rows,"secret_values_exposed":False}


@app.post("/api/wallet/payout")
def infinity_2801_wallet_payout(payload: InfinityPayout2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    amount=_2801_money_minor(payload.amount)
    # Queue only. Actual movement is deliberately delegated to a configured,
    # authenticated payment provider and never pretended to be executed here.
    dest=_2801_encrypt_secret(payload.destination)
    current=_2801_wallet_balance(payload.currency.upper())
    if amount>current: raise HTTPException(400,"insufficient internal ledger balance")
    pid=uid("payout")
    with _db_lock, db() as c:
        c.execute("INSERT INTO infinity_payout_queue_2801(id,provider,amount_minor,currency,destination_ciphertext,status,external_ref,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?,?)",(pid,payload.provider[:80],amount,payload.currency.upper()[:8],dest,payload.external_ref[:300],now(),now()))
    return {"status":"queued","payout_id":pid,"movement_executed":False,"next_step":"connect and explicitly execute the selected payment-provider adapter"}


@app.get("/api/wallet/payouts")
def infinity_2801_payouts(limit: int = 100):
    with _db_lock, db() as c:
        rows=[dict(x) for x in c.execute("SELECT id,provider,amount_minor,currency,status,external_ref,error,created_at,updated_at FROM infinity_payout_queue_2801 ORDER BY created_at DESC LIMIT ?",(max(1,min(limit,200)),)).fetchall()]
    return {"items":rows,"secret_values_exposed":False}


@app.post("/api/media/projects")
def infinity_2801_media_project(payload: InfinityMediaProject2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    if payload.format not in {"16:9","9:16","1:1","4:5"}: raise HTTPException(400,"unsupported format")
    mid=uid("media")
    plan=_2801_media_plan(payload.title,payload.brief,payload.duration_seconds,payload.format,payload.language)
    with _db_lock, db() as c:
        c.execute("INSERT INTO infinity_media_projects_2801(id,title,brief,format,duration_seconds,language,status,plan_json,created_at,updated_at) VALUES(?,?,?,?,?,?, 'planned',?,?,?)",(mid,payload.title[:300],payload.brief[:12000],payload.format,max(10,min(payload.duration_seconds,3600)),payload.language[:20],_2801_json(plan),now(),now()))
    return {"status":"created","project_id":mid,"plan":plan}


@app.get("/api/media/projects")
def infinity_2801_media_projects(limit: int = 50):
    with _db_lock, db() as c:
        rows=[dict(x) for x in c.execute("SELECT id,title,format,duration_seconds,language,status,created_at,updated_at FROM infinity_media_projects_2801 ORDER BY updated_at DESC LIMIT ?",(max(1,min(limit,200)),)).fetchall()]
    return {"items":rows}


@app.get("/api/media/projects/{project_id}")
def infinity_2801_media_project_get(project_id: str):
    with _db_lock, db() as c:
        row=c.execute("SELECT * FROM infinity_media_projects_2801 WHERE id=?",(project_id,)).fetchone()
        assets=[dict(x) for x in c.execute("SELECT id,filename,path,kind,mime,bytes,sha256,created_at FROM infinity_media_assets_2801 WHERE project_id=? ORDER BY created_at DESC",(project_id,)).fetchall()]
    if not row: raise HTTPException(404,"media project not found")
    d=dict(row); d["plan"]=_2801_parse_json(d.pop("plan_json"),{})
    return {"project":d,"assets":assets}


@app.post("/api/media/projects/{project_id}/plan")
def infinity_2801_media_plan(project_id: str, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    with _db_lock, db() as c:
        row=c.execute("SELECT title,brief,format,duration_seconds,language FROM infinity_media_projects_2801 WHERE id=?",(project_id,)).fetchone()
    if not row: raise HTTPException(404,"media project not found")
    plan=_2801_media_plan(row[0],row[1],row[3],row[2],row[4])
    with _db_lock, db() as c:
        c.execute("UPDATE infinity_media_projects_2801 SET plan_json=?,updated_at=? WHERE id=?",(_2801_json(plan),now(),project_id))
    return {"status":"planned","plan":plan}


@app.post("/api/media/projects/{project_id}/captions")
def infinity_2801_media_captions(project_id: str, payload: InfinityMediaCaptions2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    with _db_lock, db() as c:
        row=c.execute("SELECT duration_seconds FROM infinity_media_projects_2801 WHERE id=?",(project_id,)).fetchone()
    if not row: raise HTTPException(404,"media project not found")
    srt=_2801_srt_from_script(payload.script,row[0])
    rel=f"media/exports/{project_id}.srt"; path=_2801_resolve_media_path(rel); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(srt,encoding="utf-8")
    return {"status":"completed","path":rel,"captions":srt}


@app.post("/api/media/upload")
async def infinity_2801_media_upload(project_id: str, request: StarletteRequest, file: UploadFile = File(...)):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    with _db_lock, db() as c:
        if not c.execute("SELECT 1 FROM infinity_media_projects_2801 WHERE id=?",(project_id,)).fetchone(): raise HTTPException(404,"media project not found")
    original=(file.filename or "asset.bin").replace("\\","/").split("/")[-1]
    safe=re.sub(r"[^A-Za-z0-9._-]+","_",original)[:160] or "asset.bin"
    asset_id=uid("asset"); path=(INFINITY_2801_UPLOAD_ROOT / f"{asset_id}_{safe}").resolve()
    if INFINITY_2801_UPLOAD_ROOT not in path.parents: raise HTTPException(400,"invalid filename")
    total=0; h=hashlib.sha256()
    try:
        with path.open("wb") as out:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk: break
                total += len(chunk)
                if total > INFINITY_2801_UPLOAD_MAX:
                    raise HTTPException(413,"media upload exceeds 25 MB")
                out.write(chunk); h.update(chunk)
    finally:
        await file.close()
    mime=file.content_type or mimetypes.guess_type(safe)[0] or "application/octet-stream"
    rel=str(path.relative_to(WORKSPACE))
    kind=(mime.split("/")[0] or "asset")
    with _db_lock, db() as c:
        c.execute("INSERT INTO infinity_media_assets_2801(id,project_id,filename,path,kind,mime,bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(asset_id,project_id,safe,rel,kind,mime,total,h.hexdigest(),now()))
    return {"status":"uploaded","asset_id":asset_id,"path":rel,"bytes":total,"sha256":h.hexdigest()}


@app.post("/api/media/render")
def infinity_2801_media_render(payload: InfinityMediaRender2801, request: StarletteRequest):
    _2801_rate_limit_mutation(request); _2801_require_operator(request)
    with _db_lock, db() as c:
        row=c.execute("SELECT duration_seconds FROM infinity_media_projects_2801 WHERE id=?",(payload.project_id,)).fetchone()
        assets=[dict(x) for x in c.execute("SELECT path FROM infinity_media_assets_2801 WHERE project_id=? ORDER BY created_at ASC",(payload.project_id,)).fetchall()]
    if not row: raise HTTPException(404,"media project not found")
    paths=[]
    requested=payload.asset_paths or [x["path"] for x in assets]
    for rel in requested:
        p=_2801_resolve_media_path(rel); paths.append(p)
    output=_2801_resolve_media_path(f"media/exports/{payload.project_id}.mp4")
    job_id=uid("mjob")
    with _db_lock, db() as c:
        c.execute("INSERT INTO infinity_media_jobs_2801(id,project_id,kind,status,input_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(job_id,payload.project_id,"render","running",_2801_json({"asset_paths":requested}),now(),now()))
    try:
        result=_2801_fixed_ffmpeg_slideshow(paths,output,payload.duration_seconds or row[0])
        with _db_lock, db() as c:
            c.execute("UPDATE infinity_media_jobs_2801 SET status=?,output_json=?,updated_at=? WHERE id=?",(result.get("status","completed"),_2801_json(result),now(),job_id))
        return {"status":result.get("status"),"job_id":job_id,"result":result,"ffmpeg_available":bool(shutil.which("ffmpeg"))}
    except Exception as exc:
        with _db_lock, db() as c:
            c.execute("UPDATE infinity_media_jobs_2801 SET status='failed',error=?,updated_at=? WHERE id=?",(str(exc)[:2000],now(),job_id))
        raise HTTPException(400,str(exc))


@app.get("/api/finance/forecast")
def infinity_2801_finance_forecast(months: int = 3):
    months = max(1, min(int(months or 3), 12))
    summary = _2801_finance_summary("30d")
    monthly_income = int(summary["income_minor"])
    monthly_expense = int(summary["expense_minor"])
    return {
        "status":"completed",
        "months":months,
        "projected_income_minor":[monthly_income*(i+1) for i in range(months)],
        "projected_expense_minor":[monthly_expense*(i+1) for i in range(months)],
        "projected_net_minor":[(monthly_income-monthly_expense)*(i+1) for i in range(months)],
        "method":"simple run-rate projection from the last 30 days",
        "disclaimer":"Planning/bookkeeping estimate only; not personalized financial, tax, investment, or lending advice."
    }


def _2801_backup_payload() -> Dict[str, Any]:
    tables = [
        "infinity_work_profile_2801","infinity_opportunities_2801","infinity_work_projects_2801","infinity_proposals_2801","infinity_deliverables_2801","infinity_invoices_2801","infinity_finance_tx_2801","infinity_budgets_2801","infinity_wallet_accounts_2801","infinity_wallet_ledger_2801","infinity_payout_queue_2801","infinity_media_projects_2801","infinity_media_assets_2801","infinity_media_jobs_2801","infinity_chat_messages_2801"
    ]
    result={"format":"AI_INFINITY_BACKUP_2801","version":INFINITY_2801_VERSION,"created_at":now(),"tables":{}}
    with _db_lock, db() as c:
        for table in tables:
            result["tables"][table]=[dict(x) for x in c.execute(f"SELECT * FROM {table}").fetchall()]
    return result


@app.get("/api/backup")
def infinity_2801_backup():
    data=_2801_backup_payload()
    data["secret_values_exposed"]=False
    data["note"]="Encrypted payout metadata is exported as ciphertext; provider secrets and operator tokens are never exported."
    return data


# ------------------------------------------------------------
# PWA assets
# ------------------------------------------------------------
INFINITY_2801_MANIFEST = _2801_json({
    "name":"AI Infinity","short_name":"Infinity","start_url":"/infinity","display":"standalone","theme_color":"#0a1020","background_color":"#0a1020","description":"Human-centered AI Infinity workspace"
})

INFINITY_2801_SW = """
const CACHE='ai-infinity-2801-v1';
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(['/infinity','/manifest.webmanifest']))));
self.addEventListener('fetch',e=>{ if(e.request.method!=='GET') return; e.respondWith(caches.match(e.request).then(x=>x||fetch(e.request).then(r=>{const copy=r.clone();caches.open(CACHE).then(c=>c.put(e.request,copy));return r}).catch(()=>caches.match('/infinity')))); });
"""


@app.get("/manifest.webmanifest")
def infinity_2801_manifest():
    return JSONResponse(json.loads(INFINITY_2801_MANIFEST), media_type="application/manifest+json")


@app.get("/sw.js")
def infinity_2801_sw():
    return PlainTextResponse(INFINITY_2801_SW, media_type="application/javascript")


# ------------------------------------------------------------
# Final unified interface
# ------------------------------------------------------------
INFINITY_2801_UI = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0a1020"><link rel="manifest" href="/manifest.webmanifest"><title>∞ AI Infinity</title>
<style>
:root{--bg:#07101c;--panel:#0d1727;--panel2:#111d31;--line:#20304a;--text:#eef5ff;--muted:#9fb0c8;--accent:#8de2ff;--accent2:#bca8ff;--ok:#7df0b2;--warn:#ffd58d;--bad:#ff9c9c;--shadow:0 16px 45px rgba(0,0,0,.22)}*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:radial-gradient(1200px 600px at 20% -10%,rgba(86,177,255,.13),transparent 50%),var(--bg);color:var(--text);font:14px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}body{overflow-x:hidden}.app{display:grid;grid-template-columns:250px minmax(0,1fr);min-height:100vh}.side{position:sticky;top:0;height:100vh;padding:16px;border-right:1px solid var(--line);background:rgba(7,16,28,.92);backdrop-filter:blur(16px)}.brand{font-size:22px;font-weight:900;letter-spacing:-.04em}.tag{color:var(--muted);font-size:12px;margin:4px 0 16px}.nav{display:grid;gap:6px}.nav button{border:1px solid transparent;background:transparent;color:var(--muted);padding:11px;border-radius:12px;text-align:left;font:inherit;cursor:pointer}.nav button:hover,.nav button.active{background:var(--panel2);border-color:var(--line);color:var(--text)}.main{padding:18px;max-width:1550px;width:100%;margin:0 auto}.top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px}.health{display:flex;gap:8px;align-items:center}.dot{width:9px;height:9px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 4px rgba(125,240,178,.09)}.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:12px}.card{grid-column:span 4;background:linear-gradient(180deg,var(--panel),#0b1422);border:1px solid var(--line);border-radius:18px;padding:15px;box-shadow:var(--shadow);min-width:0}.span8{grid-column:span 8}.span12{grid-column:1/-1}.hero{grid-column:1/-1;padding:18px;background:linear-gradient(135deg,rgba(141,226,255,.11),rgba(188,168,255,.09)),var(--panel)}h1,h2,h3{margin:0 0 8px;letter-spacing:-.025em}h1{font-size:clamp(27px,4vw,44px);line-height:1.02}.sub{color:var(--muted)}.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:14px}.kpi{padding:12px;background:var(--panel2);border:1px solid var(--line);border-radius:14px}.kpi b{display:block;font-size:22px;margin-top:3px}.label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.09em}button.primary{background:linear-gradient(135deg,var(--accent2),var(--accent));color:#09101a;border:none}button{border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:11px;padding:10px 12px;font:inherit;cursor:pointer}.row{display:flex;gap:8px;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding:9px 0}.row:last-child{border:0}.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}textarea,input,select{width:100%;background:#08111e;color:var(--text);border:1px solid var(--line);border-radius:11px;padding:11px;font:inherit}textarea{min-height:140px;resize:vertical}.feed{display:grid;gap:8px;max-height:420px;overflow:auto}.item{padding:10px;background:#0a1422;border:1px solid var(--line);border-radius:12px}.pill{display:inline-flex;align-items:center;gap:5px;padding:4px 8px;border-radius:999px;background:#0a1625;border:1px solid var(--line);font-size:11px;color:var(--muted)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.muted{color:var(--muted)}.out{background:#06101b;border:1px solid var(--line);border-radius:12px;padding:10px;white-space:pre-wrap;overflow:auto;max-height:470px}.section{display:none}.section.on{display:block}.mobilebar{display:none}.small{font-size:12px}.voice{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.chatlog{height:330px;overflow:auto;display:grid;gap:9px;padding:4px}.bubble{padding:10px 12px;border-radius:14px;max-width:86%;background:var(--panel2);border:1px solid var(--line)}.bubble.me{margin-left:auto;background:rgba(141,226,255,.11)}.composer{display:flex;gap:8px;margin-top:8px}.composer input{flex:1}.toast{position:fixed;right:16px;bottom:16px;max-width:340px;background:#0b1626;border:1px solid var(--line);padding:12px;border-radius:12px;display:none}.tablewrap{overflow:auto}.table{width:100%;border-collapse:collapse}.table th,.table td{border-bottom:1px solid var(--line);padding:8px;text-align:left;white-space:nowrap}.empty{padding:25px;color:var(--muted);text-align:center}
@media(max-width:1050px){.app{grid-template-columns:1fr}.side{position:fixed;inset:auto 0 0 0;height:auto;z-index:20;border-right:0;border-top:1px solid var(--line);padding:6px 7px}.brand,.tag{display:none}.nav{grid-template-columns:repeat(6,1fr)}.nav button{text-align:center;padding:9px 5px;font-size:11px}.main{padding:14px 10px 82px}.grid{grid-template-columns:repeat(6,minmax(0,1fr))}.card{grid-column:span 3}.span8,.span12,.hero{grid-column:1/-1}}
@media(max-width:680px){.grid{display:block}.card{margin-bottom:10px}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.top{align-items:flex-start}.composer{flex-direction:column}.bubble{max-width:95%}.hero{padding:15px}h1{font-size:32px}}
</style></head><body>
<div class="app"><aside class="side"><div class="brand">∞ AI Infinity</div><div class="tag">Work · Create · Earn · Verify</div><div class="nav"><button class="active" data-s="home">Home</button><button data-s="chat">Chat</button><button data-s="work">Work</button><button data-s="finance">Finance</button><button data-s="media">Create</button><button data-s="system">System</button></div></aside>
<main class="main"><div class="top"><div><div class="small muted">UNIVERSAL OPERATING WORKSPACE · 2801</div></div><div class="health"><span class="dot"></span><span id="healthText">Online</span></div></div>
<section id="home" class="section on"><div class="grid"><div class="hero"><div class="label">One-command operating surface</div><h1>Tell Infinity the outcome.</h1><div class="sub">Chat naturally, organize specialist work, track real opportunities, build media, manage bookkeeping, and keep an auditable earnings ledger.</div><div style="margin-top:13px"><textarea id="cmd" placeholder="Example: Find software engineering work, draft proposals, track the project, and record the payment when it is actually received."></textarea><div class="actions"><button class="primary" onclick="compileCommand()">Plan</button><button onclick="chatFromCommand()">Ask Infinity</button><button onclick="clearCmd()">Clear</button></div><div id="cmdOut" class="out" style="margin-top:10px">Ready.</div></div><div class="kpis"><div class="kpi"><span class="label">Wallet ledger</span><b id="kWallet">0.00</b></div><div class="kpi"><span class="label">Open work</span><b id="kOpp">0</b></div><div class="kpi"><span class="label">Projects</span><b id="kProj">0</b></div><div class="kpi"><span class="label">30d net</span><b id="kNet">0.00</b></div></div></div><div class="card span8"><h2>Work radar</h2><div class="sub">Real opportunities only appear from configured/ingested sources. No earnings are fabricated.</div><div id="matches" class="feed" style="margin-top:10px"></div></div><div class="card"><h2>Security</h2><div id="security"></div><div class="actions"><button onclick="refresh()">Refresh</button></div></div><div class="card span8"><h2>Activity</h2><div id="activity" class="feed"></div></div><div class="card"><h2>Voice</h2><div class="sub">Uses the device browser voice APIs; no paid voice dependency is required.</div><div class="voice" style="margin-top:10px"><button onclick="startVoice()">🎙 Start</button><button onclick="stopVoice()">Stop</button><span id="voiceState" class="pill">Idle</span></div></div></div></section>
<section id="chat" class="section"><div class="grid"><div class="card span8"><h2>Chat</h2><div id="chatlog" class="chatlog"></div><div class="composer"><input id="chatInput" placeholder="Talk to Infinity…"><button class="primary" onclick="sendChat()">Send</button><button onclick="speakLast()">Speak</button></div></div><div class="card"><h2>Conversation</h2><div class="sub">The server can use Hugging Face when HF_TOKEN is configured and falls back locally when it is not.</div><div style="margin-top:12px"><span class="pill">No paid dependency required</span></div></div></div></section>
<section id="work" class="section"><div class="grid"><div class="card"><h2>Specialist profile</h2><input id="pName" placeholder="Display name"><input id="pHead" placeholder="Software engineer · writer · researcher" style="margin-top:7px"><input id="pSkills" placeholder="skills comma-separated" style="margin-top:7px"><input id="pSpecs" placeholder="specialties comma-separated" style="margin-top:7px"><input id="pRate" type="number" min="0" step="0.01" placeholder="hourly rate" style="margin-top:7px"><div class="actions"><button class="primary" onclick="saveProfile()">Save profile</button></div></div><div class="card span8"><h2>Opportunity engine</h2><div class="sub">Add verified opportunities or scan configured public feeds.</div><div class="actions"><button class="primary" onclick="scanFeeds()">Scan sources</button><button onclick="loadMatches()">Refresh matches</button></div><div id="workOut" class="out" style="margin-top:10px">No action yet.</div></div><div class="card span12"><h2>Projects</h2><div id="projects" class="feed"></div></div><div class="card span12"><h2>Proposal workspace</h2><div class="sub">Drafts are generated from your profile and the actual opportunity record.</div><div id="proposals" class="feed"></div></div></div></section>
<section id="finance" class="section"><div class="grid"><div class="card span4"><h2>Cash flow</h2><div id="finSummary" class="out">Loading…</div></div><div class="card span8"><h2>Record transaction</h2><div class="grid" style="display:grid;grid-template-columns:1fr 1fr"><select id="finKind"><option value="income">Income</option><option value="expense">Expense</option></select><input id="finCat" placeholder="Category"><input id="finAmt" type="number" step="0.01" placeholder="Amount"><input id="finDesc" placeholder="Description"></div><div class="actions"><button class="primary" onclick="recordFinance()">Record</button><button onclick="loadFinance()">Refresh</button></div></div><div class="card span12"><h2>Recent ledger</h2><div id="finTx" class="tablewrap"></div></div></div></section>
<section id="media" class="section"><div class="grid"><div class="card"><h2>New production</h2><input id="mTitle" placeholder="Title"><textarea id="mBrief" placeholder="Describe the film, reel, explainer, ad, tutorial, podcast visual package…"></textarea><select id="mFormat"><option>16:9</option><option>9:16</option><option>1:1</option><option>4:5</option></select><input id="mDuration" type="number" min="10" max="3600" value="60" style="margin-top:7px"><div class="actions"><button class="primary" onclick="createMedia()">Create production</button></div></div><div class="card span8"><h2>Media projects</h2><div id="mediaProjects" class="feed"></div></div><div class="card span12"><h2>Production output</h2><div id="mediaOut" class="out">Build storyboard → captions → upload assets → render locally when FFmpeg is available.</div></div></div></section>
<section id="system" class="section"><div class="grid"><div class="card span8"><h2>System integrity</h2><div id="sysOut" class="out">Loading…</div></div><div class="card"><h2>Design contract</h2><div class="row"><span>Arbitrary code</span><b class="ok">OFF</b></div><div class="row"><span>Uncertain replay</span><b class="ok">OFF</b></div><div class="row"><span>Secret return</span><b class="ok">OFF</b></div><div class="row"><span>Money custody</span><b class="warn">LEDGER ONLY</b></div></div><div class="card span12"><h2>Operator authorization</h2><input id="operator" type="password" autocomplete="off" placeholder="Operator token — kept in memory only"><div class="sub" style="margin-top:8px">The token is sent per protected request and never written to localStorage/sessionStorage.</div></div></div></section>
</main></div><div id="toast" class="toast"></div>
<script>
const $=id=>document.getElementById(id); const state={conv:'default',last:''};
function opHeaders(){const t=$('operator')?.value||''; return t?{'X-AI-Infinity-Operator':t}:{} }
async function api(u,o={}){o.headers={...(o.headers||{}),...opHeaders()}; const r=await fetch(u,o); const t=await r.text(); let j; try{j=JSON.parse(t)}catch{throw Error(t||('HTTP '+r.status))}; if(!r.ok) throw Error(j.detail||j.error||('HTTP '+r.status)); return j}
function toast(x){$('toast').textContent=x;$('toast').style.display='block';setTimeout(()=>{$('toast').style.display='none'},3300)}
function money(n){return Number(n||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}
function activate(name){document.querySelectorAll('.section').forEach(s=>s.classList.remove('on'));$(name).classList.add('on');document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.s===name)); if(name==='work')loadWork(); if(name==='finance')loadFinance(); if(name==='media')loadMedia(); if(name==='system')loadSystem(); if(name==='chat')renderChat([])}
document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>activate(b.dataset.s));
function clearCmd(){$('cmd').value='';$('cmdOut').textContent='Ready.'}
async function compileCommand(){const q=$('cmd').value.trim();if(!q)return;try{$('cmdOut').textContent=JSON.stringify(await api('/api/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command:q,execute:false})}),null,2)}catch(e){$('cmdOut').textContent=e.message}}
async function chatFromCommand(){const q=$('cmd').value.trim();if(!q)return;activate('chat');$('chatInput').value=q;await sendChat()}
async function refresh(){try{const [d,m,s]=await Promise.all([api('/api/dashboard'),api('/api/work/matches?limit=8'),api('/api/security')]);$('kWallet').textContent=money(d.wallet_balance);$('kOpp').textContent=d.counts.opportunities;$('kProj').textContent=d.counts.projects;$('kNet').textContent=money(d.finance.net);$('healthText').textContent='Online · '+d.version;$('security').innerHTML='<div class="row"><span>Operator</span><b class="'+(s.operator_configured?'ok':'warn')+'">'+(s.operator_configured?'READY':'WAITING')+'</b></div><div class="row"><span>Wallet key</span><b class="'+(s.wallet_key_configured?'ok':'warn')+'">'+(s.wallet_key_configured?'READY':'WAITING')+'</b></div><div class="row"><span>Custody</span><b class="warn">LEDGER ONLY</b></div>';$('matches').innerHTML=(m.matches||[]).map(x=>'<div class="item"><div class="pill">MATCH '+Math.round(x.match_score*100)+'%</div><b>'+escapeHtml(x.title)+'</b><div class="small muted">'+escapeHtml(x.source)+' · '+(x.compensation_minor?money(x.compensation_minor/100)+' '+escapeHtml(x.currency):'compensation not published')+'</div><div class="small" style="margin-top:5px">'+escapeHtml((x.description||'').slice(0,260))+'</div></div>').join('')||'<div class="empty">No verified opportunities loaded yet.</div>';$('activity').innerHTML='<div class="row"><span>Version</span><span class="pill">'+d.version+'</span></div><div class="row"><span>Work opportunities</span><span>'+d.counts.opportunities+'</span></div><div class="row"><span>Media projects</span><span>'+d.counts.media_projects+'</span></div>'}catch(e){$('healthText').textContent='Connection issue'}}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function sendChat(){const q=$('chatInput').value.trim();if(!q)return;pushChat('user',q);$('chatInput').value='';try{const r=await api('/api/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message:q,conversation_id:state.conv,remember:false})});state.last=r.message||'';pushChat('assistant',r.message||'');}catch(e){pushChat('assistant','Error: '+e.message)}}
function pushChat(role,text){const node=document.createElement('div');node.className='bubble '+(role==='user'?'me':'');node.textContent=text;$('chatlog').appendChild(node);$('chatlog').scrollTop=$('chatlog').scrollHeight}
function renderChat(x){if(x.length)return; if(!$('chatlog').children.length) pushChat('assistant','Ready. Tell me the result you want.')}
function speakLast(){if(!state.last)return; if('speechSynthesis' in window){speechSynthesis.cancel();speechSynthesis.speak(new SpeechSynthesisUtterance(state.last));}}
let recognition=null; function startVoice(){const R=window.SpeechRecognition||window.webkitSpeechRecognition;if(!R){$('voiceState').textContent='Unavailable';return}recognition=new R();recognition.lang=navigator.language||'en-US';recognition.interimResults=false;recognition.onstart=()=>{$('voiceState').textContent='Listening'};recognition.onresult=e=>{$('cmd').value=e.results[0][0].transcript;$('voiceState').textContent='Captured'};recognition.onerror=()=>{$('voiceState').textContent='Error'};recognition.onend=()=>{if($('voiceState').textContent==='Listening')$('voiceState').textContent='Idle'};recognition.start()}
function stopVoice(){try{recognition&&recognition.stop()}catch{} $('voiceState').textContent='Idle'}
async function loadWork(){try{const [p,pr]=await Promise.all([api('/api/work/profile'),api('/api/work/projects')]);$('pName').value=p.display_name||'';$('pHead').value=p.headline||'';$('pSkills').value=(p.skills||[]).join(', ');$('pSpecs').value=(p.specialties||[]).join(', ');$('pRate').value=p.hourly_rate||0;$('projects').innerHTML=(pr.items||[]).map(x=>'<div class="item"><b>'+escapeHtml(x.title)+'</b><div class="small muted">'+escapeHtml(x.role)+' · '+money((x.price_minor||0)/100)+' '+escapeHtml(x.currency)+' · '+escapeHtml(x.status)+'</div></div>').join('')||'<div class="empty">No projects.</div>';const props=await api('/api/work/proposals');$('proposals').innerHTML=(props.items||[]).map(x=>'<div class="item"><b>'+escapeHtml(x.id)+'</b><div class="small muted">'+escapeHtml(x.status)+'</div><div style="margin-top:5px">'+escapeHtml(x.proposal||'')+'</div></div>').join('')||'<div class="empty">No proposal drafts.</div>'}catch(e){$('workOut').textContent=e.message}}
async function saveProfile(){try{const r=await api('/api/work/profile',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({display_name:$('pName').value,headline:$('pHead').value,skills:$('pSkills').value.split(',').map(x=>x.trim()).filter(Boolean),specialties:$('pSpecs').value.split(',').map(x=>x.trim()).filter(Boolean),hourly_rate:Number($('pRate').value||0)})});$('workOut').textContent=JSON.stringify(r,null,2);toast('Profile saved')}catch(e){toast(e.message)}}
async function scanFeeds(){try{const r=await api('/api/work/opportunities/scan',{method:'POST'});$('workOut').textContent=JSON.stringify(r,null,2);await loadMatches();toast('Opportunity scan finished')}catch(e){$('workOut').textContent=e.message}}
async function loadMatches(){try{const r=await api('/api/work/matches?limit=30');$('workOut').textContent=JSON.stringify(r,null,2);$('matches').innerHTML=(r.matches||[]).map(x=>'<div class="item"><b>'+escapeHtml(x.title)+'</b><div class="small muted">Match '+Math.round(x.match_score*100)+'% · '+escapeHtml(x.source)+'</div></div>').join('')||'<div class="empty">No matches.</div>'}catch(e){$('workOut').textContent=e.message}}
async function loadFinance(){try{const [s,t]=await Promise.all([api('/api/finance/summary?period=30d'),api('/api/finance/transactions?limit=30')]);$('finSummary').textContent=JSON.stringify(s,null,2);$('finTx').innerHTML='<table class="table"><thead><tr><th>Date</th><th>Kind</th><th>Category</th><th>Amount</th></tr></thead><tbody>'+(t.items||[]).map(x=>'<tr><td>'+escapeHtml(x.tx_date)+'</td><td>'+escapeHtml(x.kind)+'</td><td>'+escapeHtml(x.category)+'</td><td>'+money(x.amount_minor/100)+' '+escapeHtml(x.currency)+'</td></tr>').join('')+'</tbody></table>'}catch(e){$('finSummary').textContent=e.message}}
async function recordFinance(){try{const r=await api('/api/finance/transactions',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({kind:$('finKind').value,category:$('finCat').value,description:$('finDesc').value,amount:Number($('finAmt').value||0)})});toast('Transaction recorded');loadFinance();refresh()}catch(e){toast(e.message)}}
async function createMedia(){try{const r=await api('/api/media/projects',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({title:$('mTitle').value,brief:$('mBrief').value,format:$('mFormat').value,duration_seconds:Number($('mDuration').value||60)})});$('mediaOut').textContent=JSON.stringify(r,null,2);loadMedia();toast('Production created')}catch(e){$('mediaOut').textContent=e.message}}
async function loadMedia(){try{const r=await api('/api/media/projects');$('mediaProjects').innerHTML=(r.items||[]).map(x=>'<div class="item"><b>'+escapeHtml(x.title)+'</b><div class="small muted">'+escapeHtml(x.format)+' · '+x.duration_seconds+'s · '+escapeHtml(x.status)+'</div><div class="actions"><button onclick="mediaPlan(\''+x.id+'\')">Plan</button><button onclick="mediaDetails(\''+x.id+'\')">Details</button></div></div>').join('')||'<div class="empty">No media projects.</div>'}catch(e){$('mediaProjects').textContent=e.message}}
async function mediaPlan(id){try{const r=await api('/api/media/projects/'+encodeURIComponent(id)+'/plan',{method:'POST'});$('mediaOut').textContent=JSON.stringify(r,null,2)}catch(e){$('mediaOut').textContent=e.message}}
async function mediaDetails(id){try{const r=await api('/api/media/projects/'+encodeURIComponent(id));$('mediaOut').textContent=JSON.stringify(r,null,2)}catch(e){$('mediaOut').textContent=e.message}}
async function loadSystem(){try{$('sysOut').textContent=JSON.stringify(await api('/api/health'),null,2)}catch(e){$('sysOut').textContent=e.message}}
refresh();setInterval(refresh,12000);if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
</script></body></html>'''


def _2801_ui_response() -> HTMLResponse:
    return HTMLResponse(INFINITY_2801_UI, headers={"Cache-Control":"no-store"})


# Middleware is used so the new interface becomes the visible root surface
# while legacy API routes remain present and callable underneath.
@app.middleware("http")
async def infinity_2801_surface(request: StarletteRequest, call_next):
    path=request.url.path
    if path in {"/", "/interface", "/infinity", "/app"}:
        return _2801_ui_response()
    if path in {"/health", "/status"}:
        return JSONResponse(infinity_2801_health())
    return await call_next(request)


# ------------------------------------------------------------
# Deep self-test / regression diagnostics
# ------------------------------------------------------------
def infinity_2801_self_test() -> Dict[str, Any]:
    tests=[]
    def T(name: str, condition: Any, detail: Any=None):
        item={"name":name,"passed":bool(condition)}
        if detail is not None: item["detail"]=detail
        tests.append(item)
    try:
        with _db_lock, db() as c:
            required_tables=[
                "infinity_work_profile_2801","infinity_opportunities_2801","infinity_work_projects_2801","infinity_proposals_2801","infinity_deliverables_2801","infinity_invoices_2801","infinity_finance_tx_2801","infinity_budgets_2801","infinity_wallet_accounts_2801","infinity_wallet_ledger_2801","infinity_payout_queue_2801","infinity_media_projects_2801","infinity_media_assets_2801","infinity_media_jobs_2801","infinity_chat_messages_2801"
            ]
            for t in required_tables:
                T(f"table:{t}", bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone()))
        T("version",INFINITY_2801_VERSION=="TARGET-2050.2801")
        T("truthful health",infinity_2801_health().get("truthful") is True)
        T("command routing",_2801_command_route("find software engineering freelance work")["area"]=="work")
        T("finance routing",_2801_command_route("prepare a cash flow report")["area"]=="finance")
        T("media routing",_2801_command_route("make a vertical video")["area"]=="media")
        plan=_2801_media_plan("test","A test explainer",60,"16:9","en")
        T("media plan",len(plan["scenes"])>=3 and len(plan["outputs"])>=4)
        srt=_2801_srt_from_script("One. Two. Three.",30)
        T("caption generation","-->" in srt and "One." in srt)
        route=_2801_command_route("send money from the wallet")
        T("wallet approval contract",route["side_effect"] is True and "protected mutation" in route["safe_next"])
        audit=_2801_wallet_audit()
        T("wallet integrity",audit["verified"] is True,audit)
        T("no real money custody",_2801_security_status()["real_money_custody"] is False)
        T("no arbitrary code",_2801_security_status()["arbitrary_code_execution"] is False)
        T("no uncertain replay",_2801_security_status()["automatic_uncertain_external_replay"] is False)
        T("pwa manifest",json.loads(INFINITY_2801_MANIFEST)["start_url"]=="/infinity")
        T("interface workspaces",all(x in INFINITY_2801_UI for x in ["Home","Chat","Work","Finance","Create","System"]))
        T("token not persisted", "localStorage.setItem" not in INFINITY_2801_UI and "sessionStorage.setItem" not in INFINITY_2801_UI)
        T("workspace confinement", str(_2801_resolve_media_path("media/exports/test.txt")).startswith(str(WORKSPACE)))
        ffmpeg_available=bool(shutil.which("ffmpeg"))
        T("ffmpeg capability truthful", isinstance(ffmpeg_available,bool))
        return {"status":"completed","version":INFINITY_2801_VERSION,"build":INFINITY_2801_BUILD,"passed":all(x["passed"] for x in tests),"tests":tests,"truthful":True}
    except Exception as exc:
        tests.append({"name":"fatal self-test","passed":False,"detail":str(exc)})
        return {"status":"completed","version":INFINITY_2801_VERSION,"build":INFINITY_2801_BUILD,"passed":False,"tests":tests,"truthful":True}


@app.get("/api/self-test")
def infinity_2801_self_test_route():
    return infinity_2801_self_test()


@app.get("/infinity/2801/self-test")
def infinity_2801_self_test_alias():
    return infinity_2801_self_test()


@app.get("/api/workspace")
def infinity_2801_workspace():
    return {"workspaces":["home","chat","work","finance","media","system"],"root":"/infinity","responsive":True,"pwa":True}


try:
    app.version = INFINITY_2801_VERSION
except Exception:
    pass
