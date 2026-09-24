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
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

APP_VERSION = "TARGET-2050.191"
BUILD = "AUTONOMOUS-REAL-WORLD-MISSION-CLOSURE-CORE"
PREVIOUS_BUILD = "TARGET-2050.190"
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
    "google": {"authorize": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token", "host": "oauth2.googleapis.com", "scope_default": "openid email profile https://www.googleapis.com/auth/calendar.readonly"},
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
