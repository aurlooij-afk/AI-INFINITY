from __future__ import annotations
import base64,hashlib,json,mimetypes,os,re,shutil,sqlite3,subprocess,threading,time,uuid
from pathlib import Path
from typing import Any,Dict
import requests
try: import redis
except Exception: redis=None
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse

VERSION="TARGET-2050.4000"; BUILD="PROFESSIONAL-PRODUCTION-GRAPH"; LOCK=threading.RLock(); PATCHED=False; WORKER_STARTED=False

def s(): import studio_ultimate; return studio_ultimate
def dbpath(): return Path(os.getenv("AI_INFINITY_DB_PATH",str(getattr(s(),"DB_PATH","/tmp/ai-infinity/ai_infinity.db")))).resolve()
def db():
    c=sqlite3.connect(dbpath(),timeout=30,check_same_thread=False); c.row_factory=sqlite3.Row; return c
def now(): return time.time()
def clean(x,n=4000): return str(x or "").replace("\x00"," ").strip()[:n]
def jd(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)
def sha(x): return hashlib.sha256(jd(x).encode()).hexdigest()
def uid(r):
    try:return s()._get_user_id(r)
    except Exception:return "anonymous"
def ensure():
    with LOCK,db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS pg_projects(project_id TEXT PRIMARY KEY,user_id TEXT,brief_sha TEXT,status TEXT,stage TEXT,progress REAL DEFAULT 0,updated_at REAL);
        CREATE TABLE IF NOT EXISTS pg_shots(shot_id TEXT PRIMARY KEY,project_id TEXT,seq INTEGER,purpose TEXT,duration REAL,narration TEXT,visual_prompt TEXT,status TEXT DEFAULT 'planned',winner_candidate_id TEXT,critic_score REAL,revision_count INTEGER DEFAULT 0,metadata TEXT DEFAULT '{}',updated_at REAL);
        CREATE INDEX IF NOT EXISTS pg_shots_i ON pg_shots(project_id,seq);
        CREATE TABLE IF NOT EXISTS pg_candidates(candidate_id TEXT PRIMARY KEY,shot_id TEXT,project_id TEXT,provider TEXT,model TEXT,status TEXT,asset_path TEXT,source_url TEXT,score REAL,critic_json TEXT DEFAULT '{}',error TEXT,created_at REAL,updated_at REAL);
        CREATE INDEX IF NOT EXISTS pg_candidates_i ON pg_candidates(shot_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS pg_reviews(review_id TEXT PRIMARY KEY,project_id TEXT,shot_id TEXT,candidate_id TEXT,reviewer TEXT,score REAL,decision TEXT,report_json TEXT,created_at REAL);
        CREATE TABLE IF NOT EXISTS pg_jobs(job_id TEXT PRIMARY KEY,user_id TEXT,project_id TEXT,shot_id TEXT,kind TEXT,provider TEXT,status TEXT,payload_json TEXT,result_json TEXT,error TEXT,created_at REAL,updated_at REAL);
        CREATE INDEX IF NOT EXISTS pg_jobs_i ON pg_jobs(status,created_at);
        """)
def redis_client():
    u=os.getenv("REDIS_URL","").strip()
    if not u or redis is None:return None
    try:r=redis.Redis.from_url(u,decode_responses=True,socket_timeout=3,socket_connect_timeout=3); r.ping(); return r
    except Exception:return None
def owned(pid,r):
    p=s()._get_project(pid)
    if not p or p.get("user_id")!=uid(r):raise HTTPException(404,"project not found")
    return p
def plan(p):
    raw=p.get("blueprint_json") or "{}"
    try:x=raw if isinstance(raw,dict) else json.loads(raw)
    except Exception:x={}
    for k in ("plan","blueprint","result"):
        if isinstance(x.get(k),dict) and (x[k].get("chapters") or x[k].get("scenes")):return x[k]
    return x if isinstance(x,dict) else {}
def sync(pid):
    ensure(); p=s()._get_project(pid)
    if not p:raise HTTPException(404,"project not found")
    req=p.get("request_json") if isinstance(p.get("request_json"),dict) else {}
    if isinstance(p.get("request_json"),str):
        try:req=json.loads(p["request_json"] or "{}")
        except Exception:req={}
    brief={"title":p.get("title"),"objective":req.get("objective") or req.get("topic"),"tone":req.get("tone"),"audience":req.get("audience"),"style":req.get("visual_style"),"platforms":req.get("platforms")}
    t=now()
    with LOCK,db() as c:
        c.execute("""INSERT INTO pg_projects(project_id,user_id,brief_sha,status,stage,progress,updated_at) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(project_id) DO UPDATE SET brief_sha=excluded.brief_sha,status=excluded.status,stage=excluded.stage,progress=excluded.progress,updated_at=excluded.updated_at""",
        (pid,p.get("user_id"),sha(brief),p.get("status"),p.get("stage") or "intent",float(p.get("progress") or 0),t))
        chapters=plan(p).get("chapters") or plan(p).get("scenes") or []
        for i,ch in enumerate(chapters[:60],1):
            if not isinstance(ch,dict):continue
            sid=f"shot_{pid[:12]}_{i:03d}"
            c.execute("""INSERT INTO pg_shots(shot_id,project_id,seq,purpose,duration,narration,visual_prompt,status,metadata,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(shot_id) DO UPDATE SET purpose=excluded.purpose,duration=excluded.duration,narration=excluded.narration,visual_prompt=excluded.visual_prompt,metadata=excluded.metadata,updated_at=excluded.updated_at""",
            (sid,pid,i,clean(ch.get("purpose") or ch.get("heading"),300),float(ch.get("actual_duration") or ch.get("duration") or 0) or None,clean(ch.get("narration"),10000),clean(ch.get("image_prompt") or ch.get("visual_query") or ch.get("heading"),2500),"planned",jd({k:ch.get(k) for k in ("on_screen","proof_needed","visual_query","camera","lighting","composition") if ch.get(k)!=None}),t))
    return graph(pid)
def graph(pid):
    ensure()
    with LOCK,db() as c:
        p=c.execute("SELECT * FROM pg_projects WHERE project_id=?",(pid,)).fetchone()
        shots=c.execute("SELECT * FROM pg_shots WHERE project_id=? ORDER BY seq",(pid,)).fetchall()
        cand=c.execute("SELECT * FROM pg_candidates WHERE project_id=? ORDER BY created_at DESC",(pid,)).fetchall()
        rev=c.execute("SELECT * FROM pg_reviews WHERE project_id=? ORDER BY created_at DESC",(pid,)).fetchall()
        jobs=c.execute("SELECT * FROM pg_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 100",(pid,)).fetchall()
    cs=[]
    for x in cand:
        d=dict(x)
        try:d["critic"]=json.loads(d.pop("critic_json") or "{}")
        except Exception:d["critic"]={}
        cs.append(d)
    return {"version":VERSION,"build":BUILD,"project":dict(p) if p else None,"shots":[dict(x) for x in shots],"candidates":cs,"reviews":[dict(x) for x in rev],"jobs":[dict(x) for x in jobs],
            "summary":{"shots":len(shots),"candidates":len(cand),"accepted":sum(1 for x in cand if x["status"]=="accepted"),"queued":sum(1 for x in jobs if x["status"]=="queued")},"truthful":True}
def providers():
    return {"version":VERSION,"providers":[
    {"id":"runway","video":True,"configured":bool(os.getenv("RUNWAYML_API_SECRET","").strip()),"model":os.getenv("AI_INFINITY_RUNWAY_MODEL","gen4.5")},
    {"id":"openai_video","video":True,"configured":bool(os.getenv("OPENAI_API_KEY","").strip()),"model":os.getenv("AI_INFINITY_OPENAI_VIDEO_MODEL","sora-2-pro")},
    {"id":"elevenlabs","voice":True,"sfx":True,"configured":bool(os.getenv("ELEVENLABS_API_KEY","").strip())},
    {"id":"tavily","research":True,"configured":bool(os.getenv("TAVILY_API_KEY","").strip())},
    {"id":"exa","research":True,"configured":bool(os.getenv("EXA_API_KEY","").strip())},
    {"id":"brave","research":True,"configured":bool(os.getenv("BRAVE_SEARCH_API_KEY","").strip())},
    {"id":"remotion","render":True,"configured":bool(os.getenv("REMOTION_RENDER_URL","").strip())},
    {"id":"r2","storage":True,"configured":bool(os.getenv("CLOUDFLARE_R2_BUCKET","").strip() and os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID","").strip())}],
    "queue":{"backend":"redis" if redis_client() else "sqlite-fallback","redis_configured":bool(os.getenv("REDIS_URL","").strip())},"truthful":True}
def runway_create(d):
    k=os.getenv("RUNWAYML_API_SECRET","").strip()
    if not k:raise RuntimeError("RUNWAYML_API_SECRET not configured")
    body={"model":d.get("model") or os.getenv("AI_INFINITY_RUNWAY_MODEL","gen4.5"),"promptText":clean(d.get("prompt"),5000),"ratio":d.get("ratio") or "1280:768","duration":max(2,min(10,int(d.get("duration") or 5)))}
    if d.get("prompt_image"):body["promptImage"]=d["prompt_image"]
    r=requests.post("https://api.dev.runwayml.com/v1/image_to_video",headers={"Authorization":f"Bearer {k}","X-Runway-Version":"2024-11-06","Content-Type":"application/json"},json=body,timeout=60); r.raise_for_status(); return r.json()
def runway_poll(tid,limit=900):
    k=os.getenv("RUNWAYML_API_SECRET",""); end=now()+max(30,min(limit,1800)); last={}
    while now()<end:
        r=requests.get(f"https://api.dev.runwayml.com/v1/tasks/{tid}",headers={"Authorization":f"Bearer {k}","X-Runway-Version":"2024-11-06"},timeout=30); r.raise_for_status(); last=r.json(); st=str(last.get("status") or "").upper()
        if st in {"SUCCEEDED","FAILED","CANCELED"}:return last
        time.sleep(5)
    return {"status":"TIMEOUT","last":last}
def download(url,out):
    with requests.get(url,stream=True,timeout=180,headers={"User-Agent":"AI-Infinity/ProductionGraph"}) as r:
        r.raise_for_status()
        with out.open("wb") as f:
            for ch in r.iter_content(1024*1024):
                if ch:f.write(ch)
    if out.stat().st_size<1000:raise RuntimeError("empty remote artifact")
    return out
def enqueue(user,pid,sid,kind,provider,payload):
    ensure();jid="pgjob_"+uuid.uuid4().hex;payload=dict(payload);cid=None;t=now()
    if kind=="video" and sid:
        cid="cand_"+uuid.uuid4().hex;payload["candidate_id"]=cid
        with LOCK,db() as c:c.execute("INSERT INTO pg_candidates(candidate_id,shot_id,project_id,provider,model,status,critic_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(cid,sid,pid,provider,payload.get("model") or "","queued","{}",t,t))
    with LOCK,db() as c:c.execute("INSERT INTO pg_jobs(job_id,user_id,project_id,shot_id,kind,provider,status,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(jid,user,pid,sid,kind,provider,"queued",jd(payload),t,t))
    r=redis_client(); backend="sqlite-fallback"
    if r:
        try:r.rpush("aii:production:queue",jid);backend="redis"
        except Exception:pass
    return {"job_id":jid,"candidate_id":cid,"status":"queued","queue_backend":backend,"truthful":True}
def process(job):
    jid=job["job_id"]
    try:d=json.loads(job.get("payload_json") or "{}")
    except Exception:d={}
    with LOCK,db() as c:c.execute("UPDATE pg_jobs SET status='running',updated_at=? WHERE job_id=?",(now(),jid))
    try:
        p=str(job.get("provider") or "").lower()
        if job["kind"]=="video" and p=="runway":
            q=runway_create(d);tid=q.get("id")
            if not tid:raise RuntimeError("Runway returned no task id")
            done=runway_poll(tid,int(d.get("timeout") or 900))
            if str(done.get("status") or "").upper()!="SUCCEEDED":raise RuntimeError("Runway task failed: "+jd(done)[:1200])
            outs=done.get("output") or [];url=outs[0] if isinstance(outs,list) and outs else done.get("output_url")
            if not url:raise RuntimeError("Runway succeeded without output URL")
            root=Path(os.getenv("AI_INFINITY_DATA_DIR","/tmp/ai-infinity"))/"production-jobs"/jid;root.mkdir(parents=True,exist_ok=True);fp=download(str(url),root/"candidate.mp4");cid=str(d.get("candidate_id") or "")
            if cid:
                try:s()._save_asset(job["project_id"],"candidate",fp,"video/mp4",{"provider":"runway","candidate_id":cid,"task_id":tid})
                except Exception:pass
                with LOCK,db() as c:c.execute("UPDATE pg_candidates SET status='ready',asset_path=?,source_url=?,model=?,updated_at=? WHERE candidate_id=?",(str(fp),str(url),d.get("model") or "gen4.5",now(),cid))
            result={"status":"succeeded","task_id":tid,"output_url":url,"local_path":str(fp)}
        else:raise RuntimeError("unsupported production-graph provider/job")
        with LOCK,db() as c:c.execute("UPDATE pg_jobs SET status='completed',result_json=?,updated_at=? WHERE job_id=?",(jd(result),now(),jid))
    except Exception as e:
        with LOCK,db() as c:c.execute("UPDATE pg_jobs SET status='failed',error=?,updated_at=? WHERE job_id=?",(str(e)[:3000],now(),jid))
def worker():
    global WORKER_STARTED
    WORKER_STARTED=True;r=redis_client()
    while True:
        try:
            row=None
            if r:
                item=r.blpop("aii:production:queue",timeout=2)
                if item:
                    with LOCK,db() as c:row=c.execute("SELECT * FROM pg_jobs WHERE job_id=?",(item[1],)).fetchone()
            if not row:
                with LOCK,db() as c:row=c.execute("SELECT * FROM pg_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:process(dict(row));continue
        except Exception:pass
        time.sleep(1)
def tech(path):
    if not path.is_file():return {"score":0,"decision":"reject"}
    score=25;checks={"file_present":True,"size_ok":path.stat().st_size>1000};ff=shutil.which("ffprobe")
    if ff and path.suffix.lower() in {".mp4",".mov",".webm",".m4v",".mkv"}:
        try:
            q=subprocess.run([ff,"-v","error","-show_format","-show_streams","-of","json",str(path)],capture_output=True,text=True,timeout=45);d=json.loads(q.stdout or "{}");ss=d.get("streams") or [];v=next((x for x in ss if x.get("codec_type")=="video"),{});a=next((x for x in ss if x.get("codec_type")=="audio"),{});w,h=int(v.get("width") or 0),int(v.get("height") or 0);dur=float((d.get("format") or {}).get("duration") or 0);checks.update({"video":bool(v),"audio":bool(a),"duration":dur>0,"resolution":max(w,h)>=720});score+=15*bool(v)+15*bool(a)+15*(dur>0)+20*(max(w,h)>=720)
        except Exception:checks["probe"]=False
    return {"score":min(100,score),"decision":"accept" if score>=70 else "review","checks":checks}
def review(cid,user):
    ensure()
    with LOCK,db() as c:
        r=c.execute("SELECT * FROM pg_candidates WHERE candidate_id=?",(cid,)).fetchone()
        if not r:raise HTTPException(404,"candidate not found")
        p=c.execute("SELECT * FROM pg_projects WHERE project_id=? AND user_id=?",(r["project_id"],user)).fetchone()
        sh=c.execute("SELECT * FROM pg_shots WHERE shot_id=?",(r["shot_id"],)).fetchone()
    if not p:raise HTTPException(404,"project not found")
    report={"technical":tech(Path(r["asset_path"] or "")),"truthful":True};score=float(report["technical"]["score"]);decision="accept" if score>=80 else ("review" if score>=60 else "reject");rid="rev_"+uuid.uuid4().hex
    with LOCK,db() as c:
        c.execute("INSERT INTO pg_reviews(review_id,project_id,shot_id,candidate_id,reviewer,score,decision,report_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(rid,p["project_id"],r["shot_id"],cid,"technical-qc",score,decision,jd(report),now()))
        c.execute("UPDATE pg_candidates SET score=?,critic_json=?,status=?,updated_at=? WHERE candidate_id=?",(score,jd(report),"accepted" if decision=="accept" else "reviewed",now(),cid))
        if decision=="accept":c.execute("UPDATE pg_shots SET winner_candidate_id=?,critic_score=?,status='approved',updated_at=? WHERE shot_id=?",(cid,score,now(),r["shot_id"]))
    return {"review_id":rid,"score":score,"decision":decision,"report":report,"truthful":True}

def install():
    global PATCHED
    if PATCHED:return
    ensure();m=s()
    if not hasattr(m,"_pg_stage_original"):
        m._pg_stage_original=m._stage
        def stage(pid,name,progress,**extra):
            m._pg_stage_original(pid,name,progress,**extra)
            try:
                p=m._get_project(pid)
                with LOCK,db() as c:c.execute("""INSERT INTO pg_projects(project_id,user_id,brief_sha,status,stage,progress,updated_at) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET status=excluded.status,stage=excluded.stage,progress=excluded.progress,updated_at=excluded.updated_at""",(pid,(p or {}).get("user_id"),sha((p or {}).get("request_json")),str((p or {}).get("status") or "running"),name,float(progress),now()))
            except Exception:pass
        m._stage=stage
    PATCHED=True
    global WORKER_STARTED
    if not WORKER_STARTED and os.getenv("AI_INFINITY_PRODUCTION_GRAPH_WORKER","1").lower() in {"1","true","yes","on"}:threading.Thread(target=worker,name="ai-infinity-production-graph",daemon=True).start()

def register(app):
    install()
    @app.get("/infinity/production/health")
    def h():return {"status":"healthy","version":VERSION,"build":BUILD,"worker_started":WORKER_STARTED,"providers":providers(),"truthful":True}
    @app.get("/infinity/production/providers")
    def ps():return providers()
    @app.get("/infinity/production/project/{pid}")
    def pg(pid:str,r:Request):owned(pid,r);return sync(pid)
    @app.post("/infinity/production/create")
    async def pc(r:Request):
        d=await r.json()
        if not isinstance(d,dict):raise HTTPException(422,"JSON object required")
        d=dict(d);d.setdefault("content_type","video");d.setdefault("quality_preset","professional");d.setdefault("idempotency_key","pg-"+uuid.uuid4().hex)
        res=s().enqueue(d,uid(r),None);pid=str(res.get("project_id") or "")
        if not pid:raise HTTPException(500,"Creator Studio did not return project id")
        return {"status":"accepted","project_id":pid,"graph":sync(pid),"truthful":True}
    @app.post("/infinity/production/project/{pid}/generate-video-batch")
    async def gb(pid:str,r:Request):
        owned(pid,r);d=await r.json();sid=clean(d.get("shot_id"),120);n=max(1,min(4,int(d.get("count") or 3)))
        if not sid:raise HTTPException(422,"shot_id required")
        if not os.getenv("RUNWAYML_API_SECRET","").strip():return {"status":"not_configured","required_secret":"RUNWAYML_API_SECRET","truthful":True}
        styles=["cinematic","documentary","editorial","commercial"];jobs=[]
        for i in range(n):
            x=dict(d);x["prompt"]=clean(d.get("prompt") or "Professional cinematic shot",3800)+f"; variation {i+1}: {styles[i%4]}.";jobs.append(enqueue(uid(r),pid,sid,"video","runway",x))
        return {"status":"accepted","jobs":jobs,"truthful":True}
    @app.post("/infinity/production/project/{pid}/review/{cid}")
    def rv(pid:str,cid:str,r:Request):owned(pid,r);return review(cid,uid(r))
    @app.get("/production",response_class=HTMLResponse)
    def ui():return HTML

HTML=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity Production</title><style>body{margin:0;background:#070a0e;color:#eef4f8;font:15px system-ui}main{max-width:1100px;margin:auto;padding:26px}.c{background:#0e141b;border:1px solid #27343f;border-radius:18px;padding:18px;margin:12px 0}.btn{background:#d8ff91;border:0;border-radius:12px;padding:12px 16px;font-weight:800}textarea{width:100%;box-sizing:border-box;background:#080c11;color:white;border:1px solid #2d3945;border-radius:12px;padding:12px}.pill{display:inline-block;border:1px solid #2d3945;border-radius:999px;padding:5px 8px;margin:3px}.muted{color:#93a7b6}.shot{border:1px solid #27343f;border-radius:13px;padding:10px;margin:8px 0}</style></head><body><main><h1>AI Infinity</h1><p class="muted">Professional Production Graph</p><div class="c"><textarea id="b" rows="5" placeholder="Describe the content you want produced..."></textarea><p><button class="btn" onclick="go()">Start production</button> <a class="pill" href="/">Main AI Infinity</a></p><div id="m" class="muted"></div></div><div class="c"><b>Providers</b><div id="p" class="muted">Loading...</div></div><div class="c"><b>Live graph</b><div id="g" class="muted">No project.</div></div></main><script>let pid=null;async function A(u,o){let r=await fetch(u,o),j=await r.json();if(!r.ok)throw Error(j.detail||JSON.stringify(j));return j}async function go(){try{let b=document.getElementById('b').value.trim();if(!b)return;let j=await A('/infinity/production/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({objective:b,title:b.slice(0,120),duration:60,format:'long',content_type:'video',quality_preset:'professional'})});pid=j.project_id;document.getElementById('m').textContent='Accepted real project '+pid;poll()}catch(e){document.getElementById('m').textContent=e.message}}async function poll(){if(!pid)return;try{let j=await A('/infinity/production/project/'+pid),p=j.project||{};document.getElementById('g').innerHTML='<b>'+Number(p.progress||0).toFixed(0)+'%</b> · '+p.stage+' · '+p.status+'<div class="muted">shots '+j.summary.shots+' · candidates '+j.summary.candidates+' · accepted '+j.summary.accepted+'</div>'+(j.shots||[]).map(x=>'<div class="shot"><b>Shot '+String(x.seq).padStart(2,'0')+' · '+(x.purpose||'Scene')+'</b><div class="muted">'+x.status+' · '+Number(x.duration||0).toFixed(1)+'s · critic '+(x.critic_score==null?'—':Number(x.critic_score).toFixed(0))+'</div></div>').join('')}catch(e){}setTimeout(poll,3000)}(async()=>{try{let j=await A('/infinity/production/providers');document.getElementById('p').innerHTML=j.providers.map(x=>'<span class="pill">'+x.id+' · '+(x.configured?'configured':'not configured')+'</span>').join('')}catch(e){}})()</script></body></html>'''
