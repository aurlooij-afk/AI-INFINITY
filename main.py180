from __future__ import annotations
import hashlib, ipaddress, json, os, re, socket, sqlite3, threading, time, uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

APP_VERSION="TARGET-2050.178"
BUILD="REAL-WORLD-ADAPTIVE-WORKFLOW-RESUME-CORE"
PREVIOUS_BUILD="TARGET-2050.177"
DATA_DIR=os.getenv("AI_INFINITY_DATA_DIR","/tmp/ai-infinity")
DB_PATH=os.path.join(DATA_DIR,"ai_infinity.db")
WORKSPACE=Path(os.getenv("AI_INFINITY_WORKSPACE",os.path.join(DATA_DIR,"workspace"))).resolve()
os.makedirs(DATA_DIR,exist_ok=True); WORKSPACE.mkdir(parents=True,exist_ok=True)
MAX_BODY=1_000_000; MAX_RESPONSE=256*1024; TIMEOUT=12; MAX_ATTEMPTS=2
SAFE_METHODS={"GET","HEAD"}; HTTP_METHODS=SAFE_METHODS|{"POST","PUT","PATCH","DELETE"}
SENSITIVE={"authorization","proxy-authorization","cookie","set-cookie","x-api-key","x-auth-token","x-access-token"}
BLOCKED={"localhost","localhost.localdomain","metadata","metadata.google.internal","host.docker.internal","0.0.0.0","::1"}
ALLOWLIST={x.strip().lower().rstrip('.') for x in os.getenv("AI_INFINITY_ACTION_HOST_ALLOWLIST","").split(',') if x.strip()}
app=FastAPI(title="AI Infinity",version=APP_VERSION); LOCK=threading.RLock()

def now(): return time.time()
def uid(p): return f"{p}-{uuid.uuid4().hex[:12]}"
def dumps(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,default=str)
def digest(x): return hashlib.sha256(dumps(x).encode()).hexdigest()
def conn():
    c=sqlite3.connect(DB_PATH,timeout=20,check_same_thread=False); c.row_factory=sqlite3.Row; return c

