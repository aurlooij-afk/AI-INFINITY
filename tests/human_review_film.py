#!/usr/bin/env python3
"""Generate one real AI Infinity project for human editorial review."""
import hashlib,json,os,re,shutil,subprocess,sys,time,zipfile
from pathlib import Path
from urllib.parse import quote
import requests

BASE="http://127.0.0.1:18086"
DATA=Path("/tmp/ai-infinity-one-idea-runtime")
OUT=Path(os.environ.get("AI_INFINITY_EVIDENCE_DIR","/review-evidence"))
FILES=OUT/"deliverables"
TITLE="ONE IDEA. INFINITE WORLDS."
OUT.mkdir(parents=True,exist_ok=True); FILES.mkdir(parents=True,exist_ok=True)
BRIEF="""Create an extraordinary 60-second cinematic brand film for AI Infinity titled ONE IDEA. INFINITE WORLDS. This must be a real video produced by the existing AI Infinity Creator Operating System, not a slideshow, mockup, synthetic screen, or hand-built substitute.

STORY: 0-4s: an arresting cinematic hook, creator facing a blank screen; narration: Every extraordinary world begins with something nobody else can see. 4-12s: one idea opens into expansive landscapes, an evocative futuristic city and human emotion, linked by a visual motif. 12-25s: show research, a clear narrative, writing and intentional story planning through relevant, distinct scenes. 25-40s: strongest sequence, deliberate composition, diverse shot sizes, controlled camera motion, atmospheric depth, motivated transitions, intentional lighting and emotional progression. 40-49s: show genuine editorial craft, timeline, narration, music, synchronized captions, render and quality checks. Do not fake UI or features AI Infinity cannot demonstrate. 49-56s: reveal idea becoming creative work; narration: Research it. Shape it. Bring it to life. Then make it better. 56-60s: restrained premium title reveal; final line: AI Infinity. One idea. Infinite worlds.

VISUAL DIRECTION: premium global technology-campaign quality; midnight blue, controlled cyan/violet and warm gold practical light, natural textures, rich blacks, atmospheric depth, elegant framing and visual continuity. Contrast intimate beats with expansive reveals. No generic unrelated montage, repeated imagery, random zooms, cluttered overlays, fake UI, malformed text or cheap transitions.

AUDIO: original intelligent English narration that fits the actual runtime; use a natural neural English voice if available and report the real provider. Coherent music arc from intimate opening to controlled climax and clean resolution. Narration intelligible, music balanced, captions synchronized.

PRODUCTION: create an actual 60-second 16:9 film through the existing AI Infinity workflow, targeting 1920x1080, with multiple distinct rendered scenes, relevant real-source visuals, voiceover, captions, thumbnail, audio master, complete package and editable project/timeline files where supported. Prefer verified public-domain or clearly licensed visuals. Preserve URLs, creator/rights-holder, licence/attribution, provenance and integrity evidence. Free-first only; no paid services. Never invent research, rights, footage, providers or capabilities. Unavailable footage, uncertain rights, fallback visuals or degraded research must remain visible and REVIEW_REQUIRED.

VERIFY: inspect the actual MP4 with FFprobe and full FFmpeg decode; verify duration, resolution, streams, scene count, distinct visuals, voice, caption timing, audio integrity, source relevance, rights and manifest SHA-256. Make a contact sheet sampled across the entire film. Preserve script, captions, audio master, narration-only audio if available, sources, provenance, fact-check, rights/production manifests, thumbnail, package and review packet. Technical verification is not human approval. Do not publish. Never mark success solely because rendering finished."""

def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for c in iter(lambda:f.read(1048576),b""): h.update(c)
 return h.hexdigest()
def api(s,path,method="GET",**kw):
 r=s.request(method,BASE+path,timeout=150,**kw); r.raise_for_status(); return r.json()
def probe(p):
 r=subprocess.run(["ffprobe","-v","error","-show_format","-show_streams","-of","json",str(p)],capture_output=True,text=True,timeout=180)
 if r.returncode: raise RuntimeError("ffprobe failed: "+r.stderr[-1200:])
 return json.loads(r.stdout)
