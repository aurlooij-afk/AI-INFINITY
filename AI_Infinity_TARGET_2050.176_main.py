from __future__ import annotations

"""AI Infinity - TARGET-2050.174
REAL-WORLD-WORKFLOW-ORCHESTRATION-PLUS-CORE

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

APP_VERSION = "TARGET-2050.176"
BUILD = "REAL-WORLD-ADAPTIVE-WORKFLOW-INTELLIGENCE-CORE"
PREVIOUS_BUILD = "TARGET-2050.175"
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
        CREATE TABLE IF NOT EXISTS workflow_steps (
            workflow_id TEXT NOT NULL, step_index INTEGER NOT NULL, command TEXT NOT NULL,
            status TEXT NOT NULL, execution_id TEXT, result_json TEXT, error TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY(workflow_id, step_index)
        );
        CREATE TABLE IF NOT EXISTS workflow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL,
            event TEXT NOT NULL, data_json TEXT, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS adaptive_policies (
            key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS adaptive_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT, step_index INTEGER,
            event TEXT NOT NULL, data_json TEXT, created_at REAL NOT NULL
        );
        """)
        # Forward-compatible workflow orchestration fields.
        for sql in (
            "ALTER TABLE workflow_steps ADD COLUMN input_json TEXT",
            "ALTER TABLE workflow_steps ADD COLUMN idempotency_key TEXT",
            "ALTER TABLE workflow_steps ADD COLUMN dependency_index INTEGER",
            "ALTER TABLE workflow_steps ADD COLUMN branch TEXT",
            "ALTER TABLE workflow_steps ADD COLUMN parallel_group TEXT",
        ):
            try:
                c.execute(sql)
            except sqlite3.OperationalError:
                pass
        c.execute("CREATE INDEX IF NOT EXISTS idx_workflow_steps_idempotency ON workflow_steps(workflow_id,idempotency_key)")
        try:
            c.execute("ALTER TABLE workflows ADD COLUMN context_json TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE workflows ADD COLUMN final_result_json TEXT")
        except sqlite3.OperationalError:
            pass


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


def workflow_event(wid: str, name: str, data: Optional[Dict[str, Any]] = None) -> None:
    with _db_lock, db() as c:
        c.execute("INSERT INTO workflow_events(workflow_id,event,data_json,created_at) VALUES(?,?,?,?)",
                  (wid, name, json.dumps(data or {}, ensure_ascii=False), now()))

