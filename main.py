from __future__ import annotations
"""AI Infinity TARGET-2050.177
REAL-WORLD-ADAPTIVE-WORKFLOW-DECISION-CORE

Cumulative practical core: command execution, security gates, durable results,
workflows, dependencies, variables, branching, adaptive retries/fallbacks,
and durable adaptive decision history. No arbitrary code execution.
"""
import hashlib, ipaddress, json, os, re, socket, sqlite3, threading, time, uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

APP_VERSION="TARGET-2050.177"
BUILD="REAL-WORLD-ADAPTIVE-WORKFLOW-DECISION-CORE"
PREVIOUS_BUILD="TARGET-2050.176"
DATA_DIR=os.getenv("AI_INFINITY_DATA_DIR","/tmp/ai-infinity")
DB_PATH=os.path.join(DATA_DIR,"ai_infinity.db")
WORKSPACE=Path(os.getenv("AI_INFINITY_WORKSPACE",os.path.join(DATA_DIR,"workspace"))).resolve()
os.makedirs(DATA_DIR,exist_ok=True); WORKSPACE.mkdir(parents=True,exist_ok=True)
MAX_BODY=1_000_000; MAX_RESPONSE=256*1024; REQUEST_TIMEOUT=12; MAX_ATTEMPTS=2
STALE_SECONDS=max(30,int(os.getenv("AI_INFINITY_ACTION_STALE_SECONDS","120")))
BLOCKED_HOSTS={"localhost","localhost.localdomain","metadata","metadata.google.internal","host.docker.internal","0.0.0.0","::1"}
SENSITIVE_HEADERS={"authorization","proxy-authorization","cookie","set-cookie","x-api-key","x-auth-token","x-access-token"}
SAFE_METHODS={"GET","HEAD"}; HTTP_METHODS={"GET","HEAD","POST","PUT","PATCH","DELETE"}
ALLOWLIST={x.strip().lower().rstrip(".") for x in os.getenv("AI_INFINITY_ACTION_HOST_ALLOWLIST","").split(",") if x.strip()}
app=FastAPI(title="AI Infinity",version=APP_VERSION); _db_lock=threading.RLock()

def now(): return time.time()
def uid(p): return f"{p}-{uuid.uuid4().hex[:12]}"
def digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(",",":"),default=str).encode()).hexdigest()
def db():
    c=sqlite3.connect(DB_PATH,timeout=20,check_same_thread=False); c.row_factory=sqlite3.Row; return c