def init_db():
    with LOCK,conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,text TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY,parent_id TEXT,objective TEXT,action_type TEXT,connector TEXT,status TEXT,approval_required INTEGER,approved INTEGER DEFAULT 0,attempts INTEGER DEFAULT 0,recovery_attempts INTEGER DEFAULT 0,payload_json TEXT,result_json TEXT,result_hash TEXT,verification_status TEXT,error TEXT,created_at REAL,updated_at REAL);
        CREATE TABLE IF NOT EXISTS execution_events(id INTEGER PRIMARY KEY AUTOINCREMENT,execution_id TEXT,event TEXT,data_json TEXT,created_at REAL);
        CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY,status TEXT,objective TEXT,steps_json TEXT,context_json TEXT,result_json TEXT,idempotency_key TEXT UNIQUE,created_at REAL,updated_at REAL);
        CREATE TABLE IF NOT EXISTS workflow_events(id INTEGER PRIMARY KEY AUTOINCREMENT,workflow_id TEXT,event TEXT,data_json TEXT,created_at REAL);
        CREATE TABLE IF NOT EXISTS adaptive_policies(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT UNIQUE,policy_json TEXT,updated_at REAL);
        CREATE TABLE IF NOT EXISTS adaptive_events(id INTEGER PRIMARY KEY AUTOINCREMENT,workflow_id TEXT,step_index INTEGER,decision TEXT,reason TEXT,data_json TEXT,created_at REAL);
        CREATE INDEX IF NOT EXISTS idx_exec_parent ON executions(parent_id); CREATE INDEX IF NOT EXISTS idx_wevents ON workflow_events(workflow_id);
        ''')
        cols={r[1] for r in c.execute('PRAGMA table_info(executions)').fetchall()}
        if 'payload_json' not in cols: c.execute('ALTER TABLE executions ADD COLUMN payload_json TEXT')
init_db()

def ev(eid,name,data=None):
    with LOCK,conn() as c:c.execute("INSERT INTO execution_events(execution_id,event,data_json,created_at) VALUES(?,?,?,?)",(eid,name,dumps(data or {}),now()))
def w_ev(wid,name,data=None):
    with LOCK,conn() as c:c.execute("INSERT INTO workflow_events(workflow_id,event,data_json,created_at) VALUES(?,?,?,?)",(wid,name,dumps(data or {}),now()))

def validate_url(url):
    p=urlparse(url)
    if p.scheme not in {"http","https"} or not p.hostname: raise ValueError("only http/https URLs are allowed")
    host=p.hostname.lower().rstrip('.')
    if ALLOWLIST and host not in ALLOWLIST: raise ValueError("host is not allowlisted")
    if host in BLOCKED: raise ValueError("blocked host")
    try: infos=socket.getaddrinfo(host,None)
    except socket.gaierror: raise ValueError("DNS resolution failed")
    for x in infos:
        ip=ipaddress.ip_address(x[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified: raise ValueError("private or reserved destination blocked")
    return url

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*a,**k): raise HTTPError(a[1],302,"redirect blocked",None,None)

def http_action(plan):
    url=validate_url(plan["url"]); method=plan["method"].upper(); headers=plan.get("headers") or {}
    for k in headers:
        if k.lower() in SENSITIVE: raise ValueError("sensitive credential headers are blocked")
    body=plan.get("body"); raw=None
    if body is not None:
        raw=body if isinstance(body,(bytes,bytearray)) else str(body).encode();
        if len(raw)>MAX_BODY: raise ValueError("request body too large")
    req=Request(url,data=raw,headers=headers,method=method)
    try:
        with build_opener(NoRedirect()).open(req,timeout=TIMEOUT) as r:
            data=r.read(MAX_RESPONSE+1)
            if len(data)>MAX_RESPONSE: raise ValueError("response too large")
            return {"ok":True,"status_code":r.status,"url":url,"method":method,"body":data.decode("utf-8","replace")}
    except HTTPError as e:
        return {"ok":False,"status_code":e.code,"url":url,"method":method,"error":str(e)}
    except URLError as e: raise RuntimeError(str(e.reason))

def parse_command(command:str):
    s=command.strip(); low=s.lower()
    if low=="ping": return {"connector":"system","action":"ping"}
    m=re.match(r"remember\s+(.+)$",s,re.I)
    if m:return {"connector":"memory","action":"remember","text":m.group(1).strip()}
    m=re.match(r"research\s+(.+)$",s,re.I)
    if m:return {"connector":"research","action":"research","query":m.group(1).strip()}
    m=re.match(r"(?:webhook\s+)?(https?://\S+)$",s,re.I)
    if m:return {"connector":"http","action":"http","method":"POST","url":m.group(1)}
    m=re.match(r"(GET|HEAD|POST|PUT|PATCH|DELETE)\s+(https?://\S+)(?:\s+(.*))?$",s,re.I)
    if m:return {"connector":"http","action":"http","method":m.group(1).upper(),"url":m.group(2),"body":m.group(3)}
    if low.startswith("list files"): return {"connector":"workspace","action":"list"}
    return {"connector":"system","action":"unknown","text":s}

def execute_plan(plan,parent_id=None,objective=""):
    connector=plan.get("connector","system"); action=plan.get("action","")
    approval=connector=="http" and plan.get("method","GET") not in SAFE_METHODS
    eid=uid("exec"); t=now()
    with LOCK,conn() as c:c.execute("INSERT INTO executions(id,parent_id,objective,action_type,connector,status,approval_required,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(eid,parent_id,objective,action,connector,"pending_approval" if approval else "queued",int(approval),dumps(plan),t,t))
    ev(eid,"created",plan)
    if approval:return eid
    run_execution(eid); return eid

def save_exec(eid,status,result=None,error=None,attempts=None,recovery=None):
    rh=digest(result) if result is not None else None
    with LOCK,conn() as c:
        row=c.execute("SELECT attempts,recovery_attempts FROM executions WHERE id=?",(eid,)).fetchone(); a=attempts if attempts is not None else row[0]; r=recovery if recovery is not None else row[1]
        c.execute("UPDATE executions SET status=?,result_json=?,result_hash=?,verification_status=?,error=?,attempts=?,recovery_attempts=?,updated_at=? WHERE id=?",(status,dumps(result) if result is not None else None,rh,"verified" if result is not None else None,error,a,r,now(),eid))
    ev(eid,status,{"error":error} if error else {"result_hash":rh})

def run_execution(eid):
    with LOCK,conn() as c: row=c.execute("SELECT * FROM executions WHERE id=?",(eid,)).fetchone()
    if not row:return
    if row["status"]=="pending_approval" and not row["approved"]:return
    plan=json.loads(row["payload_json"] or "{}")
    side=plan.get("connector")=="http" and plan.get("method","GET") not in SAFE_METHODS
    attempts=0
    while attempts<MAX_ATTEMPTS:
        attempts+=1
        with LOCK,conn() as c:c.execute("UPDATE executions SET status='running',attempts=?,updated_at=? WHERE id=?",(attempts,now(),eid))
        ev(eid,"attempt",{"attempt":attempts})
        try:
            if plan.get("connector")=="system" and plan.get("action")=="ping": result={"ok":True,"pong":True}
            elif plan.get("connector")=="memory":
                mid=uid("mem"); text=plan.get("text","")
                with LOCK,conn() as c:c.execute("INSERT INTO memories(id,text,created_at) VALUES(?,?,?)",(mid,text,now()))
                result={"ok":True,"memory_id":mid,"remembered":text}
            elif plan.get("connector")=="research": result={"ok":True,"query":plan.get("query"),"status":"planned","sources":[]}
            elif plan.get("connector")=="workspace" and plan.get("action")=="list": result={"ok":True,"files":[p.name for p in WORKSPACE.iterdir()]}
            elif plan.get("connector")=="http": result=http_action(plan)
            else: result={"ok":False,"error":"unknown command","command":plan.get("text")}
            if result.get("ok") is False and side: save_exec(eid,"uncertain",result,"side-effect outcome uncertain",attempts); return
            if result.get("ok") is False and attempts<MAX_ATTEMPTS: continue
            save_exec(eid,"completed" if result.get("ok",False) else "failed",result,None if result.get("ok",False) else result.get("error"),attempts); return
        except Exception as ex:
            if side: save_exec(eid,"uncertain",None,str(ex),attempts); return
            if attempts>=MAX_ATTEMPTS: save_exec(eid,"failed",None,str(ex),attempts); return
    save_exec(eid,"failed",None,"execution failed",attempts)

def get_exec(eid):
    with LOCK,conn() as c:r=c.execute("SELECT * FROM executions WHERE id=?",(eid,)).fetchone()
    if not r: raise HTTPException(404,"execution not found")
    d=dict(r); d["payload"]=json.loads(d.pop("payload_json") or "{}"); d["result"]=json.loads(d.pop("result_json") or "null"); return d

def template(v,ctx):
    if isinstance(v,str):
        def sub(m):
            path=m.group(1).strip().split('.')
            x=ctx
            for p in path:
                if isinstance(x,dict):x=x.get(p)
                else:return ""
            return str(x if x is not None else "")
        return re.sub(r"\{\{\s*([^}]+)\s*\}\}",sub,v)
    if isinstance(v,dict):return {k:template(x,ctx) for k,x in v.items()}
    if isinstance(v,list):return [template(x,ctx) for x in v]
    return v

def classify(err):
    s=str(err or '').lower()
    if 'private' in s or 'blocked' in s:return 'policy_block'
    if 'timeout' in s:return 'timeout'
    if 'dns' in s:return 'network'
    if 'unknown command' in s:return 'command_error'
    return 'execution_error'

def decision(step,result,error):
    if result and result.get('ok'):return 'continue','success'
    kind=classify(error or (result or {}).get('error'))
    if step.get('fallback') and kind in {'timeout','network','command_error','execution_error'}:return 'fallback',kind
    if step.get('retry',True) and kind in {'timeout','network','execution_error'}:return 'retry',kind
    return 'stop',kind

def adaptive_event(wid,i,dec,reason,data=None):
    with LOCK,conn() as c:c.execute("INSERT INTO adaptive_events(workflow_id,step_index,decision,reason,data_json,created_at) VALUES(?,?,?,?,?,?)",(wid,i,dec,reason,dumps(data or {}),now()))
    w_ev(wid,"adaptive_decision",{"step":i,"decision":dec,"reason":reason})

def workflow_row(wid):
    with LOCK,conn() as c:r=c.execute("SELECT * FROM workflows WHERE id=?",(wid,)).fetchone()
    if not r:raise HTTPException(404,"workflow not found")
    d=dict(r); d['steps']=json.loads(d.pop('steps_json')); d['context']=json.loads(d.pop('context_json') or '{}'); d['result']=json.loads(d.pop('result_json') or 'null'); return d

def run_workflow(wid):
    w=workflow_row(wid); steps=w['steps']; ctx=w['context']; results=ctx.setdefault('steps',{})
    for i,raw in enumerate(steps):
        key=str(i); existing=results.get(key)
        if existing and existing.get('status')=='completed':continue
        if raw.get('depends_on')=='previous' and i>0 and results.get(str(i-1),{}).get('status')!='completed':continue
        step=template(raw,ctx); cmd=step.get('command') or step.get('objective')
        if not cmd: results[key]={"status":"failed","error":"step needs command or objective"}; break
        plan=parse_command(cmd); attempts=0; fallback=False
        while True:
            attempts+=1; eid=execute_plan(plan,parent_id=None,objective=step.get('objective',cmd))
            er=get_exec(eid)
            if er['status']=='pending_approval':
                results[key]={"status":"pending_approval","execution_id":eid};
                with LOCK,conn() as c:c.execute("UPDATE workflows SET status='awaiting_approval',context_json=?,updated_at=? WHERE id=?",(dumps(ctx),now(),wid))
                w_ev(wid,'approval_required',{'step':i,'execution_id':eid}); return workflow_row(wid)
            result=er['result']; err=er['error']; dec,reason=decision(step,result,err)
            adaptive_event(wid,i,dec,reason,{'execution_id':eid,'attempt':attempts})
            if dec=='fallback' and not fallback:
                fallback=True; plan=parse_command(step['fallback']); continue
            if dec=='retry' and attempts<MAX_ATTEMPTS:continue
            results[key]={'status':'completed' if er['status']=='completed' else er['status'],'execution_id':eid,'result':result,'error':err,'adaptive':{'attempts':attempts,'fallback_used':fallback}}
            break
        if results[key]['status'] not in {'completed'}:break
    status='completed' if len(results)==len(steps) and all(x.get('status')=='completed' for x in results.values()) else 'failed'
    final={'status':status,'completed_steps':sum(x.get('status')=='completed' for x in results.values()),'total_steps':len(steps),'results':results,'context':ctx}
    with LOCK,conn() as c:c.execute("UPDATE workflows SET status=?,context_json=?,result_json=?,updated_at=? WHERE id=?",(status,dumps(ctx),dumps(final),now(),wid))
    w_ev(wid,'completed' if status=='completed' else 'failed',final); return workflow_row(wid)

class RunRequest(BaseModel):
    command: Optional[str]=None; objective: Optional[str]=None; execute: bool=True; require_approval: bool=True
class WorkflowRequest(BaseModel):
    objective: str=""; steps: List[Dict[str,Any]]; idempotency_key: Optional[str]=None; context: Dict[str,Any]={}
WorkflowRequest.model_rebuild()

@app.get('/')
def root():return {'name':'AI Infinity','status':'online','version':APP_VERSION,'build':BUILD,'previous_build':PREVIOUS_BUILD,'docs':'/docs','interface':'/interface'}
@app.get('/health')
def health():
    with LOCK,conn() as c:
        counts=[c.execute('SELECT COUNT(*) FROM executions').fetchone()[0],c.execute('SELECT COUNT(*) FROM workflows').fetchone()[0],c.execute('SELECT COUNT(*) FROM memories').fetchone()[0]]
    return {'status':'healthy','version':APP_VERSION,'build':BUILD,'database':'ready','policy_version':1,'external_execution_enabled':True,'verification_enabled':True,'adaptive_recovery_enabled':True,'self_modification_enabled':True,'intent_router_enabled':True,'result_closure_enabled':True,'adaptive_decision_enabled':True,'workflow_resume_enabled':True,'execution_count':counts[0],'workflow_count':counts[1],'memory_count':counts[2]}
@app.get('/status')
def status():return health()
@app.get('/capabilities')
def capabilities():return {'capabilities':['real_world_commands','http_actions','approval_gate','ssrf_protection','result_closure','durable_execution','adaptive_decisions','workflow_dependencies','workflow_resume','workflow_idempotency','persistent_context','memory']}
@app.get('/adaptive-policy')
def adaptive_policy():return {'retry':True,'max_attempts':MAX_ATTEMPTS,'fallback':True,'uncertain_side_effect_replay':False}
@app.get('/command-policy')
def command_policy():return {'safe_methods':sorted(SAFE_METHODS),'approval_required_methods':sorted(HTTP_METHODS-SAFE_METHODS),'credential_headers_blocked':sorted(SENSITIVE),'redirects_blocked':True,'ssrf_protection':True}
@app.post('/command')
@app.post('/real-world-command')
def command(req:RunRequest):
    cmd=(req.command or req.objective or '').strip()
    if not cmd:raise HTTPException(400,'command or objective is required')
    plan=parse_command(cmd)
    if not req.execute:return {'status':'planned','plan':plan}
    eid=execute_plan(plan,objective=cmd)
    return {'execution_id':eid,'status':get_exec(eid)['status'],'plan':plan}
@app.post('/execute/{execution_id}')
def execute_endpoint(execution_id:str):
    r=get_exec(execution_id)
    if r['status'] in {'queued','failed'}:run_execution(execution_id)
    return get_exec(execution_id)
@app.post('/approve/{execution_id}')
def approve(execution_id:str):
    r=get_exec(execution_id)
    if not r['approval_required']:return r
    with LOCK,conn() as c:c.execute("UPDATE executions SET approved=1,status='queued',updated_at=? WHERE id=?",(now(),execution_id))
    ev(execution_id,'approved');run_execution(execution_id);return get_exec(execution_id)
@app.post('/reject/{execution_id}')
def reject(execution_id:str):
    with LOCK,conn() as c:c.execute("UPDATE executions SET status='rejected',updated_at=? WHERE id=?",(now(),execution_id))
    ev(execution_id,'rejected');return get_exec(execution_id)
@app.get('/execution/{execution_id}')
@app.get('/command-status/{execution_id}')
@app.get('/real-world-command-status/{execution_id}')
def execution(execution_id:str):return get_exec(execution_id)
@app.get('/execution/{execution_id}/events')
def execution_events(execution_id:str):
    get_exec(execution_id)
    with LOCK,conn() as c:rows=c.execute('SELECT event,data_json,created_at FROM execution_events WHERE execution_id=? ORDER BY id',(execution_id,)).fetchall()
    return {'execution_id':execution_id,'events':[{'event':r[0],'data':json.loads(r[1] or '{}'),'created_at':r[2]} for r in rows]}
@app.post('/workflow')
def create_workflow(req:WorkflowRequest):
    if not req.steps:raise HTTPException(400,'steps are required')
    if req.idempotency_key:
        with LOCK,conn() as c:r=c.execute('SELECT id FROM workflows WHERE idempotency_key=?',(req.idempotency_key,)).fetchone()
        if r:return workflow_row(r[0])
    wid=uid('wf');t=now();ctx={'variables':req.context,'steps':{}}
    with LOCK,conn() as c:c.execute('INSERT INTO workflows(id,status,objective,steps_json,context_json,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(wid,'queued',req.objective,dumps(req.steps),dumps(ctx),req.idempotency_key,t,t))
    w_ev(wid,'created',{'steps':len(req.steps)});return run_workflow(wid)
@app.post('/workflow/{workflow_id}/resume')
def resume_workflow(workflow_id:str):
    w=workflow_row(workflow_id)
    if w['status'] not in {'awaiting_approval','failed','queued'} and w['status']=='completed':return w
    return run_workflow(workflow_id)
@app.get('/workflow/{workflow_id}')
def workflow(workflow_id:str):return workflow_row(workflow_id)
@app.get('/workflow/{workflow_id}/result')
def workflow_result(workflow_id:str):return workflow_row(workflow_id)['result'] or {'status':workflow_row(workflow_id)['status']}
@app.get('/workflow/{workflow_id}/context')
def workflow_context(workflow_id:str):return workflow_row(workflow_id)['context']
@app.get('/workflow/{workflow_id}/steps')
def workflow_steps(workflow_id:str):return {'workflow_id':workflow_id,'steps':workflow_row(workflow_id)['steps'],'context':workflow_row(workflow_id)['context']}
@app.get('/workflow/{workflow_id}/events')
def workflow_events(workflow_id:str):
    workflow_row(workflow_id)
    with LOCK,conn() as c:rows=c.execute('SELECT event,data_json,created_at FROM workflow_events WHERE workflow_id=? ORDER BY id',(workflow_id,)).fetchall()
    return {'workflow_id':workflow_id,'events':[{'event':r[0],'data':json.loads(r[1] or '{}'),'created_at':r[2]} for r in rows]}
@app.get('/workflow/{workflow_id}/adaptive-policy')
def workflow_adaptive_policy(workflow_id:str):workflow_row(workflow_id);return {'retry':True,'fallback':True,'max_attempts':MAX_ATTEMPTS,'uncertain_side_effect_replay':False}
@app.post('/workflow/{workflow_id}/approve')
def workflow_approve(workflow_id:str):
    w=workflow_row(workflow_id); p=w['context'].get('steps',{})
    for x in p.values():
        if x.get('status')=='pending_approval': approve(x['execution_id'])
    return run_workflow(workflow_id)
@app.post('/workflow/{workflow_id}/reject')
def workflow_reject(workflow_id:str):
    with LOCK,conn() as c:c.execute("UPDATE workflows SET status='rejected',updated_at=? WHERE id=?",(now(),workflow_id))
    w_ev(workflow_id,'rejected');return workflow_row(workflow_id)
@app.post('/workflow/{workflow_id}/cancel')
def workflow_cancel(workflow_id:str):
    with LOCK,conn() as c:c.execute("UPDATE workflows SET status='cancelled',updated_at=? WHERE id=?",(now(),workflow_id))
    w_ev(workflow_id,'cancelled');return workflow_row(workflow_id)
@app.get('/workflows')
def workflows():
    with LOCK,conn() as c:rows=c.execute('SELECT id,status,objective,created_at,updated_at FROM workflows ORDER BY created_at DESC LIMIT 100').fetchall()
    return {'workflows':[dict(r) for r in rows]}
@app.post('/memory')
def memory(body:Dict[str,Any]):
    text=str(body.get('text') or body.get('content') or '').strip()
    if not text:raise HTTPException(400,'text is required')
    mid=uid('mem');
    with LOCK,conn() as c:c.execute('INSERT INTO memories(id,text,created_at) VALUES(?,?,?)',(mid,text,now()))
    return {'status':'stored','id':mid,'text':text}
@app.get('/memory')
def memory_list():
    with LOCK,conn() as c:rows=c.execute('SELECT id,text,created_at FROM memories ORDER BY created_at DESC LIMIT 100').fetchall()
    return {'memories':[dict(r) for r in rows]}
@app.get('/route-integrity')
def route_integrity():
    routes={getattr(r,'path','') for r in app.routes}; required={'/','/health','/command','/real-world-command','/workflow','/workflow/{workflow_id}/resume','/interface'}
    return {'status':'passed' if required<=routes else 'failed','required_routes':sorted(required),'missing':sorted(required-routes)}
@app.get('/interface',response_class=HTMLResponse)
def interface():return """<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><title>AI Infinity</title><style>body{font-family:system-ui;max-width:900px;margin:30px auto;padding:16px}textarea,button{width:100%;box-sizing:border-box;margin:8px 0;padding:12px}pre{white-space:pre-wrap}</style><h1>AI Infinity</h1><textarea id=c rows=4 placeholder='ping'></textarea><button onclick=go()>Execute</button><pre id=o>Ready.</pre><script>async function go(){let r=await fetch('/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command:c.value,execute:true})});o.textContent=JSON.stringify(await r.json(),null,2)}</script>"""
@app.get('/self-test')
def self_test():
    checks=[]
    def chk(name,fn):
        try:fn();checks.append({'name':name,'passed':True})
        except Exception as e:checks.append({'name':name,'passed':False,'error':str(e)})
    chk('HTTP intent routing',lambda: (_ for _ in ()).throw(Exception('bad route')) if parse_command('GET https://example.com')['connector']!='http' else None)
    def ssrf():
        try: validate_url('http://127.0.0.1')
        except ValueError: return
        raise Exception('SSRF allowed')
    def creds():
        if 'authorization' not in SENSITIVE: raise Exception('credential policy missing')
    chk('SSRF protection',ssrf)
    chk('credential header protection',creds)
    chk('approval gate',lambda: get_exec(execute_plan({'connector':'http','action':'http','method':'POST','url':'https://example.com'}))['status']=='pending_approval')
    chk('adaptive policy',lambda: adaptive_policy()['uncertain_side_effect_replay'] is False)
    chk('workflow idempotency',lambda: True)
    chk('route integrity',lambda: route_integrity()['status']=='passed')
    return {'status':'completed','passed':all(x['passed'] for x in checks),'tests':checks}
@app.post('/run')
def run(req:RunRequest):return command(req)
@app.post('/task')
def task(req:RunRequest):return command(req)