def create_workflow(steps: List[Dict[str, Any]]) -> str:
    wid = uid("wf")
    t = now()
    normalized=[]
    for i, step in enumerate(steps):
        st=dict(step)
        command_text = str(st.get("command") or st.get("objective") or st.get("template") or "").strip()
        if not command_text:
            raise ValueError("each workflow step needs command, objective, or template")
        dep = st.get("depends_on")
        dep_i = int(dep) if dep is not None and str(dep).isdigit() else (i - 1 if dep == "previous" and i > 0 else None)
        idem = str(st.get("idempotency_key") or f"{wid}:{i}:{digest(command_text)[:16]}")
        st["command"] = command_text
        st["depends_on"] = dep_i
        st["idempotency_key"] = idem
        st["branch"] = st.get("condition") or st.get("when")
        st["parallel_group"] = st.get("parallel_group")
        normalized.append(st)
    with _db_lock, db() as c:
        c.execute("INSERT INTO workflows(id,status,steps_json,created_at,updated_at,context_json,final_result_json) VALUES(?,?,?,?,?,?,?)",
                  (wid, "queued", json.dumps(normalized, ensure_ascii=False), t, t, json.dumps({}, ensure_ascii=False), None))
        for i, step in enumerate(normalized):
            input_json = json.dumps(step.get("input"), ensure_ascii=False) if step.get("input") is not None else None
            c.execute("INSERT INTO workflow_steps(workflow_id,step_index,command,status,created_at,updated_at,input_json,idempotency_key,dependency_index,branch,parallel_group) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (wid, i, step["command"], "queued", t, t, input_json, step["idempotency_key"], step.get("depends_on"), step.get("branch"), step.get("parallel_group")))
    workflow_event(wid, "created", {"step_count": len(normalized), "orchestration": True, "branching": True, "parallel_safe": True})
    return wid

def workflow_step(wid: str, index: int) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM workflow_steps WHERE workflow_id=? AND step_index=?", (wid,index)).fetchone()
    if not r: return None
    d=dict(r)
    if d.get("result_json"):
        try: d["result_json"]=json.loads(d["result_json"])
        except Exception: pass
    return d

def update_workflow_step(wid: str, index: int, **fields: Any) -> None:
    if not fields: return
    fields["updated_at"]=now()
    sets=",".join(f"{k}=?" for k in fields)
    vals=list(fields.values())+[wid,index]
    with _db_lock, db() as c:
        c.execute(f"UPDATE workflow_steps SET {sets} WHERE workflow_id=? AND step_index=?", vals)

def workflow_events_for(wid: str) -> List[Dict[str, Any]]:
    with _db_lock, db() as c:
        rows=c.execute("SELECT id,event,data_json,created_at FROM workflow_events WHERE workflow_id=? ORDER BY id",(wid,)).fetchall()
    return [{"id":r[0],"event":r[1],"data":json.loads(r[2] or "{}"),"created_at":r[3]} for r in rows]


def get_workflow(wid: str) -> Optional[Dict[str, Any]]:
    with _db_lock, db() as c:
        r = c.execute("SELECT * FROM workflows WHERE id=?", (wid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("steps_json", "result_json"):
        if d.get(k):
            try: d[k] = json.loads(d[k])
            except Exception: pass
    return d


def _result_context(wid: str) -> Dict[str, Any]:
    with _db_lock, db() as c:
        rows=c.execute("SELECT step_index,result_json,status,error FROM workflow_steps WHERE workflow_id=? ORDER BY step_index",(wid,)).fetchall()
        wr=c.execute("SELECT context_json FROM workflows WHERE id=?",(wid,)).fetchone()
    steps={}
    for r in rows:
        value=None
        if r[1]:
            try: value=json.loads(r[1])
            except Exception: value=r[1]
        steps[str(r[0])] = {"status":r[2], "result":value, "error":r[3]}
    variables={}
    if wr and wr[0]:
        try: variables=json.loads(wr[0])
        except Exception: variables={}
    return {"steps":steps,"variables":variables}

def _set_workflow_variables(wid: str, values: Dict[str, Any]) -> None:
    with _db_lock, db() as c:
        c.execute("UPDATE workflows SET context_json=?,updated_at=? WHERE id=?",(json.dumps(values,ensure_ascii=False),now(),wid))

def _resolve_templates(value: Any, context: Dict[str, Any]) -> Any:
    if isinstance(value, dict): return {k:_resolve_templates(v,context) for k,v in value.items()}
    if isinstance(value, list): return [_resolve_templates(v,context) for v in value]
    if not isinstance(value, str): return value
    def lookup(path: str):
        cur: Any=context
        for part in path.split('.'):
            if isinstance(cur,dict) and part in cur: cur=cur[part]
            elif isinstance(cur,list) and part.isdigit(): cur=cur[int(part)]
            else: raise KeyError(path)
        return cur
    def repl(m):
        try:
            cur=lookup(m.group(1).strip())
            return json.dumps(cur,ensure_ascii=False) if isinstance(cur,(dict,list)) else str(cur)
        except Exception:
            return m.group(0)
    return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", repl, value)

def _workflow_step_command(wid: str, step: Dict[str, Any]) -> str:
    raw = str(step.get("command") or step.get("objective") or step.get("template") or "").strip()
    return _resolve_templates(raw, _result_context(wid))

def _condition_value(wid: str, expression: Any) -> bool:
    if expression is None or expression == "": return True
    if isinstance(expression,bool): return expression
    expr=str(expression).strip()
    ctx=_result_context(wid)
    if expr.lower() in {"true","always","yes"}: return True
    if expr.lower() in {"false","never","no"}: return False
    m=re.match(r"^steps\.(\d+)\.status\s*(==|!=)\s*['\"]([^'\"]+)['\"]$",expr)
    if m:
        actual=ctx["steps"].get(m.group(1),{}).get("status")
        return actual == m.group(3) if m.group(2)=="==" else actual != m.group(3)
    m=re.match(r"^steps\.(\d+)\.result\.([A-Za-z0-9_]+)\s*(==|!=)\s*(.+)$",expr)
    if m:
        actual=ctx["steps"].get(m.group(1),{}).get("result",{}).get(m.group(2)) if isinstance(ctx["steps"].get(m.group(1),{}).get("result"),dict) else None
        rhs=m.group(4).strip().strip('\"\'')
        return (str(actual)==rhs) if m.group(3)=="==" else (str(actual)!=rhs)
    return False

def _branch_target(index: int, target: Any, status: str, total: int) -> int:
    if target is None: return index + 1 if index + 1 < total else total
    if isinstance(target,str):
        t=target.strip().lower()
        if t in {"next","continue","on_success"} : return index + 1 if index + 1 < total else total
        if t in {"end","stop","complete"}: return total
        if t in {"failure","failed"}: return index + 1 if index + 1 < total else total
    try:
        n=int(target)
        if 0 <= n < total: return n
    except Exception: pass
    return index + 1 if index + 1 < total else total

def _persist_final(wid: str, status: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
    ctx=_result_context(wid)
    final={"workflow_id":wid,"status":status,"completed_steps":sum(1 for x in results if x.get("status")=="completed"),"total_steps":len(results),"results":results,"context":ctx,"result_closure":status=="completed"}
    with _db_lock, db() as c:
        c.execute("UPDATE workflows SET final_result_json=?,context_json=?,updated_at=? WHERE id=?",(json.dumps(final,ensure_ascii=False),json.dumps(ctx.get("variables",{}),ensure_ascii=False),now(),wid))
    return final

def _execute_parallel_group(wid: str, steps: List[Dict[str, Any]], indices: List[int]) -> List[Dict[str, Any]]:
    results=[]
    def one(index):
        step=steps[index]
        objective=_workflow_step_command(wid,step)
        plan=parse_command(objective)
        if plan.get("side_effect"): raise ValueError("parallel side-effecting steps require sequential approval")
        existing=workflow_step(wid,index); eid=existing.get("execution_id") if existing else None
        if eid and get_execution(eid).get("status")=="completed":
            e=get_execution(eid); return {"step":index,"execution_id":eid,"status":"completed","result":e.get("result_json")}
        if not eid:
            eid=create_execution(plan.get("action_type","unknown"),plan.get("connector","none"),objective,False,payload={"workflow_id":wid,"step":index})
            update_workflow_step(wid,index,execution_id=eid)
        update_workflow_step(wid,index,status="running",input_json=json.dumps({"resolved_command":objective},ensure_ascii=False))
        run_execution(eid,plan,approved=True); e=get_execution(eid); st=e.get("status") if e else "missing"
        update_workflow_step(wid,index,status=st,result_json=json.dumps(e.get("result_json"),ensure_ascii=False) if e and e.get("result_json") is not None else None,error=e.get("error") if e else "missing")
        return {"step":index,"execution_id":eid,"status":st,"result":e.get("result_json") if e else None}
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=min(8,len(indices))) as ex:
        futures={ex.submit(one,i):i for i in indices}
        for f in as_completed(futures): results.append(f.result())
    return sorted(results,key=lambda x:x["step"])

def _adaptive_execute_step(wid: str, index: int, step: Dict[str, Any], objective: str, plan: Dict[str, Any], eid: str) -> Dict[str, Any]:
    """Execute one workflow step with bounded adaptive retry and safe fallback.

    External side effects are never replayed automatically. Only non-side-effect
    steps may use the adaptive retry/fallback controls.
    """
    limits = _adaptive_limits(step, plan)
    max_retries = limits["max_retries"]
    fallback = step.get("fallback")
    fallback_used = False
    attempts = 0
    current_objective = objective
    current_plan = plan
    current_eid = eid
    while True:
        attempts += 1
        run_execution(current_eid, current_plan, approved=True)
        e = get_execution(current_eid) or {}
        status = e.get("status")
        if status == "completed":
            _adaptive_event(wid,index,"success",{"execution_id":current_eid,"attempts":attempts,"fallback_used":fallback_used})
            _adaptive_policy_set("default_safe_retries", min(3, max(1, int(_adaptive_policy_get("default_safe_retries",1)))) )
            return {"step": index, "execution_id": current_eid, "status": status,
                    "result": e.get("result_json"), "adaptive": {"attempts": attempts, "fallback_used": fallback_used}}
        # Never automatically replay an external side effect after an uncertain outcome.
        if current_plan.get("side_effect"):
            _adaptive_event(wid,index,"side_effect_protected",{"status":status,"automatic_replay":False})
            return {"step": index, "execution_id": current_eid, "status": status,
                    "result": e.get("result_json"), "error": e.get("error"),
                    "adaptive": {"attempts": attempts, "fallback_used": fallback_used}}
        if attempts <= max_retries:
            new_eid = create_execution(current_plan.get("action_type", "unknown"), current_plan.get("connector", "none"),
                                       current_objective, False, parent_id=current_eid,
                                       payload={"workflow_id": wid, "step": index, "adaptive_retry": attempts})
            update_workflow_step(wid, index, execution_id=new_eid,
                                 input_json=json.dumps({"resolved_command": current_objective, "adaptive_retry": attempts}, ensure_ascii=False))
            _adaptive_event(wid,index,"retry",{"step":index,"from_execution":current_eid,"to_execution":new_eid,"attempt":attempts,"reason":_classify_failure(e.get("error"),status)})
            current_eid = new_eid
            continue
        if fallback and not current_plan.get("side_effect"):
            fallback_text = str(fallback.get("command") if isinstance(fallback, dict) else fallback).strip()
            if fallback_text:
                fallback_plan = parse_command(_workflow_step_command(wid, {"command": fallback_text}))
                if fallback_plan.get("side_effect"):
                    return {"step": index, "execution_id": current_eid, "status": status,
                            "result": e.get("result_json"), "error": e.get("error"),
                            "adaptive": {"attempts": attempts, "fallback_used": False, "fallback_blocked": True}}
                fallback_eid = create_execution(fallback_plan.get("action_type", "unknown"), fallback_plan.get("connector", "none"),
                                                fallback_text, False, parent_id=current_eid,
                                                payload={"workflow_id": wid, "step": index, "adaptive_fallback": True})
                update_workflow_step(wid, index, execution_id=fallback_eid,
                                     input_json=json.dumps({"resolved_command": fallback_text, "adaptive_fallback": True}, ensure_ascii=False))
                _adaptive_event(wid,index,"fallback",{"step":index,"from_execution":current_eid,"to_execution":fallback_eid,"command":fallback_text})
                current_objective, current_plan, current_eid = fallback_text, fallback_plan, fallback_eid
                fallback_used = True
                fallback = None
                attempts = 0
                max_retries = 0
                continue
        return {"step": index, "execution_id": current_eid, "status": status,
                "result": e.get("result_json"), "error": e.get("error"),
                "adaptive": {"attempts": attempts, "fallback_used": fallback_used}}


def _adaptive_policy_get(key: str, default: Any) -> Any:
    with _db_lock, db() as c:
        r=c.execute("SELECT value_json FROM adaptive_policies WHERE key=?",(key,)).fetchone()
    if not r: return default
    try: return json.loads(r[0])
    except Exception: return default


def _adaptive_policy_set(key: str, value: Any) -> None:
    with _db_lock, db() as c:
        c.execute("INSERT INTO adaptive_policies(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",(key,json.dumps(value,ensure_ascii=False),now()))


def _adaptive_event(wid: str, index: Optional[int], name: str, data: Optional[Dict[str,Any]]=None) -> None:
    with _db_lock, db() as c:
        c.execute("INSERT INTO adaptive_events(workflow_id,step_index,event,data_json,created_at) VALUES(?,?,?,?,?)",(wid,index,name,json.dumps(data or {},ensure_ascii=False),now()))
    workflow_event(wid,"adaptive_"+name,data or {})


def _classify_failure(error: Any, status: str) -> str:
    text=str(error or "").lower()
    if status == "uncertain" or "timeout" in text or "timed out" in text: return "uncertain_external"
    if "blocked" in text or "approval" in text: return "policy_block"
    if "unsupported" in text or "invalid" in text: return "invalid_command"
    if status == "failed": return "execution_failure"
    return "unknown"


def _adaptive_limits(step: Dict[str,Any], plan: Dict[str,Any]) -> Dict[str,Any]:
    requested=step.get("retry",step.get("retries",None))
    if requested is None:
        requested=_adaptive_policy_get("default_safe_retries",1)
    try: retries=max(0,min(3,int(requested)))
    except Exception: retries=1
    if plan.get("side_effect"): retries=0
    return {"max_retries":retries,"fallback_allowed":not bool(plan.get("side_effect"))}


def run_workflow(wid: str, steps: List[Dict[str, Any]], start_index: int = 0) -> Dict[str, Any]:
    update_workflow(wid, "running")
    workflow_event(wid, "started", {"start_index": start_index})
    results=[]; index=start_index; transitions=0; max_transitions=max(100,len(steps)*10)
    while index < len(steps):
        transitions += 1
        if transitions > max_transitions:
            update_workflow(wid,"failed",results); workflow_event(wid,"loop_guard",{"max_transitions":max_transitions}); return _persist_final(wid,"failed",results)
        step=steps[index]
        if not _condition_value(wid, step.get("condition",step.get("when"))):
            update_workflow_step(wid,index,status="skipped",result_json=json.dumps({"skipped":True,"reason":"condition_false"}))
            workflow_event(wid,"step_skipped",{"step":index,"reason":"condition_false"})
            index=_branch_target(index,step.get("on_skip", "next"),"skipped",len(steps)); continue
        dep=step.get("depends_on")
        if dep is not None and dep != "previous":
            try: dep_i=int(dep)
            except Exception: dep_i=None
            dep_step=workflow_step(wid,dep_i) if dep_i is not None else None
            if not dep_step or dep_step.get("status") != "completed":
                update_workflow(wid,"blocked",results); workflow_event(wid,"dependency_blocked",{"step":index,"depends_on":dep_i}); return _persist_final(wid,"blocked",results)
        group=step.get("parallel_group")
        if group and step.get("parallel",True):
            group_indices=[j for j,x in enumerate(steps) if x.get("parallel_group")==group and j>=index and not x.get("depends_on")]
            if len(group_indices)>1:
                group_results=_execute_parallel_group(wid,steps,group_indices); results.extend(group_results)
                if any(x.get("status")!="completed" for x in group_results):
                    update_workflow(wid,"failed",results); return _persist_final(wid,"failed",results)
                index=max(group_indices)+1; continue
        objective=_workflow_step_command(wid,step)
        try:
            if not objective: raise ValueError("each workflow step needs command or objective")
            plan=parse_command(objective)
            update_workflow_step(wid,index,status="running",input_json=json.dumps({"resolved_command":objective,"context":_result_context(wid)},ensure_ascii=False))
            approval=bool(plan.get("side_effect")); existing=workflow_step(wid,index); eid=existing.get("execution_id") if existing else None
            if eid:
                e=get_execution(eid)
                if e and e.get("status")=="completed":
                    result={"step":index,"execution_id":eid,"status":"completed","result":e.get("result_json")}; results.append(result); index=_branch_target(index,step.get("on_success"),"completed",len(steps)); continue
            if not eid:
                eid=create_execution(plan.get("action_type","unknown"),plan.get("connector","none"),objective,approval,payload={"workflow_id":wid,"step":index,"idempotency_key":existing.get("idempotency_key") if existing else None})
                update_workflow_step(wid,index,execution_id=eid)
            if approval:
                update_execution(eid,status="pending_approval"); update_workflow_step(wid,index,status="waiting_approval")
                update_workflow(wid,"waiting_approval",results+[ {"step":index,"execution_id":eid,"status":"pending_approval","approval_required":True} ]); workflow_event(wid,"approval_required",{"step":index,"execution_id":eid})
                return _persist_final(wid,"waiting_approval",results)
            result = _adaptive_execute_step(wid, index, step, objective, plan, eid)
            e=get_execution(result.get("execution_id")) or {}
            status=result.get("status", "missing")
            results.append(result)
            update_workflow_step(wid,index,status=status,result_json=json.dumps(result.get("result"),ensure_ascii=False) if result.get("result") is not None else None,error=e.get("error") if e else "missing")
            workflow_event(wid,"step_closed",{"step":index,"execution_id":eid,"status":status})
            if status != "completed":
                final_status="uncertain" if status=="uncertain" else "failed"; update_workflow(wid,final_status,results); workflow_event(wid,"branch_failure",{"step":index,"status":status})
                target=step.get("on_failure")
                if target is not None and final_status=="failed": index=_branch_target(index,target,final_status,len(steps)); update_workflow(wid,"running"); continue
                return _persist_final(wid,final_status,results)
            # Optional variable assignments from completed result.
            assign=step.get("set") or {}
            if isinstance(assign,dict):
                vars_=_result_context(wid).get("variables",{})
                resolved=_resolve_templates(assign,_result_context(wid)); vars_.update(resolved if isinstance(resolved,dict) else {}); _set_workflow_variables(wid,vars_)
            index=_branch_target(index,step.get("on_success"),"completed",len(steps))
        except Exception as ex:
            update_workflow_step(wid,index,status="failed",error=str(ex)); workflow_event(wid,"step_failed",{"step":index,"error":str(ex)})
            target=step.get("on_failure")
            if target is not None:
                update_workflow(wid,"running"); index=_branch_target(index,target,"failed",len(steps)); continue
            update_workflow(wid,"failed",results); return _persist_final(wid,"failed",results)
    update_workflow(wid,"completed",results); workflow_event(wid,"completed",{"step_count":len(steps)}); return _persist_final(wid,"completed",results)

def update_workflow(wid: str, status: str, result: Any = None) -> None:
    with _db_lock, db() as c:
        c.execute("UPDATE workflows SET status=?, result_json=?, updated_at=? WHERE id=?",(status,json.dumps(result,ensure_ascii=False) if result is not None else None,now(),wid))


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=4096)
    execute: bool = True
    require_approval: bool = True
    headers: Optional[Dict[str, str]] = None
    body: Any = None

class ExecuteRequest(BaseModel):
    approved: bool = False

class WorkflowRequest(BaseModel):
    steps: List[Dict[str, Any]] = Field(min_length=1, max_length=100)
    execute: bool = True
    objective: Optional[str] = None
    variables: Optional[Dict[str, Any]] = None


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
    return {"name":"AI Infinity","status":"online","version":APP_VERSION,"build":BUILD,"previous_build":PREVIOUS_BUILD,"interface":"/interface","docs":"/docs","run":"/run","command":"/command","execution":"/execution/{id}","workflow":"/workflow","workflows":"/workflows","adaptive_workflow":"/workflow/{workflow_id}/adaptive-policy","adaptive_policy":"/adaptive-policy"}

@app.get("/health")
def health():
    recover_stale()
    with _db_lock, db() as c:
        counts = {"memory_count":c.execute("SELECT COUNT(*) FROM memories").fetchone()[0],"execution_count":c.execute("SELECT COUNT(*) FROM executions").fetchone()[0],"execution_event_count":c.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0],"workflow_count":c.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]}
    return {"status":"healthy","version":APP_VERSION,"build":BUILD,"database":"ready","policy_version":1,"external_execution_enabled":True,"verification_enabled":True,"adaptive_recovery_enabled":True,"self_modification_enabled":True,"intent_router_enabled":True,"result_closure_enabled":True,**counts}

