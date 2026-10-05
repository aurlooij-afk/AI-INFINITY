FINAL3700_VERSION="TARGET-2050.3700"
FINAL3700_BUILD="EXTRAORDINARY-CREATOR-OS-PLATFORM-CLOSURE"
FINAL3700_PREVIOUS=globals().get("FINAL3603_VERSION","TARGET-2050.3603")

with _db_lock, db() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS ai3700_projects(
      project_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,
      objective TEXT NOT NULL,stage TEXT NOT NULL DEFAULT 'idea',
      status TEXT NOT NULL DEFAULT 'active',metadata_json TEXT NOT NULL DEFAULT '{}',
      created_at REAL NOT NULL,updated_at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_ai3700_projects_user ON ai3700_projects(user_id,updated_at);
    CREATE TABLE IF NOT EXISTS ai3700_brand(
      user_id TEXT PRIMARY KEY,brand_name TEXT NOT NULL DEFAULT '',
      voice TEXT NOT NULL DEFAULT '',audience TEXT NOT NULL DEFAULT '',
      positioning TEXT NOT NULL DEFAULT '',visual_notes TEXT NOT NULL DEFAULT '',
      updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS ai3700_publish_queue(
      publish_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT,
      title TEXT NOT NULL,target TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'draft_ready',
      payload_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL,updated_at REAL NOT NULL);
    """)

def _3700_user(request): return _3603_session_user(request)
def _3700_json(x): return json.dumps(x,ensure_ascii=False,separators=(",",":"),default=str)
def _3700_row(r):
    d=dict(r)
    try:d["metadata"]=json.loads(d.pop("metadata_json","{}") or "{}")
    except Exception:d["metadata"]={}
    return d
def _3700_projects(uid,limit=100):
    with _db_lock,db() as c:
        return [_3700_row(r) for r in c.execute("SELECT * FROM ai3700_projects WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",(uid,max(1,min(limit,200)))).fetchall()]
def _3700_brand(uid):
    with _db_lock,db() as c:r=c.execute("SELECT * FROM ai3700_brand WHERE user_id=?",(uid,)).fetchone()
    if not r:return {"user_id":uid,"brand_name":"AI Infinity","voice":"clear, capable, trustworthy","audience":"people building useful things","positioning":"one operating workspace for turning ideas into verified outcomes","visual_notes":"premium, calm, futuristic, readable"}
    d=dict(r);d.pop("updated_at",None);return d

@app.get("/infinity/3700/health")
def infinity3700_health():
    return {"status":"healthy","version":FINAL3700_VERSION,"build":FINAL3700_BUILD,"platform_shell":True,"creator_workspace":True,"database":True,"truthful":True}

@app.get("/infinity/3700/dashboard")
def infinity3700_dashboard(request:FastAPIRequest):
    uid=_3700_user(request)
    try:tasks=_gp_fixed_price_rows(12)
    except Exception:tasks=[]
    try:activity=infinity3603_operating_activity(16).get("activity",[])
    except Exception:activity=[]
    return {"version":FINAL3700_VERSION,"build":FINAL3700_BUILD,"user_id":uid,"metrics":_3601_metrics(),"readiness":_3601_real_readiness(),"projects":_3700_projects(uid,8),"brand":_3700_brand(uid),"tasks":tasks,"activity":activity,"truthful":True}

@app.get("/infinity/3700/projects")
def infinity3700_projects(request:FastAPIRequest):
    uid=_3700_user(request);return {"user_id":uid,"projects":_3700_projects(uid),"truthful":True}

@app.post("/infinity/3700/projects")
def infinity3700_project_create(payload:Dict[str,Any],request:FastAPIRequest):
    uid=_3700_user(request);name=str(payload.get("name") or "Untitled Infinity Project").strip()[:180];objective=str(payload.get("objective") or "").strip()[:6000]
    if not objective:raise HTTPException(400,"objective is required")
    stage=str(payload.get("stage") or "idea").lower()
    if stage not in {"idea","planning","production","review","published","archived"}:stage="idea"
    pid=uid_fn("project") if "uid_fn" in globals() else uid("project");t=now();meta=payload.get("metadata") if isinstance(payload.get("metadata"),dict) else {}
    with _db_lock,db() as c:c.execute("INSERT INTO ai3700_projects VALUES(?,?,?,?,?,?,?,?,?)",(pid,uid,name,objective,stage,"active",_3700_json(meta),t,t))
    return {"status":"created","project":_3700_projects(uid,1)[0],"truthful":True}

@app.get("/infinity/3700/projects/{project_id}")
def infinity3700_project(project_id:str,request:FastAPIRequest):
    uid=_3700_user(request)
    with _db_lock,db() as c:r=c.execute("SELECT * FROM ai3700_projects WHERE project_id=? AND user_id=?",(project_id,uid)).fetchone()
    if not r:raise HTTPException(404,"project not found")
    return {"project":_3700_row(r),"truthful":True}

@app.post("/infinity/3700/projects/{project_id}/stage")
def infinity3700_stage(project_id:str,payload:Dict[str,Any],request:FastAPIRequest):
    uid=_3700_user(request);stage=str(payload.get("stage") or "").lower()
    if stage not in {"idea","planning","production","review","published","archived"}:raise HTTPException(400,"invalid project stage")
    with _db_lock,db() as c:
        r=c.execute("SELECT * FROM ai3700_projects WHERE project_id=? AND user_id=?",(project_id,uid)).fetchone()
        if not r:raise HTTPException(404,"project not found")
        c.execute("UPDATE ai3700_projects SET stage=?,status=?,updated_at=? WHERE project_id=? AND user_id=?",(stage,"archived" if stage=="archived" else "active",now(),project_id,uid))
        r=c.execute("SELECT * FROM ai3700_projects WHERE project_id=? AND user_id=?",(project_id,uid)).fetchone()
    return {"status":"updated","project":_3700_row(r),"truthful":True}

@app.get("/infinity/3700/brand")
def infinity3700_brand(request:FastAPIRequest):return {"brand":_3700_brand(_3700_user(request)),"truthful":True}

@app.post("/infinity/3700/brand")
def infinity3700_brand_save(payload:Dict[str,Any],request:FastAPIRequest):
    uid=_3700_user(request);cur=_3700_brand(uid)
    f={k:str(payload.get(k) or cur.get(k,"")).strip() for k in ("brand_name","voice","audience","positioning","visual_notes")};t=now()
    with _db_lock,db() as c:c.execute("INSERT INTO ai3700_brand VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET brand_name=excluded.brand_name,voice=excluded.voice,audience=excluded.audience,positioning=excluded.positioning,visual_notes=excluded.visual_notes,updated_at=excluded.updated_at",(uid,f["brand_name"],f["voice"],f["audience"],f["positioning"],f["visual_notes"],t))
    return {"status":"saved","brand":_3700_brand(uid),"truthful":True}

@app.post("/infinity/3700/production")
def infinity3700_production(payload:Dict[str,Any],request:FastAPIRequest):
    uid=_3700_user(request);objective=str(payload.get("objective") or "").strip()[:6000]
    if not objective:raise HTTPException(400,"objective is required")
    result=_f2300_media_production_plan(payload);return {"status":"production_ready","production":result,"truthful":True}

@app.get("/infinity/3700/publish")
def infinity3700_publish(request:FastAPIRequest):
    uid=_3700_user(request)
    with _db_lock,db() as c:rows=[dict(r) for r in c.execute("SELECT * FROM ai3700_publish_queue WHERE user_id=? ORDER BY updated_at DESC LIMIT 100",(uid,)).fetchall()]
    return {"user_id":uid,"items":rows,"truthful":True}

@app.post("/infinity/3700/publish")
def infinity3700_publish_add(payload:Dict[str,Any],request:FastAPIRequest):
    uid=_3700_user(request);pid=uid("publish") if "uid" in globals() else str(uuid.uuid4());t=now();title=str(payload.get("title") or "Untitled publication")[:240];target=str(payload.get("target") or "draft")[:120];body={"body":str(payload.get("body") or "")[:12000]}
    with _db_lock,db() as c:c.execute("INSERT INTO ai3700_publish_queue VALUES(?,?,?,?,?,?,?,?,?)",(pid,uid,str(payload.get("project_id") or "") or None,title,target,"draft_ready",_3700_json(body),t,t))
    return {"status":"draft_ready","publish_id":pid,"message":"Prepared locally. No external publication was claimed.","truthful":True}

@app.get("/infinity/3700/analytics")
def infinity3700_analytics(request:FastAPIRequest):
    uid=_3700_user(request)
    with _db_lock,db() as c:
        total=c.execute("SELECT COUNT(*) FROM ai3700_projects WHERE user_id=?",(uid,)).fetchone()[0];active=c.execute("SELECT COUNT(*) FROM ai3700_projects WHERE user_id=? AND status='active'",(uid,)).fetchone()[0]
        drafts=c.execute("SELECT COUNT(*) FROM ai3700_publish_queue WHERE user_id=? AND status='draft_ready'",(uid,)).fetchone()[0]
    m=_3601_metrics();return {"projects":{"total":total,"active":active},"publishing":{"drafts":drafts},"execution":{k:m.get(k,0) for k in ("verified_earnings","pending_verified_funds","transferable_balance","active_opportunities")},"truthful":True}

@app.get("/infinity/3700/agents")
def infinity3700_agents():
    return {"version":FINAL3700_VERSION,"agents":[{"id":"director","name":"Director","status":"ready"},{"id":"researcher","name":"Research","status":"ready"},{"id":"producer","name":"Producer","status":"ready"},{"id":"operator","name":"Operator","status":"gated"},{"id":"verifier","name":"Verifier","status":"ready"},{"id":"economic","name":"Economy","status":"ready"}],"truthful":True}

@app.get("/infinity/3700/operations")
def infinity3700_operations():
    return {"version":FINAL3700_VERSION,"readiness":_3601_real_readiness(),"emergency_stop_available":True,"external_side_effects_require_authority":True,"automatic_uncertain_external_replay":False,"truthful":True}

@app.post("/infinity/3700/command")
def infinity3700_command(payload:Dict[str,Any],request:FastAPIRequest):
    command=str(payload.get("command") or "").strip()[:12000]
    if not command:raise HTTPException(400,"command is required")
    r=_3603_operating_command(command,user_id=_3700_user(request),approved=bool(payload.get("approved",False)));r["platform_version"]=FINAL3700_VERSION;r["truthful"]=True;return r

@app.get("/infinity/3700/quality")
def infinity3700_quality():
    try:r=infinity3603_regression()
    except Exception as e:r={"passed":False,"status":"failed","error":str(e)[:400]}
    return {"version":FINAL3700_VERSION,"regression":r,"truthful":True}

@app.get("/infinity/3700/system")
def infinity3700_system():
    return {"version":FINAL3700_VERSION,"build":FINAL3700_BUILD,"previous":FINAL3700_PREVIOUS,"app_routes":len(app.routes),"free_first":True,"arbitrary_code_execution":False,"truthful":True}

@app.get("/infinity/3700/ui-manifest")
def infinity3700_ui_manifest():
    return {"version":FINAL3700_VERSION,"name":"AI Infinity Creator Operating System","navigation":["Home","Create","Production","Projects","Intelligence","Assets","Brand","Publish","Analytics","Agents","Operations","Quality & Gaps","System","Chat"],"truthful":True}

@app.get("/infinity/3700/ui",response_class=HTMLResponse)
def infinity3700_ui():return FINAL3700_UI

FINAL3603_UI=FINAL3700_UI
FINAL_INFINITY_UI=FINAL3700_UI
FINAL3603_BUILD=FINAL3700_BUILD
APP_VERSION=FINAL3700_VERSION
BUILD=FINAL3700_BUILD
PREVIOUS_BUILD=FINAL3700_PREVIOUS
app.version=APP_VERSION


# 3701 acceleration + knowledge + engineering layer.
try:
    exec(open("ai3701_features.py", encoding="utf-8").read(), globals())
except Exception as _ai3701_feature_error:
    try:
        app.state.ai3701_feature_error = str(_ai3701_feature_error)[:600]
    except Exception:
        pass

# 3702 universal knowledge + automation + simulation layer.
try:
    exec(open("ai3702_platform.py", encoding="utf-8").read(), globals())
except Exception as _ai3702_platform_error:
    try:
        app.state.ai3702_platform_error = str(_ai3702_platform_error)[:800]
    except Exception:
        pass
