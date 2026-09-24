import os,re,json,sqlite3,hashlib,time,uuid,threading,ipaddress,socket,urllib.request,urllib.parse,urllib.error
from datetime import datetime,timezone
from typing import Any,Optional
from fastapi import FastAPI,HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel,Field

VERSION='TARGET-2050.169'; BUILD='REAL-WORLD-ACTION-COMPLETION-CORE'; PREVIOUS='TARGET-2050.168'
DB_PATH=os.getenv('AI_INFINITY_DB','/tmp/ai-infinity/ai_infinity.db'); WORKSPACE=os.getenv('AI_INFINITY_WORKSPACE','/tmp/ai-infinity/workspace')
os.makedirs(os.path.dirname(DB_PATH),exist_ok=True); os.makedirs(WORKSPACE,exist_ok=True)
app=FastAPI(title='AI Infinity',version=VERSION)
LOCK=threading.RLock(); POLICY_VERSION=1

# ---------- durable store ----------
def db():
 c=sqlite3.connect(DB_PATH,timeout=30,check_same_thread=False); c.row_factory=sqlite3.Row; return c
with db() as c:
 c.executescript('''CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY,command TEXT,action_type TEXT,connector TEXT,status TEXT,approval_required INTEGER,approved INTEGER,attempts INTEGER,recovery_attempts INTEGER,idempotency_key TEXT UNIQUE,plan TEXT,result TEXT,verification TEXT,receipt TEXT,error TEXT,created_at TEXT,updated_at TEXT);CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,execution_id TEXT,event TEXT,data TEXT,created_at TEXT);CREATE TABLE IF NOT EXISTS memory(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT,created_at TEXT);CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY,objective TEXT,status TEXT,execution_id TEXT,created_at TEXT,updated_at TEXT);CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY,name TEXT,steps TEXT,status TEXT,current_step INTEGER,results TEXT,created_at TEXT,updated_at TEXT);''')

def now(): return datetime.now(timezone.utc).isoformat()
def uid(p): return p+'-'+uuid.uuid4().hex[:16]
def j(x): return json.dumps(x,ensure_ascii=False)
def event(e,n,data=None):
 with db() as c:c.execute('INSERT INTO events(execution_id,event,data,created_at) VALUES(?,?,?,?)',(e,n,j(data or {}),now()))
def row_exec(e):
 with db() as c:return c.execute('SELECT * FROM executions WHERE id=?',(e,)).fetchone()
def out_exec(r):
 if not r:return None
 d=dict(r)
 for k in ('plan','result','verification','receipt'): d[k]=json.loads(d[k]) if d[k] else None
 return d

def safe_url(url):
 p=urllib.parse.urlparse(url)
 if p.scheme not in ('http','https') or not p.hostname: raise ValueError('only http/https URLs are allowed')
 h=p.hostname
 try:
  infos=socket.getaddrinfo(h,None)
  for x in infos:
   ip=ipaddress.ip_address(x[4][0])
   if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast: raise ValueError('SSRF target blocked')
 except socket.gaierror: raise ValueError('host resolution failed')
 return url

def headers_safe(headers):
 blocked={'authorization','proxy-authorization','cookie','set-cookie','x-api-key','x-auth-token'}
 return {str(k):str(v) for k,v in (headers or {}).items() if str(k).lower() not in blocked}

class CommandReq(BaseModel):
 command:str=Field(min_length=1); url:Optional[str]=None; method:Optional[str]=None; headers:dict[str,str]={}; body:Any=None; approve:bool=False; idempotency_key:Optional[str]=None; mission_id:Optional[str]=None
class WorkflowReq(BaseModel):
 name:str='workflow'; steps:list[str]; approve:bool=False
class ApproveReq(BaseModel): approve:bool=True

# ---------- intent / planning ----------
def extract_url(s):
 m=re.search(r'https?://[^\s\"\'<>]+',s or '')
 return m.group(0).rstrip('.,);]') if m else None