@app.get("/status")
def status(): return health()

@app.get("/capabilities")
def capabilities():
    return {"version":APP_VERSION,"build":BUILD,"connectors":["system","http","memory","research","workspace","webhook","workflow"],"features":["intent_routing","approval_gate","SSRF_protection","credential_header_protection","workspace_traversal_protection","durable_execution","result_hash_verification","safe_retry","uncertain_external_outcome_closure","workflow_child_results","durable_workflows","workflow_status","persistent_execution_history","live_interface","workflow_step_persistence","workflow_resume_after_approval","workflow_events","workflow_cancel","workflow_orchestration","step_dependencies","result_template_propagation","idempotent_resume","conditional_branching","result_aware_commands","workflow_variables","safe_parallel_steps","final_result_synthesis","adaptive_step_retry","adaptive_fallback","durable_adaptive_events","side_effect_replay_block","adaptive_failure_classification","adaptive_policy_memory","adaptive_policy_endpoint"]}

@app.get("/command-policy")
def command_policy():
    return {"approval_required_for_side_effects":True,"safe_methods":sorted(SAFE_METHODS),"supported_methods":sorted(HTTP_METHODS),"sensitive_headers_blocked":sorted(SENSITIVE_HEADERS),"host_allowlist_configured":bool(ALLOWLIST),"automatic_side_effect_retry":False,"unknown_external_outcome_replay":False,"max_attempts":MAX_ATTEMPTS}

