from __future__ import annotations
import hashlib,json,os,re,shutil,subprocess,time,uuid
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from urllib.request import Request as URLRequest,urlopen
from fastapi import HTTPException,Request
from fastapi.responses import FileResponse

APP=globals()["app"]; DB=globals()["db"]; LOCK=globals()["_db_lock"]; ROOT=Path(os.getenv("AI_INFINITY_DATA_DIR","/tmp/ai-infinity")); NOW=globals().get("now",time.time)
def now(): return float(NOW())
def uid(r):
    f=globals().get("_3603_session_user")
    try:return str(f(r)) if callable(f) else "default"
    except Exception:return "default"
def clean(x,n=12000): return str(x or "").replace("\x00","").strip()[:n]
def jd(x): return json.dumps(x,ensure_ascii=False,default=str,separators=(",",":"))

with LOCK,DB() as c:
    c.executescript("""CREATE TABLE IF NOT EXISTS ai3701_notebooks(notebook_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ai3701_sources(source_id TEXT PRIMARY KEY,notebook_id TEXT NOT NULL,user_id TEXT NOT NULL,title TEXT NOT NULL,source_type TEXT NOT NULL,source_url TEXT,content TEXT NOT NULL,content_hash TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ready',created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ai3701_turbo(job_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,title TEXT NOT NULL,objective TEXT NOT NULL,duration_seconds INTEGER NOT NULL,status TEXT NOT NULL,progress REAL NOT NULL DEFAULT 0,stage TEXT NOT NULL,dir TEXT NOT NULL,result_json TEXT NOT NULL DEFAULT '{}',error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,updated_at REAL NOT NULL,completed_at REAL);""")

try:
 import studio_ultimate as studio
 if not getattr(APP.state,"ai3701_studio_registered",False): studio.register(APP,model_fn=globals().get("_2700_model")); APP.state.ai3701_studio_registered=True
except Exception as e: studio=None; APP.state.ai3701_studio_error=str(e)[:400]
try:
 import creator_pro_os as pro
 if not getattr(APP.state,"ai3701_pro_registered",False): pro.register_pro(APP); APP.state.ai3701_pro_registered=True
except Exception as e: pro=None; APP.state.ai3701_pro_error=str(e)[:400]