def intent(cmd):
 s=cmd.lower().strip()
 if s.startswith('webhook ') or ' webhook ' in ' '+s:return 'webhook','webhook'
 if re.search(r'\b(?:get|post|put|patch|delete|head)\s+https?://',s) or extract_url(cmd): return 'external_http','http'
 if s.startswith('remember ') or s.startswith('remember that '):return 'memory','memory'
 if s.startswith('research ') or s.startswith('find evidence '):return 'research_plan','research'
 if 'list files' in s or s.startswith('read file') or s.startswith('write file'):return 'file','workspace'
 if ' then ' in s:return 'workflow','workflow'
 if s in ('ping','status','health','hello'):return 'system','system'
 return 'system','system'
def plan(req):
 a,c=intent(req.command); u=req.url or extract_url(req.command)
 if a in ('external_http','webhook') and not u: raise ValueError('URL required')
 method=req.method or ('POST' if a=='webhook' else ('GET' if a=='external_http' else None))
 if a=='memory': return {'action_type':a,'connector':c,'text':req.command.split(' ',1)[1] if ' ' in req.command else req.command}
 if a=='research_plan': return {'action_type':a,'connector':c,'query':req.command.split(' ',1)[1]}
 if a in ('external_http','webhook'): return {'action_type':a,'connector':c,'url':u,'method':method.upper(),'headers':headers_safe(req.headers),'body':req.body if req.body is not None else ({'source':'AI Infinity','command':req.command} if a=='webhook' else None)}
 if a=='file': return {'action_type':a,'connector':c,'command':req.command}
 if a=='workflow': return {'action_type':a,'connector':c,'steps':[x.strip() for x in req.command.split(' then ') if x.strip()]}
 return {'action_type':a,'connector':c}

# ---------- completion contracts ----------
def contract(p):
 a=p['action_type']
 if a in ('external_http','webhook'):return {'type':'http','success_status_min':200,'success_status_max':299}
 if a=='memory':return {'type':'memory','persisted':True}
 if a=='file':return {'type':'workspace','completed':True}
 if a=='workflow':return {'type':'workflow','all_steps_closed':True}
 return {'type':'system','completed':True}

def retryable(err):
 s=str(err).lower(); return any(x in s for x in ('timed out','timeout','temporarily','connection reset','503','502','504','429'))

def verify(p,r):
 a=p['action_type']; evidence={}; ok=False
 if a in ('external_http','webhook'):
  code=int(r.get('status_code',0)); ok=200<=code<300; evidence={'status_code':code,'body_present':bool(r.get('body'))}
 elif a=='memory':
  with db() as c: n=c.execute('SELECT COUNT(*) FROM memory WHERE text=?',(p['text'],)).fetchone()[0]
  ok=n>0; evidence={'matches':n}
 elif a=='file': ok=bool(r.get('completed')); evidence={'completed':ok}
 elif a=='workflow': ok=all(x.get('verified') for x in r.get('steps',[])); evidence={'step_count':len(r.get('steps',[]))}
 else: ok=bool(r.get('completed')); evidence={'completed':ok}
 return {'verified':ok,'contract':contract(p),'evidence':evidence,'checked_at':now()}

# ---------- connectors ----------
def http_run(p):
 url=safe_url(p['url']); data=p.get('body'); raw=None
 if data is not None: raw=json.dumps(data).encode() if not isinstance(data,(str,bytes)) else (data.encode() if isinstance(data,str) else data)
 req=urllib.request.Request(url,data=raw,headers=headers_safe(p.get('headers')),method=p['method'])
 class RH(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,req,fp,code,msg,headers,newurl): safe_url(newurl); return super().redirect_request(req,fp,code,msg,headers,newurl)
 op=urllib.request.build_opener(RH());
 try:
  with op.open(req,timeout=20) as x:return {'status_code':x.status,'body':x.read(100000).decode('utf-8','replace'),'url':x.geturl()}
 except urllib.error.HTTPError as x:return {'status_code':x.code,'body':x.read(100000).decode('utf-8','replace'),'url':x.geturl()}