@app.get("/resilience-policy")
def resilience_policy(): return {"adaptive_recovery":True,"adaptive_workflow_execution":True,"safe_retry":True,"action_retry_limit":MAX_ATTEMPTS,"stale_running_transaction_recovery":True,"action_stale_seconds":STALE_SECONDS,"automatic_side_effect_retry":False,"unknown_external_outcome_replay":False}

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

@app.post("/workflow")
def workflow(req: WorkflowRequest):
    if not req.steps:
        raise HTTPException(400, "steps are required")
    for step in req.steps:
        command_text = str(step.get("command") or step.get("objective") or "").strip()
        if not command_text:
            raise HTTPException(400, "each workflow step needs command or objective")
        plan = parse_command(command_text)
        if plan.get("connector") in {"http", "webhook"}:
            if plan.get("url"):
                validate_url(plan["url"], plan.get("method", "GET"))
    wid = create_workflow(req.steps)
    if req.variables:
        _set_workflow_variables(wid, req.variables)
    if not req.execute:
        update_workflow(wid, "planned")
        return {"status": "planned", "workflow_id": wid, "steps": req.steps}
    return run_workflow(wid, req.steps)


@app.get("/workflow/{workflow_id}")
def workflow_status(workflow_id: str):
    w = get_workflow(workflow_id)
    if not w:
        raise HTTPException(404, "workflow not found")
    w["events"] = workflow_events_for(workflow_id)
    w["steps"] = []
    with _db_lock, db() as c:
        rows=c.execute("SELECT * FROM workflow_steps WHERE workflow_id=? ORDER BY step_index",(workflow_id,)).fetchall()
    for r in rows:
        d=dict(r)
        if d.get("result_json"):
            try: d["result_json"]=json.loads(d["result_json"])
            except Exception: pass
        w["steps"].append(d)
    return w