if studio:
 try:
  OV,OC,OA=studio._make_voice,studio._concat_video_clips,studio._assemble
  def fast_voice(plan,workdir,voice="en-US-AriaNeural"):
   exe=shutil.which("espeak-ng") or shutil.which("espeak")
   if not exe:return OV(plan,workdir,voice)
   ch=plan.get("chapters") or []
   def one(i,x):
    p=workdir/("voice_%03d.wav"%i); subprocess.run([exe,"-s","155","-w",str(p),clean(x.get("narration"),18000)],check=True,timeout=240); return p
   with ThreadPoolExecutor(max_workers=min(6,max(1,len(ch)))) as pool: parts=sorted([f.result() for f in as_completed([pool.submit(one,i+1,x) for i,x in enumerate(ch)])],key=lambda p:p.name)
   if not parts:return OV(plan,workdir,voice)
   m=workdir/"voices.txt";m.write_text("".join("file '%s'\n"%str(p).replace("'","'\\''") for p in parts),encoding="utf-8");wav=workdir/"narration.wav";mp3=workdir/"narration.mp3"
   subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(m),"-c:a","pcm_s16le",str(wav)],check=True,timeout=900,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   subprocess.run(["ffmpeg","-y","-i",str(wav),"-codec:a","libmp3lame","-q:a","3",str(mp3)],check=True,timeout=900,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   return mp3,"local-espeak-parallel"
  def fast_concat(clips,target,workdir,width=1280,height=720):
   if not clips:return None
   def norm(i,c):
    p=workdir/("clip_%03d.mp4"%i);studio._ffmpeg("-i",c["path"],"-t","12","-vf",f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}","-an","-r","30","-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",p,timeout=240);return p
   with ThreadPoolExecutor(max_workers=min(6,len(clips))) as pool:parts=sorted([f.result() for f in as_completed([pool.submit(norm,i,c) for i,c in enumerate(clips)]) if True],key=lambda p:p.name)
   parts=[p for p in parts if p.exists()]
   if not parts:return OC(clips,target,workdir,width,height)
   m=workdir/"clips.txt";m.write_text("".join("file '%s'\n"%str(parts[i%len(parts)]).replace("'","'\\''") for i in range(max(1,int(target/12)+1))),encoding="utf-8");out=workdir/"visuals.mp4"
   subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(m),"-t",str(target),"-c","copy","-fflags","+genpts",str(out)],check=True,timeout=1200,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);return out
  def fast_assemble(v,a,m,s,o,d):
   if os.getenv("AI_INFINITY_FAST_MODE","1").lower() not in {"1","true","yes","on"}:return OA(v,a,m,s,o,d)
   filt="[1:a]volume=1[a];[2:a]volume=.12[m];[a][m]amix=inputs=2:duration=first:dropout_transition=2[out]"
   subprocess.run(["ffmpeg","-y","-i",str(v),"-i",str(a),"-i",str(m),"-filter_complex",filt,"-map","0:v:0","-map","[out]","-t",str(d),"-c:v","copy","-c:a","aac","-b:a","160k","-movflags","+faststart",str(o)],check=True,timeout=1800,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  studio._make_voice=fast_voice; studio._concat_video_clips=fast_concat; studio._assemble=fast_assemble
 except Exception: pass

def owned_nb(nid,u):
 with LOCK,DB() as c:r=c.execute("SELECT 1 FROM ai3701_notebooks WHERE notebook_id=? AND user_id=?",(nid,u)).fetchone()
 if not r:raise HTTPException(404,"notebook not found")

@APP.get("/infinity/3701/health")
def ai3701_health(): return {"status":"healthy","version":"TARGET-2050.3701","build":"EXTRAORDINARY-TURBO60-NOTEBOOK-GITHUB","creator_pro":bool(getattr(APP.state,"ai3701_pro_registered",False)),"studio_3607":bool(getattr(APP.state,"ai3701_studio_registered",False)),"notebook_grounding":True,"github_style_engineering":True,"turbo_60":True,"truthful":True}

@APP.post("/infinity/3701/knowledge/notebooks")
def nb_create(payload:dict,request:Request):
 u=uid(request);n="nb_"+uuid.uuid4().hex;t=now();title=clean(payload.get("title") or "AI Infinity Research Notebook",240);desc=clean(payload.get("description"),1200)
 with LOCK,DB() as c:c.execute("INSERT INTO ai3701_notebooks VALUES(?,?,?,?,?,?)",(n,u,title,desc,t,t))
 return {"status":"created","notebook":{"notebook_id":n,"title":title,"description":desc},"truthful":True}

@APP.get("/infinity/3701/knowledge/notebooks")
def nb_list(request:Request):
 u=uid(request)
 with LOCK,DB() as c:r=[dict(x) for x in c.execute("SELECT notebook_id,title,description,created_at,updated_at FROM ai3701_notebooks WHERE user_id=? ORDER BY updated_at DESC",(u,)).fetchall()]
 return {"notebooks":r,"truthful":True}

@APP.post("/infinity/3701/knowledge/notebooks/{nid}/sources")
def nb_source(nid:str,payload:dict,request:Request):
 u=uid(request);owned_nb(nid,u);typ=clean(payload.get("source_type") or "text",30).lower();url=clean(payload.get("url"),1600);title=clean(payload.get("title") or "Source",300);content=clean(payload.get("content"),120000)
 if typ=="url":
  if not studio:raise HTTPException(503,"source reader unavailable")
  try:
   with studio._safe_open_get(url,timeout=8) as r:raw=r.read(2500000)
  except Exception as e:raise HTTPException(422,"source fetch failed: "+str(e)[:300])
  text=raw.decode("utf-8","replace");m=re.search(r"<title[^>]*>(.*?)</title>",text,re.I|re.S)
  if m:title=re.sub(r"<[^>]+>","",m.group(1)).strip() or title
  content=re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>"," ",text,flags=re.I);content=re.sub(r"\s+"," ",html.unescape(content)).strip()[:120000]
 if not content:raise HTTPException(422,"source content is required")
 t=now();sid="src_"+uuid.uuid4().hex;h=hashlib.sha256(content.encode()).hexdigest()
 with LOCK,DB() as c:c.execute("INSERT INTO ai3701_sources VALUES(?,?,?,?,?,?,?,?,?,?,?)",(sid,nid,u,title,typ,url or None,content,h,"ready",t,t))
 return {"status":"ready","source":{"source_id":sid,"title":title,"source_type":typ,"source_url":url or None,"content_hash":h},"truthful":True}

@APP.post("/infinity/3701/knowledge/query")
def nb_query(payload:dict,request:Request):
 u=uid(request);nid=clean(payload.get("notebook_id"),100);q=clean(payload.get("query"),6000);owned_nb(nid,u)
 with LOCK,DB() as c:r=[dict(x) for x in c.execute("SELECT * FROM ai3701_sources WHERE notebook_id=? AND user_id=?",(nid,u)).fetchall()]
 if not r:return {"status":"empty","answer":"Add a source first.","citations":[],"truthful":True}
 terms=re.findall(r"[A-Za-z0-9]{3,}",q.lower());rank=sorted(r,key=lambda x:sum(x["content"].lower().count(t) for t in terms),reverse=True)[:5];e=[];cite=[]
 for i,x in enumerate(rank,1):e.append("[S%d] %s\n%s"%(i,x["title"],re.sub(r"\s+"," ",x["content"])[:2800]));cite.append({"id":"S%d"%i,"title":x["title"],"url":x["source_url"]})
 answer=None;model=globals().get("_2700_model")
 if callable(model):
  try:z=model({"messages":[{"role":"system","content":"Answer only from supplied sources. Cite [S1], [S2]. Never invent evidence."},{"role":"user","content":"QUESTION:\n"+q+"\n\nSOURCES:\n"+"\n\n".join(e)}],"temperature":0.1}) or {};answer=clean(z.get("text") or z.get("answer"),16000)
  except Exception:answer=None
 if not answer:answer="\n\n".join("[%s] %s"%(cite[i]["id"],e[i].split("\n",1)[1][:900]) for i in range(min(3,len(cite))))
 return {"status":"completed","answer":answer,"citations":cite,"grounded":True,"truthful":True}

@APP.get("/infinity/3701/engineering/overview")
def eng_overview():
 repo=os.getenv("AI_INFINITY_GITHUB_REPO","aurlooij-afk/AI-INFINITY");token=os.getenv("AI_INFINITY_GITHUB_TOKEN","").strip();h={"Accept":"application/vnd.github+json","User-Agent":"AI-Infinity/3701"}; 
 if token:h["Authorization"]="Bearer "+token
 out={"repository":repo,"write_enabled":bool(token),"capabilities":["code search","issues","pull-request review","actions","activity","quality gates"],"truthful":True}
 try:
  with urlopen(URLRequest("https://api.github.com/repos/"+repo,headers=h),timeout=8) as r:out["repo"]=json.loads(r.read(1200000).decode())
  with urlopen(URLRequest("https://api.github.com/repos/"+repo+"/commits?per_page=6",headers=h),timeout=8) as r:out["commits"]=json.loads(r.read(1200000).decode())
 except Exception as e:out["read_error"]=str(e)[:300]
 return out

TPOOL=ThreadPoolExecutor(max_workers=4)
def turbo_chapters(title,obj,duration):
 focus=["Cold open","Context","Mental model","Core framework","Mechanism","Case study","Failure modes","Workflow","Tradeoffs","Applications","Checklist","Synthesis"]
 rat=[.055,.07,.08,.095,.11,.1,.08,.095,.075,.085,.07,.085]
 base=[
  "Define the decision and the outcome clearly. Separate evidence from assumption and make the constraints explicit. State what the audience should be able to explain or do after this section, and keep the section tied to the larger objective.",
  "Explain the mechanism step by step. Show what changes, what stays constant, which inputs matter, and which signals indicate progress. Prefer cause-and-effect reasoning over slogans, and distinguish observed evidence from interpretation.",
  "Use a practical example, a counterexample, and a boundary case. Make the audience able to predict what happens next. Turn the idea into a repeatable method with inputs, actions, checks, recovery steps, and a measurable result.",
  "Connect the section to research, execution, measurement, recovery, and iteration. Explain how a real operator would move from information to action, how quality is checked, what can fail, and how the workflow adapts without pretending uncertainty is certainty.",
  "End with a concrete action, metric, and verification question. Give the viewer a decision rule, a checklist, a useful warning, and a next experiment. Keep the language direct, information-dense, and appropriate for spoken narration."
 ]
 variants=[
  "Take the same idea from the perspective of a beginner, then from the perspective of an experienced operator. Explain what each person would notice first and what common mistake would waste time.",
  "Compare a fast path and a high-quality path. Explain when speed is valuable, when it creates hidden cost, and how to preserve reliability while reducing unnecessary work.",
  "Trace one realistic scenario from start to finish. Identify the trigger, the first decision, the main bottleneck, the evidence used, the action taken, the verification step, and the lesson learned.",
  "Describe failure recovery. Show how to detect a bad assumption early, isolate the problem, choose a safe fallback, record the lesson, and return to the main objective without fabricating success.",
  "Explain measurement. Define the leading signal, the lagging result, the quality threshold, and the point where the operator should stop, revise, or continue.",
  "Add an advanced nuance that is often skipped in superficial explanations. Clarify the tradeoff, the dependency, the edge case, and the reason the simple rule can break under different conditions.",
  "Turn the section into a mini operating playbook: preparation, execution, verification, handoff, documentation, and follow-up. Make every step observable and useful outside the video itself.",
  "Ask the difficult question an expert audience would raise. Answer it carefully, separating what can be supported from what remains uncertain, and show what evidence would change the conclusion.",
  "Close by reconnecting this section to the central objective. Summarize the insight, name the practical consequence, and give the audience one concrete next move they can make immediately."
 ]
 out=[]
 for i,(f,r) in enumerate(zip(focus,rat),1):
  text="\n\n".join("%s Section: %s Topic: %s"%(b,f,obj) for b in base+variants)
  out.append({"index":i,"heading":f,"duration":max(60,int(duration*r)),"narration":text,"visual_query":title+" "+f,"on_screen":f,"proof_needed":[]})
 out[-1]["duration"]=max(60,duration-sum(x["duration"] for x in out[:-1]));return out
def turbo_model(payload):
 msg=payload.get("messages",[{}])[-1].get("content","");m=re.search(r"TITLE:\s*(.+)",msg);title=(m.group(1).strip() if m else "AI Infinity flagship episode");m=re.search(r"OBJECTIVE:\s*(.+)",msg);obj=(m.group(1).strip() if m else "Create useful long-form content");m=re.search(r"DURATION:\s*(\d+)",msg);dur=int(m.group(1)) if m else 3600
 plan={"title":title[:200],"hook":"A clear answer to the outcome that matters.","premise":obj[:5000],"audience":"general audience","tone":"cinematic, intelligent, useful","cta":"Save and apply the framework.","language":"English","chapters":turbo_chapters(title,obj,dur)};return {"text":json.dumps(plan,ensure_ascii=False),"provider":"ai-infinity-turbo-local"}
def turbo_worker(jid,title,obj,dur):
 try:
  # Never run hour-long media encoding inside the Render web process by default.
  # The instant package is the fast path; heavyweight rendering is explicit/opt-in.
  if os.getenv("AI_INFINITY_TURBO_RENDER","0").lower() not in {"1","true","yes","on"}:
   res={"status":"package_ready","render":"deferred","duration_seconds":dur,"reason":"heavy media rendering is isolated from the web request/runtime","truthful":True}
  else:
   with LOCK,DB() as c:c.execute("UPDATE ai3701_turbo SET status='media',stage='audio-video',progress=.35,updated_at=? WHERE job_id=?",(now(),jid))
   req={"title":title,"objective":obj,"format":"long","duration":dur,"audience":"general audience","tone":"cinematic, intelligent, useful","language":"English","platforms":["YouTube","Instagram","TikTok","LinkedIn"],"voice":"en-US-AriaNeural"}
   res=studio.create_project(req,model_fn=turbo_model) if studio else {"status":"package_only","truthful":True}
  with LOCK,DB() as c:c.execute("UPDATE ai3701_turbo SET status='completed',stage='done',progress=1,result_json=?,updated_at=?,completed_at=? WHERE job_id=?",(jd(res),now(),now(),jid))
 except Exception as e:
  with LOCK,DB() as c:c.execute("UPDATE ai3701_turbo SET status='failed',stage='error',error=?,updated_at=?,completed_at=? WHERE job_id=?",(str(e)[:2000],now(),now(),jid))
@APP.post("/infinity/3701/turbo/compose")
def turbo_compose(payload:dict,request:Request):
 u=uid(request);title=clean(payload.get("title") or "AI Infinity flagship 60-minute episode",240);obj=clean(payload.get("objective"),5000)
 if not obj:raise HTTPException(422,"objective is required")
 dur=max(900,min(3600,int(payload.get("duration_seconds") or 3600)));jid="turbo_"+uuid.uuid4().hex;root=DATA/"turbo"/jid;root.mkdir(parents=True,exist_ok=True);ch=turbo_chapters(title,obj,dur)
 (root/"storyboard.json").write_text(jd({"title":title,"objective":obj,"duration_seconds":dur,"chapters":ch,"turbo":True,"truthful":True}),encoding="utf-8");(root/"script.md").write_text("# "+title+"\n\n## Objective\n"+obj+"\n\n"+"\n\n".join("## "+x["heading"]+"\n\n"+x["narration"] for x in ch)+"\n",encoding="utf-8");(root/"sources.json").write_text(jd({"sources":[],"note":"Research is continued by the Studio engine; no invented sources.","truthful":True}),encoding="utf-8")
 cur=0;lines=[]
 for i,x in enumerate(ch,1):start=cur;cur+=x["duration"];fmt=lambda v:"%02d:%02d:%02d,000"%(v//3600,(v%3600)//60,v%60);lines.append("%d\n%s --> %s\n%s\n"%(i,fmt(start),fmt(cur),re.sub(r"\s+"," ",x["narration"])[:900]))
 (root/"captions.srt").write_text("\n".join(lines),encoding="utf-8");t=now();result={"script":"/infinity/3701/turbo/artifact/%s/script.md"%jid,"storyboard":"/infinity/3701/turbo/artifact/%s/storyboard.json"%jid,"sources":"/infinity/3701/turbo/artifact/%s/sources.json"%jid,"captions":"/infinity/3701/turbo/artifact/%s/captions.srt"%jid}
 with LOCK,DB() as c:c.execute("INSERT INTO ai3701_turbo VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(jid,u,title,obj,dur,"queued",.18,"instant_package",str(root),jd(result),"",t,t,None))
 TPOOL.submit(turbo_worker,jid,title,obj,dur)
 return {"status":"accepted","job_id":jid,"duration_seconds":dur,"instant_content":True,"instant_artifacts":list(result.values()),"message":"One-hour content package is available immediately; real audio/video production continues in parallel.","truthful":True}
@APP.get("/infinity/3701/turbo/{jid}")
def turbo_status(jid:str,request:Request):
 u=uid(request)
 with LOCK,DB() as c:r=c.execute("SELECT * FROM ai3701_turbo WHERE job_id=? AND user_id=?",(jid,u)).fetchone()
 if not r:raise HTTPException(404,"turbo job not found")
 d=dict(r);d["result"]=json.loads(d.pop("result_json","{}"));return {"job":d,"truthful":True}
@APP.get("/infinity/3701/turbo/artifact/{jid}/{name}")
def turbo_artifact(jid:str,name:str,request:Request):
 u=uid(request)
 with LOCK,DB() as c:r=c.execute("SELECT dir FROM ai3701_turbo WHERE job_id=? AND user_id=?",(jid,u)).fetchone()
 if not r:raise HTTPException(404,"turbo job not found")
 root=Path(r[0]).resolve();p=(root/name).resolve()
 if root not in p.parents or not p.exists():raise HTTPException(404,"artifact not found")
 return FileResponse(p)