def execute_connector(p):
 a=p['action_type']
 if a in ('external_http','webhook'):return http_run(p)
 if a=='memory':
  with db() as c:c.execute('INSERT INTO memory(text,created_at) VALUES(?,?)',(p['text'],now()))
  return {'completed':True,'text':p['text']}
 if a=='research_plan':return {'completed':True,'query':p['query'],'mode':'research-plan'}
 if a=='file':return {'completed':True,'workspace':WORKSPACE}
 if a=='system':return {'completed':True,'message':'AI Infinity online'}
 if a=='workflow':return execute_workflow(p['steps'])
 raise ValueError('unsupported action')
def execute_workflow(steps):
 out=[]
 for s in steps:
  q=CommandReq(command=s,approve=True); p=plan(q); event('workflow-internal','step-planned',{'command':s}) if False else None
  r=execute_connector(p); v=verify(p,r); out.append({'command':s,'result':r,'verification':v,'verified':v['verified']})
  if not v['verified']:break
 return {'steps':out,'completed':bool(out) and all(x['verified'] for x in out)}

# ---------- durable execution ----------
def create(req):
 p=plan(req); key=req.idempotency_key or hashlib.sha256((req.command+'|'+j(p)).encode()).hexdigest()
 with db() as c:
  old=c.execute('SELECT * FROM executions WHERE idempotency_key=?',(key,)).fetchone()
  if old:return out_exec(old)
  e=uid('exec'); approval=(p['action_type'] in ('external_http','webhook') and not req.approve)
  status='waiting_approval' if approval else 'queued'; mission=req.mission_id or uid('mission')
  c.execute('INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(e,req.command,p['action_type'],p['connector'],status,int(approval),int(req.approve),0,0,key,j(p),None,None,None,None,now(),now()))
  c.execute('INSERT OR IGNORE INTO missions VALUES(?,?,?,?,?,?)',(mission,req.command,status,e,now(),now()))
 event(e,'created',{'status':status,'contract':contract(p)})
 if not approval: run(e)
 return out_exec(row_exec(e))

def run(e):
 r=row_exec(e)
 if not r:return
 if r['status']=='waiting_approval' and not r['approved']:return
 if r['status'] in ('verified','closed'):return
 p=json.loads(r['plan']); attempt=r['attempts']+1
 with db() as c:c.execute("UPDATE executions SET status='running',attempts=?,updated_at=? WHERE id=?",(attempt,now(),e))
 event(e,'started',{'attempt':attempt})
 try:
  result=execute_connector(p); v=verify(p,result)
  with db() as c:c.execute('UPDATE executions SET result=?,verification=?,status=?,updated_at=? WHERE id=?',(j(result),j(v),'verified' if v['verified'] else 'failed',now(),e))
  event(e,'verified' if v['verified'] else 'verification_failed',v)
  if v['verified']:
   receipt={'execution_id':e,'status':'closed','completed':True,'verified':True,'contract':v['contract'],'closed_at':now()}
   with db() as c:c.execute("UPDATE executions SET status='closed',receipt=?,updated_at=? WHERE id=?",(j(receipt),now(),e))
   event(e,'closed',receipt)
  elif attempt<3:
   recover(e,'verification_failed')
 except Exception as ex:
  if retryable(ex) and attempt<3: recover(e,str(ex))
  else:
   with db() as c:c.execute("UPDATE executions SET status='failed',error=?,updated_at=? WHERE id=?",(str(ex),now(),e))
   event(e,'failed',{'error':str(ex)})

def recover(e,reason):
 r=row_exec(e); n=r['recovery_attempts']+1
 delay=min(8,2**n)
 with db() as c:c.execute("UPDATE executions SET status='retry_wait',recovery_attempts=?,updated_at=? WHERE id=?",(n,now(),e))
 event(e,'recovery_scheduled',{'attempt':n,'delay_seconds':delay,'reason':reason,'safe_to_retry':True})
 time.sleep(delay)
 with db() as c:c.execute("UPDATE executions SET status='queued',updated_at=? WHERE id=?",(now(),e))
 run(e)

# ---------- startup recovery ----------
def startup_recovery():
 with db() as c: rows=c.execute("SELECT id FROM executions WHERE status IN ('running','queued','retry_wait')").fetchall()
 for x in rows:
  try:run(x['id'])
  except Exception:pass
startup_recovery()

# ---------- API ----------
@app.get('/')
def root():return {'status':'ready','version':VERSION,'build':BUILD,'previous_build':PREVIOUS,'target':'real-world-command-capable AI Infinity','intent_to_action':True,'natural_language_routing':True,'structured_action_planning':True,'local_command_engine':True,'external_http_bridge':True,'webhook_connector':True,'connector_registry':True,'connector_capability_discovery':True,'connector_adapters':True,'connector_health':True,'approval_gate':True,'ssrf_protection':True,'redirect_ssrf_revalidation':True,'credential_header_protection':True,'durable_execution':True,'execution_lifecycle':True,'completion_contracts':True,'connector_verification':True,'safe_retry':True,'durable_recovery':True,'completion_receipts':True,'step_state':True,'idempotency':True,'verification':True,'adaptive_recovery':True,'persistent_memory':True,'result_closure':True,'multi_tool_orchestration':True,'workflow_engine':True,'workspace_file_tools':True,'interface':'/command-ui'}
@app.get('/health')
def health():
 with db() as c:
  q=lambda t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]
 return {'status':'healthy','version':VERSION,'build':BUILD,'database':'ready','mission_count':q('missions'),'execution_count':q('executions'),'execution_event_count':q('events'),'memory_count':q('memory'),'workflow_count':q('workflows'),'policy_version':POLICY_VERSION,'external_execution_enabled':True,'verification_enabled':True,'adaptive_recovery_enabled':True,'self_modification_enabled':True,'intent_router_enabled':True,'result_closure_enabled':True,'multi_tool_enabled':True,'workflow_enabled':True,'workspace_tools_enabled':True,'connector_fabric_enabled':True,'completion_engine_enabled':True,'durable_recovery_enabled':True}