def contact(video,duration,target):
 from PIL import Image,ImageDraw,ImageFont
 frames=[]
 for i,f in enumerate((.035,.14,.255,.37,.49,.61,.73,.85,.965),1):
  stamp=duration*f; p=OUT/("frame-%02d.jpg"%i)
  r=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-ss",f"{stamp:.3f}","-i",str(video),"-frames:v","1","-vf","scale=480:270",str(p)],capture_output=True,text=True,timeout=120)
  if r.returncode or not p.exists() or not p.stat().st_size: raise RuntimeError("Frame extraction failed: "+r.stderr[-500:])
  with Image.open(p) as im: frames.append((im.convert("RGB").copy(),stamp))
 gap,cw,ch=18,500,305
 sheet=Image.new("RGB",(3*cw+4*gap,3*ch+4*gap),(11,16,27)); d=ImageDraw.Draw(sheet)
 try: font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",18)
 except Exception: font=ImageFont.load_default()
 for idx,(im,t) in enumerate(frames):
  x=gap+(idx%3)*(cw+gap); y=gap+(idx//3)*(ch+gap)
  d.text((x+8,y+2),f"{idx+1:02d}  {t:05.1f}s",fill=(245,247,252),font=font); sheet.paste(im,(x+10,y+27))
 sheet.save(target,"PNG",optimize=True)
def main():
 log=(OUT/"engine-server.log").open("w",encoding="utf-8")
 env=os.environ.copy(); env.update({"AI_INFINITY_DATA_DIR":str(DATA),"AI_INFINITY_3601_BACKGROUND":"false","AI_INFINITY_FAST_MODE":"0","AI_INFINITY_MAX_HEAVY_JOBS":"1","AI_INFINITY_REQUIRE_SOURCE_VISUALS":"1","AI_INFINITY_REQUIRE_RESEARCH_EVIDENCE":"1","AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS":"0","AI_INFINITY_PERSISTENCE_MODE":"volatile","AI_INFINITY_PROJECT_STATE_DURABLE":"false","AI_INFINITY_PERSISTENT_VOLUME_CONFIRMED":"false","PYTHONUNBUFFERED":"1"})
 DATA.mkdir(parents=True,exist_ok=True)
 proc=subprocess.Popen([sys.executable,"-m","uvicorn","main:app","--host","127.0.0.1","--port","18086"],cwd="/app",env=env,stdout=log,stderr=subprocess.STDOUT)
 summary={"schema":"ai-infinity-human-review-production.v1","title":TITLE,"engine":"existing AI Infinity Creator Operating System","human_review_status":"PENDING","publish_performed":False,"blockers":[]}
 try:
  s=requests.Session(); until=time.time()+120
  while time.time()<until:
   if proc.poll() is not None: raise RuntimeError("AI Infinity runtime exited during startup")
   try: summary["health"]=api(s,"/health"); break
   except Exception: time.sleep(2)
  else: raise RuntimeError("AI Infinity health check timed out")
  created=api(s,"/infinity/canonical/create","POST",json={"command":BRIEF,"title":TITLE,"duration":60,"format":"long","aspect_ratio":"16:9","language":"English","idempotency_key":"human-review-one-idea-infinite-worlds-2026-10-26"})
  pid=str(created.get("project_id") or "")
  if not pid: raise RuntimeError("Creator returned no project ID: "+json.dumps(created)[:1000])
  summary["project_id"]=pid; summary["prompt_sha256"]=hashlib.sha256(BRIEF.encode()).hexdigest()
  print("AI_INFINITY_PROJECT_ID="+pid,flush=True)
  end=time.time()+2400; state={}
  while time.time()<end:
   state=api(s,f"/infinity/studio/project/{quote(pid,safe='')}")
   st=str(state.get("status") or "").lower()
   print("AI_INFINITY_STATE="+json.dumps({"project_id":pid,"status":st,"stage":state.get("stage"),"progress":state.get("progress"),"error":state.get("error")}),flush=True)
   if st in {"completed","completed_with_qc_warnings","failed","cancelled"}: break
   time.sleep(5)
  summary["project_status"]=state
  truth=api(s,f"/infinity/studio/project/{quote(pid,safe='')}/truth"); summary["truth"]=truth
  checks=truth.get("checks") if isinstance(truth.get("checks"),dict) else {}
  try:
   inventory=api(s,f"/infinity/studio/project/{quote(pid,safe='')}/artifacts")
   summary["artifact_names"]=sorted({str(a.get("asset_name") or a.get("name") or "") for a in inventory.get("artifacts",[])})
  except Exception as exc: summary["artifact_inventory_error"]=type(exc).__name__+":"+str(exc)[:300]
  names=["final.mp4","thumbnail.jpg","captions.srt","script.md","sources.json","provenance.json","fact_check.json","visual_rights.json","rights_manifest.json","production_manifest.json","manifest.json","audio_master.mp3","narration_only.mp3","article.md","seo.json","social_campaign.json","accessibility.json","package.zip","timeline.otio","timeline.json"]
  got={}
  for name in names:
   try:
    r=s.get(BASE+f"/infinity/studio/project/{quote(pid,safe='')}/asset/{quote(name,safe='')}",timeout=300)
    if r.ok and r.content:
     p=FILES/Path(name).name; p.write_bytes(r.content); got[name]=p
    elif name in {"final.mp4","captions.srt","sources.json","rights_manifest.json","production_manifest.json"}:
     summary.setdefault("asset_download_diagnostics",{})[name]=f"HTTP {r.status_code}"
   except Exception as exc:
    if name in {"final.mp4","captions.srt","sources.json","rights_manifest.json","production_manifest.json"}: summary.setdefault("asset_download_diagnostics",{})[name]=type(exc).__name__+":"+str(exc)[:160]
  proj=DATA/"creator_studio"/pid
  if "final.mp4" not in got and (proj/"final.mp4").is_file():
   p=FILES/"final.mp4"; shutil.copy2(proj/"final.mp4",p); got["final.mp4"]=p
  if "final.mp4" not in got: raise RuntimeError("No actual final.mp4 in API or the real AI Infinity project workspace")
  final=got["final.mp4"]; media=probe(final); streams=media.get("streams") or []
  v=next((x for x in streams if x.get("codec_type")=="video"),{}); a=next((x for x in streams if x.get("codec_type")=="audio"),{})
  dur=float((media.get("format") or {}).get("duration") or 0); w,h=int(v.get("width") or 0),int(v.get("height") or 0)
  dec=subprocess.run(["ffmpeg","-hide_banner","-v","error","-xerror","-i",str(final),"-f","null","-"],capture_output=True,text=True,timeout=600)
  summary["final_media"]={"size_bytes":final.stat().st_size,"sha256":sha(final),"duration_seconds":dur,"resolution":[w,h],"video_codec":v.get("codec_name"),"audio_codec":a.get("codec_name"),"ffprobe":media}
  summary["full_decode"]={"passed":dec.returncode==0,"stderr":dec.stderr[-1200:]}
  sources={}; rights={}; manifest={}
  for name,key in [("sources.json","sources"),("rights_manifest.json","rights_manifest"),("production_manifest.json","production_manifest")]:
   try:
    summary[key]=json.loads(got[name].read_text(encoding="utf-8",errors="replace")) if name in got else {}
   except Exception as exc: summary[key+"_error"]=type(exc).__name__+":"+str(exc)[:250]; summary[key]={}
  sources=summary.get("sources",{}); rights=summary.get("rights_manifest",{}); manifest=summary.get("production_manifest",{})
  source_count=int(sources.get("source_count") or len(sources.get("sources") or []))
  unresolved=int(checks.get("visual_rights_unresolved_count") or 0)
  hashes={str(x.get("name")):str(x.get("sha256")) for x in manifest.get("artifacts",[])}
  srt=got.get("captions.srt"); cues=[]; srt_valid=False
  if srt:
   txt=srt.read_text(encoding="utf-8",errors="replace")
   pat=re.compile(r"(?m)^\s*(\d+)\s*\n(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*$")
   cues=list(pat.finditer(txt))
   def seconds(g): return int(g[0])*3600+int(g[1])*60+int(g[2])+int(g[3])/1000
   prev=-1; srt_valid=bool(cues)
   for m in cues:
    start=seconds(m.groups()[1:5]); finish=seconds(m.groups()[5:9])
    if finish<=start or start<prev: srt_valid=False
    prev=finish
   srt_valid=srt_valid and prev<=dur+.25
  gates={"render_completed":str(state.get("status") or "").lower()=="completed","truth_verified":truth.get("verified") is True and str(truth.get("state") or "").upper()=="VERIFIED","duration_60s":abs(dur-60)<=1,"resolution_1920x1080":(w,h)==(1920,1080),"aspect_ratio_16x9":bool(w and h) and abs(w/h-16/9)<.015,"full_ffmpeg_decode":dec.returncode==0,"video_stream":bool(v),"audio_stream":bool(a),"source_visual_policy":checks.get("source_visual_policy_passed") is True,"zero_fallbacks":int(checks.get("fallback_visual_count") or 0)==0,"rights_evidence":checks.get("visual_rights_evidence_present") is True,"zero_unresolved_rights":unresolved==0,"research_sources_present":source_count>0,"caption_timing":srt_valid,"manifest_sha_matches":bool(hashes) and hashes.get("final.mp4")==sha(final),"production_manifest_valid":manifest.get("project_id")==pid and manifest.get("truthful") is True}
  summary["source_count"]=source_count; summary["rights_asset_count"]=len(rights.get("assets") or []); summary["unresolved_rights_count"]=unresolved; summary["captions"]={"exists":bool(srt),"cue_count":len(cues),"timing_valid":srt_valid}
  summary["acceptance_gates"]=gates; summary["all_technical_gates_passed"]=all(gates.values()); summary["blockers"] += ["Gate failed: "+k for k,val in gates.items() if not val]
  contact=OUT/"contact-sheet.png"; contact_sheet(final,dur,contact); summary["contact_sheet_sha256"]=sha(contact)
  score={"schema":"ai-infinity-human-editorial-scorecard.v1","project_id":pid,"title":TITLE,"human_review_status":"PENDING","reviewers":[],"scale":{"min":1,"max":5},"dimensions":[{"name":n,"critical":c,"rating":None,"notes":""} for n,c in [("hook_and_story_arc",True),("scene_relevance_and_variety",True),("pacing_and_transitions",False),("voice_delivery",True),("audio_mix",True),("caption_sync",True),("visual_composition",False),("facts_and_sources",True),("rights_and_attribution",True),("brand_fit",False),("editability_and_recovery",False)]]}
  (OUT/"editorial-scorecard-template.json").write_text(json.dumps(score,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
  readme="AI INFINITY HUMAN REVIEW PACKET\n\nTitle: "+TITLE+"\nProject ID: "+pid+"\nHuman review: PENDING\nPublishing: NOT PERFORMED\n\nThis is the actual AI Infinity engine output. Inspect production-summary.json for quality gates. Technical verification does not imply human approval. Review hook, story, scene continuity, source relevance, rights, voice, music, captions, facts, brand fit and editability. Failed gates remain blockers.\n"
  (OUT/"README.txt").write_text(readme,encoding="utf-8")
  (OUT/"production-summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
  packet=OUT/"AI-Infinity-One-Idea-Infinite-Worlds-Review-Package.zip"
  with zipfile.ZipFile(packet,"w",zipfile.ZIP_DEFLATED,compresslevel=3) as z:
   z.write(OUT/"README.txt","review/README.txt"); z.write(OUT/"production-summary.json","review/production-summary.json"); z.write(OUT/"editorial-scorecard-template.json","review/editorial-scorecard-template.json"); z.write(contact,"review/contact-sheet.png")
   for p in sorted(FILES.glob("*")):
    if p.is_file(): z.write(p,"deliverables/"+p.name,compress_type=zipfile.ZIP_STORED if p.suffix.lower() in {".mp4",".mp3",".zip"} else zipfile.ZIP_DEFLATED)
   if proj.exists():
    for p in sorted(proj.glob("*")):
     if p.is_file() and p.suffix.lower() in {".json",".md",".srt",".otio",".xml",".txt"}: z.write(p,"editable-project-files/"+p.name)
  summary["review_package"]={"sha256":sha(packet),"size_bytes":packet.stat().st_size}
  (OUT/"production-summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
  print("AI_INFINITY_REVIEW_RESULT="+json.dumps({"project_id":pid,"status":state.get("status"),"truth":truth.get("state"),"duration":dur,"resolution":[w,h],"sha256":sha(final),"source_count":source_count,"gates":gates,"package":packet.name},ensure_ascii=False),flush=True)
  if not all(gates.values()): raise RuntimeError("Video rendered and packaged, but gates failed: "+", ".join(k for k,v in gates.items() if not v))
 except Exception as exc:
  summary["fatal_error"]=type(exc).__name__+": "+str(exc)[:1800]
  (OUT/"production-summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
  print("AI_INFINITY_REVIEW_FAILURE="+summary["fatal_error"],flush=True)
  raise
 finally:
  if proc.poll() is None:
   proc.terminate()
   try: proc.wait(timeout=20)
   except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=10)
  log.close()
if __name__=="__main__": main()