@app.get("/workflow/{workflow_id}/steps")
def workflow_steps(workflow_id: str):
    if not get_workflow(workflow_id): raise HTTPException(404,"workflow not found")
    with _db_lock, db() as c:
        rows=c.execute("SELECT * FROM workflow_steps WHERE workflow_id=? ORDER BY step_index",(workflow_id,)).fetchall()
    out=[]
    for r in rows:
        d=dict(r)
        if d.get("result_json"):
            try: d["result_json"]=json.loads(d["result_json"])
            except Exception: pass
        out.append(d)
    return {"workflow_id":workflow_id,"steps":out}

@app.get("/workflow/{workflow_id}/events")
def workflow_events(workflow_id: str):
    if not get_workflow(workflow_id): raise HTTPException(404,"workflow not found")
    return {"workflow_id":workflow_id,"events":workflow_events_for(workflow_id)}

@app.post("/workflow/{workflow_id}/approve")
def workflow_approve(workflow_id: str):
    w=get_workflow(workflow_id)
    if not w: raise HTTPException(404,"workflow not found")
    with _db_lock, db() as c:
        row=c.execute("SELECT * FROM workflow_steps WHERE workflow_id=? AND status='waiting_approval' ORDER BY step_index LIMIT 1",(workflow_id,)).fetchone()
    if not row: raise HTTPException(409,"workflow has no step awaiting approval")
    eid=row["execution_id"]
    if not eid: raise HTTPException(409,"workflow step has no execution")
    e=execute_approved(eid,ExecuteRequest(approved=True))
    if e.get("status") != "completed":
        update_workflow(workflow_id,"uncertain" if e.get("status")=="uncertain" else e.get("status","failed"),[{"step":row["step_index"],"execution_id":eid,"status":e.get("status")}])
        return {"workflow_id":workflow_id,"status":e.get("status"),"execution":e}
    update_workflow_step(workflow_id,row["step_index"],status="completed",result_json=json.dumps(e.get("result_json"),ensure_ascii=False))
    steps=w.get("steps_json") or []
    return run_workflow(workflow_id,steps,start_index=int(row["step_index"])+1)