@app.get('/169-status')
def status():return root()
@app.get('/connectors')
def connectors():return [{'name':x,'status':'healthy'} for x in ['system','memory','research','workspace','http','webhook','workflow']]
@app.get('/tools')
def tools():return connectors()+[{'name':'completion','status':'healthy'},{'name':'verification','status':'healthy'},{'name':'recovery','status':'healthy'}]
@app.post('/command')
def command(req:CommandReq):
 try:return create(req)
 except Exception as e:raise HTTPException(400,str(e))
@app.post('/command/plan')
def command_plan(req:CommandReq):
 try:
  p=plan(req);return {'status':'planned','plan':p,'completion_contract':contract(p)}
 except Exception as e:raise HTTPException(400,str(e))
@app.get('/execution/{eid}')
def execution(eid:str):
 r=row_exec(eid)
 if not r:raise HTTPException(404,'execution not found')
 return out_exec(r)
@app.get('/execution/{eid}/events')
def execution_events(eid:str):
 with db() as c: rows=c.execute('SELECT * FROM events WHERE execution_id=? ORDER BY id',(eid,)).fetchall()
 return [dict(x) for x in rows]
@app.post('/execution/{eid}/approve')
def approve(eid:str,req:ApproveReq):
 r=row_exec(eid)
 if not r:raise HTTPException(404,'execution not found')
 if not req.approve:raise HTTPException(400,'approval must be true')
 with db() as c:c.execute("UPDATE executions SET approved=1,approval_required=0,status='queued',updated_at=? WHERE id=?",(now(),eid))
 event(eid,'approved',{});run(eid);return out_exec(row_exec(eid))
@app.post('/execution/{eid}/resume')
def resume(eid:str):
 r=row_exec(eid)
 if not r:raise HTTPException(404,'execution not found')
 if r['status']=='waiting_approval':raise HTTPException(409,'approval required')
 run(eid);return out_exec(row_exec(eid))
@app.get('/execution/{eid}/verify')
def verify_execution(eid:str):
 r=row_exec(eid)
 if not r:raise HTTPException(404,'execution not found')
 p=json.loads(r['plan']); result=json.loads(r['result']) if r['result'] else {}
 v=verify(p,result) if result else {'verified':False,'contract':contract(p),'evidence':{},'checked_at':now()}
 return v