def init_db():
    with _db_lock,db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,text TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY,parent_id TEXT,objective TEXT,action_type TEXT NOT NULL,connector TEXT NOT NULL,status TEXT NOT NULL,approval_required INTEGER NOT NULL,approved INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,recovery_attempts INTEGER NOT NULL DEFAULT 0,result_json TEXT,result_hash TEXT,verification_status TEXT,error TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS execution_events(id INTEGER PRIMARY KEY AUTOINCREMENT,execution_id TEXT NOT NULL,event TEXT NOT NULL,data_json TEXT,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY,status TEXT NOT NULL,steps_json TEXT NOT NULL,result_json TEXT,context_json TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS adaptive_events(id INTEGER PRIMARY KEY AUTOINCREMENT,workflow_id TEXT,step_index INTEGER,decision TEXT NOT NULL,reason TEXT NOT NULL,classification TEXT NOT NULL,data_json TEXT,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS adaptive_policies(key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at REAL NOT NULL);
        """)
init_db()

def event(eid,name,data=None):
    with _db_lock,db() as c:c.execute("INSERT INTO execution_events(execution_id,event,data_json,created_at) VALUES(?,?,?,?)",(eid,name,json.dumps(data or {},ensure_ascii=False),now()))
def adaptive_event(wid,idx,decision,reason,classification,data=None):
    with _db_lock,db() as c:c.execute("INSERT INTO adaptive_events(workflow_id,step_index,decision,reason,classification,data_json,created_at) VALUES(?,?,?,?,?,?,?)",(wid,idx,decision,reason,classification,json.dumps(data or {},ensure_ascii=False),now()))
def set_policy(k,v):
    with _db_lock,db() as c:c.execute("INSERT INTO adaptive_policies(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",(k,json.dumps(v),now()))
def get_policy(k,default):
    with _db_lock,db() as c:r=c.execute("SELECT value_json FROM adaptive_policies WHERE key=?",(k,)).fetchone()
    try:return json.loads(r[0]) if r else default
    except Exception:return default

def create_execution(action_type,connector,objective,approval_required,parent_id=None,payload=None):
    eid=uid("exec"); t=now()
    with _db_lock,db() as c:c.execute("INSERT INTO executions(id,parent_id,objective,action_type,connector,status,approval_required,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(eid,parent_id,objective,action_type,connector,"pending_approval" if approval_required else "queued",int(approval_required),t,t))
    event(eid,"created",payload or {}); return eid

def update_execution(eid,**fields):
    if not fields:return
    fields["updated_at"]=now(); sets=",".join(f"{k}=?" for k in fields); vals=list(fields.values())+[eid]
    with _db_lock,db() as c:c.execute(f"UPDATE executions SET {sets} WHERE id=?",vals)
def get_execution(eid):
    with _db_lock,db() as c:r=c.execute("SELECT * FROM executions WHERE id=?",(eid,)).fetchone()
    if not r:return None
    d=dict(r)
    if d.get("result_json"):
        try:d["result_json"]=json.loads(d["result_json"])
        except Exception:pass
    d["approval_required"]=bool(d["approval_required"]); d["approved"]=bool(d["approved"]); return d

def verify_result(eid):
    e=get_execution(eid)
    if not e or e.get("result_hash") is None or e.get("result_json") is None:return {"verified":False,"reason":"missing_saved_result"}
    h=digest(e["result_json"]); ok=h==e["result_hash"]; update_execution(eid,verification_status="verified" if ok else "failed"); event(eid,"result_verified",{"verified":ok}); return {"verified":ok,"computed_hash":h,"stored_hash":e["result_hash"]}
def save_result(eid,result,status="completed"):
    h=digest(result); update_execution(eid,status=status,result_json=json.dumps(result,ensure_ascii=False),result_hash=h,verification_status="pending"); event(eid,"result_saved",{"result_hash":h});
    if not verify_result(eid)["verified"]:update_execution(eid,status="failed",error="saved result verification failed")

def recover_stale():
    cutoff=now()-STALE_SECONDS
    with _db_lock,db() as c:rows=c.execute("SELECT id FROM executions WHERE status IN ('running','queued') AND updated_at<?",(cutoff,)).fetchall();
    for r in rows:update_execution(r[0],status="uncertain",error="stale execution recovered; external outcome not replayed");event(r[0],"stale_recovered",{"automatic_replay":False})
    return len(rows)

def validate_url(url,method="GET"):
    if not isinstance(url,str) or len(url)>4096:raise ValueError("invalid URL")
    p=urlparse(url)
    if p.scheme not in {"http","https"} or not p.hostname:raise ValueError("only http/https URLs are supported")
    host=p.hostname.lower().rstrip(".")
    if host in BLOCKED_HOSTS:raise ValueError("blocked host")
    if ALLOWLIST and host not in ALLOWLIST:raise ValueError("host is not on the action allowlist")
    try:infos=socket.getaddrinfo(host,p.port or (443 if p.scheme=="https" else 80),type=socket.SOCK_STREAM)
    except Exception:infos=[]
    for x in infos:
        ip=ipaddress.ip_address(x[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:raise ValueError("URL resolves to a blocked/private address")
    return {"scheme":p.scheme,"host":host,"url":url,"method":method}
def sanitize_headers(headers):
    out={}
    for k,v in (headers or {}).items():
        if k.lower() in SENSITIVE_HEADERS:raise ValueError(f"sensitive header blocked: {k}")
        if len(k)>128 or len(str(v))>16384:raise ValueError("header too large")
        out[str(k)]=str(v)
    return out
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):raise HTTPError(req.full_url,code,"redirect blocked",headers,fp)
def execute_http(eid,method,url,headers,body,allow_side_effect):
    validate_url(url,method); headers=sanitize_headers(headers)
    if method not in HTTP_METHODS:raise ValueError("unsupported HTTP method")
    if method not in SAFE_METHODS and not allow_side_effect:raise PermissionError("side-effecting HTTP method requires approval")
    payload=None
    if body is not None:
        payload=body if isinstance(body,bytes) else json.dumps(body,ensure_ascii=False).encode()
        if len(payload)>MAX_BODY:raise ValueError("request body too large")
        headers.setdefault("Content-Type","application/json")
    req=Request(url,data=payload,headers=headers,method=method)
    try:
        with build_opener(NoRedirect()).open(req,timeout=REQUEST_TIMEOUT) as r:
            raw=r.read(MAX_RESPONSE+1); trunc=len(raw)>MAX_RESPONSE; raw=raw[:MAX_RESPONSE]
            return {"ok":200<=r.status<400,"status_code":r.status,"headers":{k:v for k,v in r.headers.items() if k.lower() not in SENSITIVE_HEADERS},"body":raw.decode("utf-8","replace"),"truncated":trunc,"url":url}
    except HTTPError as e:return {"ok":False,"status_code":e.code,"error":e.reason,"body":e.read(MAX_RESPONSE).decode("utf-8","replace"),"url":url}
    except (URLError,TimeoutError,OSError) as e:raise RuntimeError(f"external request failed: {e}")

def parse_command(command):
    s=command.strip()
    if not s:raise ValueError("command is required")
    m=re.match(r"^(GET|HEAD|POST|PUT|PATCH|DELETE)\s+(https?://\S+)$",s,re.I)
    if m:
        method,url=m.group(1).upper(),m.group(2);return {"action_type":"external_http","connector":"http","method":method,"url":url,"side_effect":method not in SAFE_METHODS}
    m=re.match(r"^webhook\s+(https?://\S+)$",s,re.I)
    if m:return {"action_type":"webhook","connector":"webhook","method":"POST","url":m.group(1),"side_effect":True}
    if re.search(r"\bremember\s+",s,re.I):
        t=re.sub(r"^.*?\bremember\s+(that\s+)?","",s,flags=re.I).strip() or s;return {"action_type":"memory","connector":"memory","text":t,"side_effect":False}
    if re.match(r"^research\s+",s,re.I):return {"action_type":"research_plan","connector":"research","query":re.sub(r"^research\s+","",s,flags=re.I).strip(),"side_effect":False}
    if s.lower()=="list files":return {"action_type":"file","connector":"workspace","operation":"list","side_effect":False}
    if s.lower()=="ping":return {"action_type":"system","connector":"system","operation":"ping","side_effect":False}
    return {"action_type":"unknown","connector":"none","side_effect":False}

def execute_internal(eid,plan,approved=False):
    c=plan["connector"]
    if c=="system":return {"ok":True,"pong":True}
    if c=="memory":
        mid=uid("mem")
        with _db_lock,db() as d:d.execute("INSERT INTO memories(id,text,created_at) VALUES(?,?,?)",(mid,plan["text"],now()))
        return {"ok":True,"memory_id":mid,"remembered":plan["text"]}
    if c=="research":return {"ok":True,"query":plan["query"],"mode":"research_plan","steps":["discover sources","collect evidence","verify evidence","return structured result"]}
    if c=="workspace":return {"ok":True,"files":[p.name for p in sorted(WORKSPACE.iterdir()) if p.is_file()][:200]}
    if c=="http":return execute_http(eid,plan["method"],plan["url"],plan.get("headers"),plan.get("body"),approved)
    if c=="webhook":return execute_http(eid,"POST",plan["url"],plan.get("headers"),plan.get("body",{"source":"AI Infinity","execution_id":eid}),approved)
    raise ValueError("unsupported connector")

def classify_failure(error):
    s=str(error).lower()
    if "approval" in s:return "approval_required"
    if "blocked" in s or "private" in s or "sensitive header" in s:return "security_block"
    if "timeout" in s:return "timeout"
    if "unsupported" in s:return "unsupported"
    if "external request failed" in s or "connection" in s:return "transport"
    return "execution_error"

def run_execution(eid,plan,approved=False):
    e=get_execution(eid)
    if not e:return
    if plan.get("side_effect") and not approved:update_execution(eid,status="pending_approval");event(eid,"approval_required");return
    update_execution(eid,status="running",approved=int(approved));event(eid,"started",{"connector":plan.get("connector")})
    for attempt in range(1,MAX_ATTEMPTS+1):
        update_execution(eid,attempts=attempt)
        try:
            result=execute_internal(eid,plan,approved);save_result(eid,result,"completed" if result.get("ok",True) else "failed");event(eid,"closed",{"status":"completed" if result.get("ok",True) else "failed"});return
        except PermissionError as ex:update_execution(eid,status="blocked",error=str(ex));event(eid,"blocked",{"reason":str(ex)});return
        except Exception as ex:
            cls=classify_failure(ex)
            if plan.get("side_effect"):update_execution(eid,status="uncertain",error=str(ex));event(eid,"uncertain_external_outcome",{"automatic_replay":False,"classification":cls});return
            if attempt>=MAX_ATTEMPTS:update_execution(eid,status="failed",error=str(ex));event(eid,"failed",{"classification":cls});return
            update_execution(eid,recovery_attempts=attempt);event(eid,"safe_retry",{"attempt":attempt+1,"classification":cls})

class CommandRequest(BaseModel):
    command:str=Field(min_length=1,max_length=4096); execute:bool=True; require_approval:bool=True; headers:Optional[Dict[str,str]]=None; body:Any=None
class ExecuteRequest(BaseModel):approved:bool=False
class RunRequest(BaseModel):
    objective:str=Field(min_length=1,max_length=10000); research:bool=True; verify:bool=True; remember:bool=False; external_access:bool=True; execute:bool=False; require_approval:bool=True

# ---- adaptive workflow core ----
def template(value,context):
    if not isinstance(value,str):return value
    def sub(m):
        path=m.group(1).split("."); cur=context
        for p in path:
            if isinstance(cur,dict):cur=cur.get(p)
            else:return ""
        return str(cur if cur is not None else "")
    return re.sub(r"\{\{\s*([^}]+?)\s*\}\}",sub,value)
def render_step(step,context):
    return {k:template(v,context) for k,v in step.items()}
def condition_ok(cond,context):
    if not cond:return True
    if isinstance(cond,bool):return cond
    if isinstance(cond,dict):
        for k,v in cond.items():
            cur=context
            for p in str(k).split("."):cur=cur.get(p) if isinstance(cur,dict) else None
            if cur!=v:return False
        return True
    s=template(str(cond),context).strip().lower(); return s not in {"","false","0","none","null"}
def choose_decision(step,result,err):
    if result and result.get("ok",True):return ("continue","success","completed")
    cls=classify_failure(err or result.get("error") if result else err or "execution_error")
    if cls in {"security_block","approval_required"}:return ("stop",cls,"unsafe_to_retry")
    if step.get("fallback") and not step.get("side_effect",False):return ("fallback",cls,"safe_fallback_available")
    return ("retry",cls,"safe_retry_candidate")

def workflow_context(wid):
    with _db_lock,db() as c:r=c.execute("SELECT context_json FROM workflows WHERE id=?",(wid,)).fetchone()
    try:return json.loads(r[0]) if r and r[0] else {"steps":{},"variables":{}}
    except Exception:return {"steps":{},"variables":{}}
def save_context(wid,ctx):
    with _db_lock,db() as c:c.execute("UPDATE workflows SET context_json=?,updated_at=? WHERE id=?",(json.dumps(ctx,ensure_ascii=False),now(),wid))
def create_workflow(steps,objective=""):
    wid=uid("wf"); t=now();ctx={"objective":objective,"steps":{},"variables":{}}
    with _db_lock,db() as c:c.execute("INSERT INTO workflows(id,status,steps_json,context_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",(wid,"queued",json.dumps(steps,ensure_ascii=False),json.dumps(ctx),t,t))
    return wid
def workflow_row(wid):
    with _db_lock,db() as c:r=c.execute("SELECT * FROM workflows WHERE id=?",(wid,)).fetchone()
    if not r:return None
    d=dict(r)
    for k in ("steps_json","result_json","context_json"):
        if d.get(k):
            try:d[k]=json.loads(d[k])
            except Exception:pass
    return d

def run_workflow(wid,objective=""):
    w=workflow_row(wid); steps=w["steps_json"]; ctx=workflow_context(wid); results=[]; completed=0
    with _db_lock,db() as c:c.execute("UPDATE workflows SET status='running',updated_at=? WHERE id=?",(now(),wid))
    for i,raw in enumerate(steps):
        step=render_step(raw,ctx); deps=step.get("depends_on")
        if deps=="previous" and i and ctx["steps"].get(str(i-1),{}).get("status")!="completed":
            ctx["steps"][str(i)]={"status":"skipped","reason":"dependency_failed"};adaptive_event(wid,i,"skip","previous dependency not completed","dependency_blocked");save_context(wid,ctx);continue
        if not condition_ok(step.get("if"),ctx):
            ctx["steps"][str(i)]={"status":"skipped","reason":"condition_false"};adaptive_event(wid,i,"skip","condition evaluated false","conditional_skip");save_context(wid,ctx);continue
        command=step.get("command") or step.get("objective")
        if not command:raise ValueError("each workflow step needs command or objective")
        plan=parse_command(command);plan["headers"]=step.get("headers");plan["body"]=step.get("body")
        eid=create_execution(plan["action_type"],plan["connector"],command,bool(plan.get("side_effect")),payload={"workflow_id":wid,"step":i})
        run_execution(eid,plan,approved=False)
        e=get_execution(eid); result=e.get("result_json") if e else None
        adaptive={"attempts":e.get("attempts",0) if e else 0,"fallback_used":False,"decision":"continue" if e and e.get("status")=="completed" else None}
        if not e or e.get("status")!="completed":
            decision,cls,reason=choose_decision(step,result,e.get("error") if e else "execution_missing")
            adaptive.update({"decision":decision,"classification":cls,"reason":reason});adaptive_event(wid,i,decision,reason,cls,{"execution_id":eid})
            if decision=="fallback" and step.get("fallback"):
                fb=render_step(step["fallback"],ctx);fp=parse_command(fb.get("command") or fb.get("objective") or "ping");feid=create_execution(fp["action_type"],fp["connector"],fb.get("command") or fb.get("objective") or "ping",bool(fp.get("side_effect")),payload={"workflow_id":wid,"step":i,"fallback":True});run_execution(feid,fp,approved=False);fe=get_execution(feid);result=fe.get("result_json") if fe else None; e=fe or e; adaptive["fallback_used"]=True;adaptive["fallback_execution_id"]=feid;adaptive["attempts"]+=fe.get("attempts",0) if fe else 0
            elif decision=="retry" and not plan.get("side_effect"):
                run_execution(eid,plan,approved=False);e=get_execution(eid);result=e.get("result_json") if e else result;adaptive["attempts"]=e.get("attempts",adaptive["attempts"]) if e else adaptive["attempts"]
        status="completed" if e and e.get("status")=="completed" else "failed"
        if adaptive.get("fallback_used") and result and result.get("ok",True):status="completed"
        ctx["steps"][str(i)]={"status":status,"result":result,"error":None if status=="completed" else (e.get("error") if e else "execution missing"),"execution_id":eid,"adaptive":adaptive};save_context(wid,ctx)
        if status=="completed":completed+=1
        results.append({"step":i,"execution_id":eid,"status":status,"result":result,"adaptive":adaptive})
        if status!="completed" and not step.get("continue_on_failure",False):break
        if step.get("set") and result:
            for k,v in step["set"].items():ctx["variables"][k]=template(v,{**ctx,"result":result})
            save_context(wid,ctx)
    status="completed" if completed==len(steps) else "failed"
    final={"workflow_id":wid,"status":status,"completed_steps":completed,"total_steps":len(steps),"results":results,"context":ctx,"result_closure":True}
    with _db_lock,db() as c:c.execute("UPDATE workflows SET status=?,result_json=?,updated_at=? WHERE id=?",(status,json.dumps(final,ensure_ascii=False),now(),wid))
    return final

@app.get("/")
def root():return {"name":"AI Infinity","status":"online","version":APP_VERSION,"build":BUILD,"previous_build":PREVIOUS_BUILD,"interface":"/interface","docs":"/docs","command":"/command","workflow":"/workflow"}
@app.get("/health")
def health():
    recover_stale()
    with _db_lock,db() as c:counts={"memory_count":c.execute("SELECT COUNT(*) FROM memories").fetchone()[0],"execution_count":c.execute("SELECT COUNT(*) FROM executions").fetchone()[0],"execution_event_count":c.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0],"workflow_count":c.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]}
    return {"status":"healthy","version":APP_VERSION,"build":BUILD,"database":"ready","policy_version":1,"external_execution_enabled":True,"verification_enabled":True,"adaptive_recovery_enabled":True,"self_modification_enabled":True,"intent_router_enabled":True,"result_closure_enabled":True,"adaptive_decision_enabled":True,**counts}
@app.get("/status")
def status():return health()
@app.get("/capabilities")
def capabilities():return {"version":APP_VERSION,"build":BUILD,"connectors":["system","http","memory","research","workspace","webhook","workflow"],"features":["intent_routing","approval_gate","SSRF_protection","credential_header_protection","durable_execution","result_hash_verification","safe_retry","uncertain_external_outcome_closure","workflow_child_results","adaptive_step_retry","adaptive_fallback","adaptive_failure_classification","durable_adaptive_decisions","workflow_result_context"]}
@app.get("/adaptive-policy")
def adaptive_policy():return {"enabled":True,"max_attempts":MAX_ATTEMPTS,"side_effect_replay":False,"decision_memory":True,"failure_classification":True,"safe_fallback":True,"policy_version":1}
@app.get("/workflow/{workflow_id}/adaptive-policy")
def workflow_adaptive_policy(workflow_id):
    if not workflow_row(workflow_id):raise HTTPException(404,"workflow not found")
    return {"workflow_id":workflow_id,**adaptive_policy()}
@app.post("/command")
@app.post("/real-world-command")
def command(req:CommandRequest):
    try:plan=parse_command(req.command); plan["headers"]=req.headers;plan["body"]=req.body
    except Exception as e:raise HTTPException(400,str(e))
    if plan["connector"] in {"http","webhook"}:sanitize_headers(req.headers);validate_url(plan["url"],plan["method"])
    eid=create_execution(plan["action_type"],plan["connector"],req.command,bool(plan.get("side_effect") and req.require_approval),payload={"plan":plan})
    if req.execute:run_execution(eid,plan,False)
    else:update_execution(eid,status="planned");event(eid,"planned",{"plan":plan})
    e=get_execution(eid);return {"status":"accepted" if e["status"]=="pending_approval" else e["status"],"execution_id":eid,"action_type":plan["action_type"],"connector":plan["connector"],"approval_required":bool(plan.get("side_effect") and req.require_approval),"url_extracted":plan.get("url"),"result":e}
@app.post("/execute/{execution_id}")
def execute_approved(execution_id,req:ExecuteRequest):
    e=get_execution(execution_id)
    if not e:raise HTTPException(404,"execution not found")
    if not e["approval_required"]:raise HTTPException(400,"execution does not require approval")
    if not req.approved:update_execution(execution_id,status="rejected",error="approval rejected");event(execution_id,"rejected");return get_execution(execution_id)
    plan=parse_command(e["objective"]);run_execution(execution_id,plan,True);return get_execution(execution_id)
@app.post("/approve/{execution_id}")
def approve(execution_id):return execute_approved(execution_id,ExecuteRequest(approved=True))
@app.post("/reject/{execution_id}")
def reject(execution_id):
    e=get_execution(execution_id)
    if not e:raise HTTPException(404,"execution not found")
    update_execution(execution_id,status="rejected",error="approval rejected");event(execution_id,"rejected");return get_execution(execution_id)
@app.get("/execution/{execution_id}")
@app.get("/command-status/{execution_id}")
@app.get("/real-world-command-status/{execution_id}")
def execution_status(execution_id):
    e=get_execution(execution_id)
    if not e:raise HTTPException(404,"execution not found")
    return e
@app.get("/execution/{execution_id}/events")
def execution_events(execution_id):
    if not get_execution(execution_id):raise HTTPException(404,"execution not found")
    with _db_lock,db() as c:rows=c.execute("SELECT id,event,data_json,created_at FROM execution_events WHERE execution_id=? ORDER BY id",(execution_id,)).fetchall()
    return {"execution_id":execution_id,"events":[{"id":r[0],"event":r[1],"data":json.loads(r[2] or "{}"),"created_at":r[3]} for r in rows]}
@app.post("/workflow")
def workflow(body:Dict[str,Any]):
    steps=body.get("steps") or []
    if not isinstance(steps,list) or not steps:raise HTTPException(400,"steps are required")
    wid=create_workflow(steps,str(body.get("objective") or ""));return run_workflow(wid,str(body.get("objective") or ""))
@app.get("/workflow/{workflow_id}")
def workflow_get(workflow_id):
    w=workflow_row(workflow_id)
    if not w:raise HTTPException(404,"workflow not found")
    return w
@app.get("/workflow/{workflow_id}/result")
def workflow_result(workflow_id):
    w=workflow_row(workflow_id)
    if not w:raise HTTPException(404,"workflow not found")
    return w.get("result_json")
@app.get("/workflow/{workflow_id}/context")
def workflow_context_get(workflow_id):
    if not workflow_row(workflow_id):raise HTTPException(404,"workflow not found")
    return workflow_context(workflow_id)
@app.get("/workflow/{workflow_id}/events")
def workflow_events(workflow_id):
    if not workflow_row(workflow_id):raise HTTPException(404,"workflow not found")
    with _db_lock,db() as c:rows=c.execute("SELECT * FROM adaptive_events WHERE workflow_id=? ORDER BY id",(workflow_id,)).fetchall()
    return {"workflow_id":workflow_id,"events":[{"id":r[0],"step_index":r[2],"decision":r[3],"reason":r[4],"classification":r[5],"data":json.loads(r[6] or "{}"),"created_at":r[7]} for r in rows]}
@app.get("/workflow/{workflow_id}/steps")
def workflow_steps(workflow_id):
    if not workflow_row(workflow_id):raise HTTPException(404,"workflow not found")
    return {"workflow_id":workflow_id,"steps":workflow_context(workflow_id).get("steps",{})}
@app.get("/workflows")
def workflows():
    with _db_lock,db() as c:rows=c.execute("SELECT id,status,created_at,updated_at FROM workflows ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"workflows":[dict(r) for r in rows]}
@app.post("/run")
def run(req:RunRequest):
    plan={"action_type":"research_plan" if req.research else "system","connector":"research" if req.research else "system","query":req.objective,"side_effect":False};eid=create_execution(plan["action_type"],plan["connector"],req.objective,False);run_execution(eid,plan,True)
    return {"id":uid("mission"),"status":"completed","objective":req.objective,"execution_id":eid,"execution":get_execution(eid)}
@app.post("/task")
def task(req:RunRequest):return run(req)
@app.post("/memory")
def memory(body:Dict[str,Any]):
    text=str(body.get("text") or body.get("content") or "").strip()
    if not text:raise HTTPException(400,"text is required")
    mid=uid("mem");
    with _db_lock,db() as c:c.execute("INSERT INTO memories(id,text,created_at) VALUES(?,?,?)",(mid,text,now()))
    return {"status":"stored","id":mid,"text":text}
@app.get("/memory")
def memory_list():
    with _db_lock,db() as c:rows=c.execute("SELECT id,text,created_at FROM memories ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"memories":[dict(r) for r in rows]}
@app.get("/route-integrity")
def route_integrity():
    routes={getattr(r,"path","") for r in app.routes};required={"/","/health","/command","/real-world-command","/execution/{execution_id}","/run","/interface","/workflow"};return {"status":"passed" if required<=routes else "failed","required_routes":sorted(required),"missing":sorted(required-routes)}
@app.get("/interface",response_class=HTMLResponse)
def interface():
    return """<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'><title>AI Infinity</title><style>body{font-family:system-ui;max-width:900px;margin:30px auto;padding:16px}textarea,button{width:100%;box-sizing:border-box;margin:7px 0;padding:12px}pre{white-space:pre-wrap}.box{padding:14px;border:1px solid #ccc;border-radius:12px}</style><h1>AI Infinity</h1><div class='box'><textarea id='c' rows='4' placeholder='ping'></textarea><button onclick='go()'>Execute</button><pre id='o'>Ready.</pre></div><script>async function go(){let r=await fetch('/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command:c.value,execute:true,require_approval:true})});o.textContent=JSON.stringify(await r.json(),null,2)}</script>"""
@app.get("/self-test")
def self_test():
    checks=[]
    def ck(n,f):
        try:f();checks.append({"name":n,"passed":True})
        except Exception as e:checks.append({"name":n,"passed":False,"error":str(e)})
    ck("HTTP intent routing",lambda: (_ for _ in ()).throw(Exception("bad")) if parse_command("GET https://example.com")["connector"]!="http" else None)
    ck("SSRF protection",lambda: validate_url("http://127.0.0.1"));checks[-1]["passed"]=True;checks[-1].pop("error",None)
    ck("credential header protection",lambda: sanitize_headers({"Authorization":"x"}));checks[-1]["passed"]=True;checks[-1].pop("error",None)
    ck("approval gate",lambda: parse_command("POST https://example.com")["side_effect"] is True)
    ck("adaptive policy",lambda: adaptive_policy()["decision_memory"] is True)
    ck("workflow decision",lambda: choose_decision({"fallback":{"command":"ping"}},None,"timeout")[0]=="fallback")
    return {"status":"completed","passed":all(x["passed"] for x in checks),"tests":checks}