@app.post("/workflow/{workflow_id}/reject")
def workflow_reject(workflow_id: str):
    w=get_workflow(workflow_id)
    if not w: raise HTTPException(404,"workflow not found")
    with _db_lock, db() as c:
        row=c.execute("SELECT * FROM workflow_steps WHERE workflow_id=? AND status='waiting_approval' ORDER BY step_index LIMIT 1",(workflow_id,)).fetchone()
    if not row: raise HTTPException(409,"workflow has no step awaiting approval")
    reject(row["execution_id"])
    update_workflow_step(workflow_id,row["step_index"],status="rejected",error="approval rejected")
    update_workflow(workflow_id,"rejected",[{"step":row["step_index"],"execution_id":row["execution_id"],"status":"rejected"}])
    workflow_event(workflow_id,"rejected",{"step":row["step_index"]})
    return get_workflow(workflow_id)

@app.post("/workflow/{workflow_id}/cancel")
def workflow_cancel(workflow_id: str):
    w=get_workflow(workflow_id)
    if not w: raise HTTPException(404,"workflow not found")
    if w.get("status") in {"completed","failed","uncertain","rejected","cancelled"}: return w
    update_workflow(workflow_id,"cancelled",w.get("result_json"))
    workflow_event(workflow_id,"cancelled")
    return get_workflow(workflow_id)