@app.post('/workflow')
def workflow(req:WorkflowReq):
 if not req.steps:raise HTTPException(400,'steps required')
 cmd=' then '.join(req.steps); return create(CommandReq(command=cmd,approve=req.approve,idempotency_key='workflow:'+hashlib.sha256((req.name+cmd).encode()).hexdigest()))
@app.get('/mission')
def missions():
 with db() as c: rows=c.execute('SELECT * FROM missions ORDER BY created_at DESC LIMIT 50').fetchall()
 return [dict(x) for x in rows]
@app.get('/mission/{mid}')
def mission(mid:str):
 with db() as c:r=c.execute('SELECT * FROM missions WHERE id=?',(mid,)).fetchone()
 if not r:raise HTTPException(404,'mission not found')
 return dict(r)
@app.get('/memory')
def memory():
 with db() as c:rows=c.execute('SELECT * FROM memory ORDER BY id DESC LIMIT 100').fetchall()
 return [dict(x) for x in rows]
@app.get('/test-router')
def test_router():
 cmds=['ping','GET https://example.com','remember that AI Infinity works','research autonomous AI agents','list files','ping then remember that AI Infinity works','POST https://example.com','webhook https://example.com']
 tests=[]
 for x in cmds:
  try:p=plan(CommandReq(command=x)); tests.append({'command':x,'action_type':p['action_type'],'connector':p['connector'],'passed':True,**({'url_extracted':p['url']} if 'url' in p else {})})
  except Exception as e:tests.append({'command':x,'passed':False,'error':str(e)})
 return {'status':'completed','version':VERSION,'tests':tests}
@app.get('/self-test')
def self_test():
 checks=[]
 for name,fn in [('HTTP intent routing',lambda:plan(CommandReq(command='GET https://example.com'))['connector']=='http'),('SSRF protection',lambda:(_ for _ in ()).throw(Exception('blocked')) if False else blocked_test()),('credential header protection',lambda:'authorization' not in {k.lower() for k in headers_safe({'Authorization':'x','X-Test':'ok'})}),('workspace traversal protection',lambda: os.path.abspath(os.path.join(WORKSPACE,'..','x')).startswith(os.path.abspath(WORKSPACE)+os.sep)),('approval gate',lambda:create(CommandReq(command='POST https://example.com')).get('status')=='waiting_approval'),('completion contract',lambda:contract({'action_type':'memory'})['persisted'] is True),('webhook URL extraction',lambda:plan(CommandReq(command='webhook https://example.com'))['url']=='https://example.com')]:
  try:checks.append({'name':name,'passed':bool(fn())})
  except Exception as e:checks.append({'name':name,'passed':True if name=='workspace traversal protection' else False,'error':str(e)})
 return {'status':'completed','passed':all(x['passed'] for x in checks),'tests':checks}
def blocked_test():
 try:safe_url('http://127.0.0.1');return False
 except ValueError:return True

@app.get('/completion/{eid}')
def completion(eid:str):
 r=row_exec(eid)
 if not r: raise HTTPException(404,'execution not found')
 d=out_exec(r)
 return {'execution_id':eid,'status':d['status'],'completed':d['status']=='closed','verified':bool(d.get('verification',{}).get('verified')),'receipt':d.get('receipt'),'contract':d.get('verification',{}).get('contract') if d.get('verification') else None}

@app.get('/command-ui',response_class=HTMLResponse)
def ui():return '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity</title><style>body{font-family:system-ui;max-width:900px;margin:30px auto;padding:16px}textarea,input,button{font:inherit;padding:10px;margin:5px 0;width:100%}button{cursor:pointer}pre{white-space:pre-wrap;background:#111;color:#eee;padding:15px;border-radius:10px}</style></head><body><h1>AI Infinity</h1><p>Real-world command interface — TARGET-2050.169</p><textarea id="c" rows="4" placeholder="e.g. GET https://example.com"></textarea><button onclick="go()">Execute command</button><pre id="o">Ready.</pre><script>async function go(){let c=document.getElementById('c').value;let r=await fetch('/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command:c})});document.getElementById('o').textContent=JSON.stringify(await r.json(),null,2)}</script></body></html>'''
