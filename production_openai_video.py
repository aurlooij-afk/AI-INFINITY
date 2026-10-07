from __future__ import annotations

"""AI Infinity TARGET-2050.4002 — OpenAI Sora Video Adapter."""

import json, os, sqlite3, threading, time, uuid
from pathlib import Path
import requests
from fastapi import HTTPException, Request

VERSION="TARGET-2050.4002"
BUILD="OPENAI-SORA-ASYNC-VIDEO-ADAPTER"
LOCK=threading.RLock()
WORKER_STARTED=False

def pg():
    import production_graph
    return production_graph

def studio():
    import studio_ultimate
    return studio_ultimate

def db():
    c=sqlite3.connect(pg().dbpath(),timeout=30,check_same_thread=False);c.row_factory=sqlite3.Row;return c

def now():return time.time()

def uid(r):
    try:return studio()._get_user_id(r)
    except Exception:return "anonymous"

def own(pid,r):
    p=studio()._get_project(pid)
    if not p or p.get("user_id")!=uid(r):raise HTTPException(404,"project not found")
    return p

def ensure():
    with LOCK,db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS sora_jobs(
          job_id TEXT PRIMARY KEY,user_id TEXT,project_id TEXT,shot_id TEXT,candidate_id TEXT,
          remote_id TEXT,status TEXT NOT NULL,payload_json TEXT NOT NULL,result_json TEXT,
          error TEXT,created_at REAL,updated_at REAL
        );
        CREATE INDEX IF NOT EXISTS sora_jobs_status ON sora_jobs(status,created_at);
        """)

def status():
    return {
      "id":"openai_video",
      "configured":bool(os.getenv("OPENAI_API_KEY","").strip()),
      "model":os.getenv("AI_INFINITY_OPENAI_VIDEO_MODEL","sora-2-pro"),
      "endpoint":"POST /v1/videos",
      "async":True,
      "truthful":True
    }

def create_remote(payload):
    key=os.getenv("OPENAI_API_KEY","").strip()
    if not key:raise RuntimeError("OPENAI_API_KEY not configured")
    seconds=str(payload.get("seconds") or os.getenv("AI_INFINITY_OPENAI_VIDEO_SECONDS","8"))
    if seconds not in {"4","8","12"}:seconds="8"
    size=str(payload.get("size") or os.getenv("AI_INFINITY_OPENAI_VIDEO_SIZE","1280x720"))
    if size not in {"720x1280","1280x720","1024x1792","1792x1024"}:size="1280x720"
    body={"model":payload.get("model") or os.getenv("AI_INFINITY_OPENAI_VIDEO_MODEL","sora-2-pro"),
          "prompt":str(payload.get("prompt") or "")[:32000],"seconds":seconds,"size":size}
    ref=payload.get("input_reference")
    if isinstance(ref,dict) and (ref.get("image_url") or ref.get("file_id")):body["input_reference"]={k:v for k,v in ref.items() if k in {"image_url","file_id"}}
    r=requests.post("https://api.openai.com/v1/videos",headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json=body,timeout=90)
    r.raise_for_status();return r.json()

def retrieve(remote_id):
    key=os.getenv("OPENAI_API_KEY","").strip()
    r=requests.get(f"https://api.openai.com/v1/videos/{remote_id}",headers={"Authorization":f"Bearer {key}"},timeout=45);r.raise_for_status();return r.json()

def download(remote_id,out):
    key=os.getenv("OPENAI_API_KEY","").strip()
    with requests.get(f"https://api.openai.com/v1/videos/{remote_id}/content",headers={"Authorization":f"Bearer {key}"},params={"variant":"video"},stream=True,timeout=180) as r:
        r.raise_for_status()
        with out.open("wb") as f:
            for chunk in r.iter_content(1024*1024):
                if chunk:f.write(chunk)
    if out.stat().st_size<1000:raise RuntimeError("empty Sora video content")
    return out

def process(job):
    jid=job["job_id"]
    try:
        with LOCK,db() as c:c.execute("UPDATE sora_jobs SET status='creating',updated_at=? WHERE job_id=?",(now(),jid))
        payload=json.loads(job["payload_json"] or "{}")
        remote_id=job.get("remote_id")
        if not remote_id:
            created=create_remote(payload);remote_id=str(created.get("id") or "")
            if not remote_id:raise RuntimeError("Sora returned no video id")
            with LOCK,db() as c:c.execute("UPDATE sora_jobs SET remote_id=?,status='in_progress',updated_at=? WHERE job_id=?",(remote_id,now(),jid))
        deadline=now()+max(120,min(3600,int(payload.get("timeout") or 1800)));data={}
        while now()<deadline:
            data=retrieve(remote_id);st=str(data.get("status") or "").lower()
            if st=="completed":break
            if st=="failed":raise RuntimeError(json.dumps(data.get("error") or data)[:2000])
            time.sleep(max(5,min(20,int(payload.get("poll_seconds") or 8))))
        else:raise RuntimeError("Sora job polling timeout")
        root=Path(os.getenv("AI_INFINITY_DATA_DIR","/tmp/ai-infinity"))/"production-jobs"/jid;root.mkdir(parents=True,exist_ok=True)
        fp=download(remote_id,root/"candidate.mp4")
        cid=str(job.get("candidate_id") or payload.get("candidate_id") or "")
        if cid:
            try:studio()._save_asset(job["project_id"],"candidate",fp,"video/mp4",{"provider":"openai_video","remote_id":remote_id,"candidate_id":cid})
            except Exception:pass
            with LOCK,db() as c:c.execute("UPDATE pg_candidates SET status='ready',asset_path=?,source_url=?,model=?,updated_at=? WHERE candidate_id=?",(str(fp),f"/v1/videos/{remote_id}/content",payload.get("model") or os.getenv("AI_INFINITY_OPENAI_VIDEO_MODEL","sora-2-pro"),now(),cid))
        result={"status":"completed","remote_id":remote_id,"local_path":str(fp),"metadata":data}
        with LOCK,db() as c:c.execute("UPDATE sora_jobs SET status='completed',result_json=?,updated_at=? WHERE job_id=?",(json.dumps(result,ensure_ascii=False),now(),jid))
    except Exception as e:
        with LOCK,db() as c:c.execute("UPDATE sora_jobs SET status='failed',error=?,updated_at=? WHERE job_id=?",(str(e)[:3000],now(),jid))

def enqueue(user,pid,sid,payload):
    ensure()
    jid="sorajob_"+uuid.uuid4().hex
    cid="cand_"+uuid.uuid4().hex
    t=now()
    d=dict(payload);d["candidate_id"]=cid
    with LOCK,db() as c:
        c.execute("INSERT INTO sora_jobs(job_id,user_id,project_id,shot_id,candidate_id,remote_id,status,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(jid,user,pid,sid,cid,None,"queued",json.dumps(d,ensure_ascii=False),t,t))
        c.execute("INSERT INTO pg_candidates(candidate_id,shot_id,project_id,provider,model,status,critic_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(cid,sid,pid,"openai_video",d.get("model") or os.getenv("AI_INFINITY_OPENAI_VIDEO_MODEL","sora-2-pro"),"queued","{}",t,t))
    r=pg().redis_client();backend="sqlite-fallback"
    if r:
        try:r.rpush("aii:openai_video:queue",jid);backend="redis"
        except Exception:pass
    return {"job_id":jid,"candidate_id":cid,"status":"queued","queue_backend":backend,"truthful":True}

def worker():
    global WORKER_STARTED
    WORKER_STARTED=True;r=pg().redis_client()
    while True:
        try:
            row=None
            if r:
                item=r.blpop("aii:openai_video:queue",timeout=2)
                if item:
                    with LOCK,db() as c:row=c.execute("SELECT * FROM sora_jobs WHERE job_id=?",(item[1],)).fetchone()
            if not row:
                with LOCK,db() as c:row=c.execute("SELECT s.*,c.status as candidate_status FROM sora_jobs s LEFT JOIN pg_candidates c ON c.candidate_id=s.candidate_id WHERE s.status='queued' ORDER BY s.created_at LIMIT 1").fetchone()
            if row:process(dict(row));continue
        except Exception:pass
        time.sleep(1)

def install():
    ensure()
    global WORKER_STARTED
    if not WORKER_STARTED and os.getenv("AI_INFINITY_PRODUCTION_GRAPH_WORKER","1").lower() in {"1","true","yes","on"}:
        threading.Thread(target=worker,name="ai-infinity-sora-worker",daemon=True).start()

def register(app):
    install()
    @app.get("/infinity/production/providers/openai-video")
    def provider():return status()
    @app.post("/infinity/production/project/{pid}/generate-openai-video")
    async def generate(pid:str,r:Request):
        own(pid,r);d=await r.json()
        sid=str(d.get("shot_id") or "").strip()
        if not sid:raise HTTPException(422,"shot_id required")
        prompt=str(d.get("prompt") or "").strip()
        if not prompt:raise HTTPException(422,"prompt required")
        if not os.getenv("OPENAI_API_KEY","").strip():return {"status":"not_configured","required_secret":"OPENAI_API_KEY","truthful":True}
        return enqueue(uid(r),pid,sid,{"prompt":prompt,"model":d.get("model"),"seconds":d.get("seconds"),"size":d.get("size"),"input_reference":d.get("input_reference"),"timeout":d.get("timeout")})
    @app.get("/infinity/production/project/{pid}/sora-job/{jid}")
    def job(pid:str,jid:str,r:Request):
        own(pid,r);ensure()
        with LOCK,db() as c:row=c.execute("SELECT job_id,project_id,shot_id,candidate_id,remote_id,status,result_json,error,created_at,updated_at FROM sora_jobs WHERE job_id=? AND project_id=?",(jid,pid)).fetchone()
        if not row:raise HTTPException(404,"Sora job not found")
        d=dict(row)
        try:d["result"]=json.loads(d.pop("result_json") or "{}")
        except Exception:d["result"]={}
        return {"version":VERSION,"build":BUILD,"job":d,"truthful":True}