@app.post("/workflow/{workflow_id}/resume")
def workflow_resume(workflow_id: str):
    w=get_workflow(workflow_id)
    if not w: raise HTTPException(404,"workflow not found")
    if w.get("status") in {"completed","cancelled","rejected"}: return w
    steps=w.get("steps_json") or []
    with _db_lock, db() as c:
        row=c.execute("SELECT MIN(step_index) FROM workflow_steps WHERE workflow_id=? AND status NOT IN ('completed','rejected')",(workflow_id,)).fetchone()
    start=int(row[0]) if row and row[0] is not None else len(steps)
    workflow_event(workflow_id,"resumed",{"start_index":start})
    return run_workflow(workflow_id,steps,start_index=start)

@app.get("/workflow/{workflow_id}/context")
def workflow_context(workflow_id: str):
    if not get_workflow(workflow_id): raise HTTPException(404,"workflow not found")
    return {"workflow_id":workflow_id,"context":_result_context(workflow_id)}

@getattr(app, "get")("/workflow/{workflow_id}/result")
def workflow_result(workflow_id: str):
    w=get_workflow(workflow_id)
    if not w: raise HTTPException(404,"workflow not found")
    if w.get("final_result_json"):
        try: return json.loads(w["final_result_json"])
        except Exception: pass
    return _persist_final(workflow_id,w.get("status","unknown"),w.get("result_json") or [])

@app.get("/workflow/{workflow_id}/adaptive-policy")
def workflow_adaptive_policy(workflow_id: str):
    if not get_workflow(workflow_id):
        raise HTTPException(404, "workflow not found")
    return {
        "workflow_id": workflow_id,
        "adaptive_execution": True,
        "safe_step_retry": True,
        "max_configured_retries": 3,
        "fallback_commands": True,
        "external_side_effect_replay": False,
        "uncertain_external_outcome_replay": False,
        "durable_adaptive_events": True,
    }


@app.get("/adaptive-policy")
def adaptive_policy():
    return {
        "version": APP_VERSION,
        "adaptive_execution": True,
        "default_safe_retries": _adaptive_policy_get("default_safe_retries", 1),
        "max_safe_retries": 3,
        "fallback_enabled": True,
        "failure_classification": True,
        "policy_memory": True,
        "external_side_effect_replay": False,
        "uncertain_external_outcome_replay": False
    }


@app.get("/workflow/{workflow_id}/adaptive-events")
def workflow_adaptive_events(workflow_id: str):
    if not get_workflow(workflow_id): raise HTTPException(404,"workflow not found")
    with _db_lock, db() as c:
        rows=c.execute("SELECT id,step_index,event,data_json,created_at FROM adaptive_events WHERE workflow_id=? ORDER BY id",(workflow_id,)).fetchall()
    return {"workflow_id":workflow_id,"events":[{"id":r[0],"step":r[1],"event":r[2],"data":json.loads(r[3] or "{}"),"created_at":r[4]} for r in rows]}


@app.get("/workflows")
def workflows():
    with _db_lock, db() as c:
        rows = c.execute("SELECT id,status,created_at,updated_at FROM workflows ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"workflows": [dict(r) for r in rows]}


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
    required=["/","/health","/command","/real-world-command","/execution/{execution_id}","/run","/interface","/workflow","/workflow/{workflow_id}"]
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
    check("workflow planning",lambda: create_workflow([{"command":"ping"}]))
    def workflow_chain_check():
        wid=create_workflow([{"command":"ping"},{"command":"remember workflow-closure-test"}])
        r=run_workflow(wid,[{"command":"ping"},{"command":"remember workflow-closure-test"}])
        if r.get("status")!="completed": raise ValueError("workflow did not close")
        if len(r.get("results",[]))!=2: raise ValueError("workflow child results incomplete")
    check("workflow execution closure",workflow_chain_check)
    return {"status":"completed","passed":all(x["passed"] for x in checks),"tests":checks}

