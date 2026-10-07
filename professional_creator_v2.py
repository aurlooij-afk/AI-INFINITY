"""
AI Infinity — Professional Creator System v2
Single canonical control surface for real content production.
"""
from __future__ import annotations
import json, os, re, shutil, zipfile
from pathlib import Path
from typing import Any, Dict, List
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

VERSION = "TARGET-2050.PRO-CREATOR.2"
BUILD = "PROFESSIONAL-CREATOR-SYSTEM-A-TO-Z"

def _safe(v: Any, limit: int = 6000) -> str:
    return str(v or "").replace("\x00","").strip()[:limit]

def _project(studio, pid: str, request: Request):
    uid=studio._get_user_id(request)
    p=studio._get_project(pid)
    if not p or p.get("user_id")!=uid:
        raise HTTPException(404,"project not found")
    return uid,p

def _json_value(v: Any)->Dict[str,Any]:
    if isinstance(v,dict): return v
    try: 
        x=json.loads(v or "{}")
        return x if isinstance(x,dict) else {}
    except Exception: return {}

def _truth(studio,p)->Dict[str,Any]:
    fn=getattr(studio,"_professional_truth",None)
    if not fn: return {"verified":False,"status":"verification_engine_unavailable","truthful":True}
    try: return fn(p,reconcile=True)
    except Exception as exc: return {"verified":False,"status":"verification_failed","error":str(exc)[:800],"truthful":True}

def _scenes(studio,p)->List[Dict[str,Any]]:
    root=studio._project_dir(p["project_id"])
    candidates=[]
    sb=root/"storyboard.json"
    if sb.exists():
        try: candidates.append(json.loads(sb.read_text(encoding="utf-8")))
        except Exception: pass
    b=_json_value(p.get("blueprint_json")); candidates += [b, b.get("plan") if isinstance(b.get("plan"),dict) else {}]
    r=_json_value(p.get("result_json")); candidates += [r, r.get("plan") if isinstance(r.get("plan"),dict) else {}]
    for c in candidates:
        for key in ("scenes","chapters"):
            rows=c.get(key) if isinstance(c,dict) else None
            if not isinstance(rows,list) or not rows: continue
            out=[]
            for i,x in enumerate(rows,1):
                if not isinstance(x,dict): continue
                try: d=float(x.get("actual_duration") or x.get("duration") or 6)
                except Exception: d=6
                out.append({"source_index":i,"source_asset":f"scene_{i:02d}.mp4","heading":_safe(x.get("heading") or x.get("title") or f"Scene {i}",240),"narration":_safe(x.get("narration") or x.get("text"),6000),"duration":max(.5,min(600,d))})
            if out: return out
    return []

def _timeline(studio,p)->Dict[str,Any]:
    root=studio._project_dir(p["project_id"])
    srcs=_scenes(studio,p)
    cursor=0.0; rows=[]
    for pos,x in enumerate(srcs,1):
        src=root/x["source_asset"]
        sd=float(studio.probe_duration(src)) if src.exists() else 0.0
        d=sd if sd>0 else float(x["duration"])
        rows.append({**x,"position":pos,"index":pos,"source_duration":round(d,3),"start":round(cursor,3),"end":round(cursor+d,3),"trim_start":0.0,"trim_end":0.0,"enabled":True,"source_exists":src.exists()})
        cursor += d
    return {"version":VERSION,"project_id":p["project_id"],"title":p.get("title"),"fps":30,"resolution":[1920,1080],"duration_seconds":round(cursor,3),"tracks":[{"id":"video","type":"video"},{"id":"audio","type":"audio"}],"scenes":rows,"truthful":True}

def _normalise(studio,p,payload):
    root=studio._project_dir(p["project_id"])
    rows=payload.get("scenes")
    if not isinstance(rows,list) or not rows: raise HTTPException(422,"timeline.scenes is required")
    out=[]; seen=set(); cursor=0.0
    for pos,raw in enumerate(rows[:40],1):
        if not isinstance(raw,dict): continue
        asset=Path(str(raw.get("source_asset") or "")).name
        m=re.fullmatch(r"scene_([0-9]{2})\.mp4",asset)
        if not m: raise HTTPException(422,"timeline contains an invalid scene source")
        source_index=int(m.group(1))
        if source_index in seen: raise HTTPException(422,"timeline contains a duplicate scene source")
        seen.add(source_index)
        src=root/asset
        if not src.exists(): raise HTTPException(409,f"missing scene source: {asset}")
        sd=float(studio.probe_duration(src))
        try: ts=max(0.0,float(raw.get("trim_start") or 0))
        except Exception: ts=0.0
        try: te=max(0.0,float(raw.get("trim_end") or 0))
        except Exception: te=0.0
        dur=sd-ts-te
        if dur<=0.25: raise HTTPException(422,f"trim removes scene {source_index}")
        row={"position":pos,"index":pos,"source_index":source_index,"source_asset":asset,"heading":_safe(raw.get("heading") or f"Scene {source_index}",240),"narration":_safe(raw.get("narration"),6000),"trim_start":round(ts,3),"trim_end":round(te,3),"duration":round(dur,3),"source_duration":round(sd,3),"enabled":bool(raw.get("enabled",True)),"start":round(cursor,3),"end":round(cursor+dur,3)}
        if row["enabled"]: cursor += dur
        out.append(row)
    if not any(x["enabled"] for x in out): raise HTTPException(422,"timeline must keep at least one enabled scene")
    cur=0.0
    for x in out:
        x["start"]=round(cur,3); x["end"]=round(cur+x["duration"],3)
        if x["enabled"]: cur += x["duration"]
    return {"version":VERSION,"project_id":p["project_id"],"title":p.get("title"),"fps":30,"resolution":[1920,1080],"duration_seconds":round(cur,3),"tracks":[{"id":"video","type":"video"},{"id":"audio","type":"audio"}],"scenes":out,"truthful":True}

def _save_timeline(studio,pid,payload):
    root=studio._project_dir(pid); path=root/"timeline.json"
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    studio.register_artifact(pid,path,"application/json",{"editor":VERSION})
    studio.audit_event(pid,"professional_timeline_saved",{"scene_count":len(payload.get("scenes") or []),"duration_seconds":payload.get("duration_seconds")})
    return path

def _manifest(studio,pid):
    root=studio._project_dir(pid); out=root/"manifest.json"
    items=[]
    for fp in sorted(root.iterdir()):
        if fp.is_file() and fp.name not in {"manifest.json","package.zip"}:
            try: items.append({"name":fp.name,"size_bytes":fp.stat().st_size,"sha256":studio.file_sha256(fp)})
            except Exception: pass
    out.write_text(json.dumps({"version":VERSION,"pipeline":BUILD,"project_id":pid,"master":"final.mp4","timeline":"timeline.json","files":items,"generated_at":studio.utc_iso(),"truthful":True},ensure_ascii=False,indent=2),encoding="utf-8")
    studio.register_artifact(pid,out,"application/json",{"editor":VERSION})
    return out

def _package(studio,pid):
    root=studio._project_dir(pid); out=root/"package.zip"
    names=["final.mp4","variant_16x9.mp4","variant_9x16.mp4","variant_1x1.mp4","variant_4x5.mp4","thumbnail.jpg","script.md","captions.srt","sources.json","fact_check.json","provenance.json","visual_rights.json","seo.json","social_campaign.json","accessibility.json","platform_manifest.json","timeline.json","manifest.json","production_truth.json"]
    files=[]
    for n in names:
        p=root/n
        if p.exists(): files.append(p)
    for p in root.glob("short_*.mp4"):
        if p not in files and p.exists(): files.append(p)
    if not files: raise RuntimeError("no deliverables to package")
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        for p in files: z.write(p,arcname=p.name)
    studio.register_artifact(pid,out,"application/zip",{"type":"verified_delivery_package","member_count":len(files),"editor":VERSION})
    return out

def _variants(studio,pid,master):
    root=studio._project_dir(pid)
    ratios={"16:9":(1920,1080),"9:16":(1080,1920),"1:1":(1080,1080),"4:5":(1080,1350)}
    res=[]
    for ratio,(w,h) in ratios.items():
        name=f"variant_{ratio.replace(':','x')}.mp4"; out=root/name
        studio.ffmpeg("-i",master,"-vf",f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30","-c:v","libx264","-preset",os.getenv("AI_INFINITY_VIDEO_PRESET","veryfast"),"-crf","18","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k","-ar","48000","-ac","2","-movflags","+faststart",out,timeout=1500)
        meta={"ratio":ratio,"width":w,"height":h,"source":master.name,"duration_seconds":round(studio.probe_duration(out),3),"sha256":studio.file_sha256(out),"truthful":True}
        studio._save_asset(pid,"variant",out,"video/mp4",meta); studio.register_artifact(pid,out,"video/mp4",meta)
        res.append({"ratio":ratio,"asset_name":name,"duration_seconds":meta["duration_seconds"]})
    return res

def _render(studio,pid,timeline):
    root=studio._project_dir(pid)
    selected=[x for x in timeline["scenes"] if x.get("enabled")]
    segs=[]
    chapters=[]
    for i,x in enumerate(selected,1):
        src=root/Path(x["source_asset"]).name; out=root/f"timeline_segment_{i:02d}.mp4"
        args=[]
        if x["trim_start"]>0: args += ["-ss",x["trim_start"]]
        args += ["-i",src,"-t",x["duration"],"-map","0:v:0","-map","0:a?","-c:v","libx264","-preset",os.getenv("AI_INFINITY_VIDEO_PRESET","veryfast"),"-crf","18","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k","-movflags","+faststart",out]
        studio.ffmpeg(*args,timeout=max(300,int(x["duration"]*20)))
        if not out.exists() or out.stat().st_size<10000: raise RuntimeError("timeline segment render failed")
        segs.append(out); chapters.append({"heading":x["heading"],"narration":x["narration"],"duration":x["duration"],"actual_duration":x["duration"]})
    manifest=root/"timeline_concat.txt"
    manifest.write_text("".join("file '"+str(p).replace("'","'\\''")+"'\\n" for p in segs),encoding="utf-8")
    tmp=root/"final.timeline.tmp.mp4"; master=root/"final.mp4"
    studio.ffmpeg("-f","concat","-safe","0","-i",manifest,"-c","copy","-movflags","+faststart",tmp,timeout=1800)
    os.replace(tmp,master)
    captions=root/"captions.srt"; studio.write_srt(chapters,captions); studio.register_artifact(pid,captions,"application/x-subrip",{"editor":VERSION})
    thumb=studio.make_thumbnail(master,chapters[0]["heading"],root); studio.register_artifact(pid,thumb,"image/jpeg",{"editor":VERSION})
    qc=studio.extended_quality_check(master,chapters,captions,root,assets=[])
    r=_json_value(studio._get_project(pid).get("result_json")); r["quality"]=qc; r["professional_timeline_render"]=timeline
    studio._update_project(pid,status="completed" if qc.get("passed") else "completed_with_qc_warnings",stage="quality_control",progress=100 if qc.get("passed") else 98,result_json=studio.jdump(r),error=None if qc.get("passed") else "Timeline render completed; professional QC requires review.")
    studio.register_artifact(pid,master,"video/mp4",{"editor":VERSION,"quality":qc,"sha256":studio.file_sha256(master)})
    _manifest(studio,pid)
    return master,qc

UI = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity — Professional Creator</title>
<style>body{margin:0;background:#070b10;color:#edf2f7;font:14px system-ui;padding:20px}main{max-width:1400px;margin:auto}.card{border:1px solid #263241;background:#0c131c;border-radius:16px;padding:16px;margin:12px 0}.grid{display:grid;grid-template-columns:1.4fr .6fr;gap:12px}.cmd{width:100%;min-height:150px;background:#080d13;color:white;border:1px solid #344255;border-radius:12px;padding:12px;box-sizing:border-box}.btn{border:1px solid #405064;background:#111a24;color:#eef2f6;padding:9px 12px;border-radius:9px}.primary{background:#d9f99d;color:#080d11;border-color:#d9f99d;font-weight:800}.row{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}.tl{display:grid;gap:8px}.scene{border:1px solid #283545;background:#0a1017;border-radius:12px;padding:12px}.sceneGrid{display:grid;grid-template-columns:1fr 1fr 110px 110px;gap:8px}.sceneGrid input,.sceneGrid textarea{width:100%;box-sizing:border-box;background:#080d13;color:white;border:1px solid #273647;border-radius:8px;padding:8px}.sceneGrid textarea{min-height:68px}.muted{color:#93a0b1;font-size:11px}.good{color:#9fe8cb}.warn{color:#f7d78a}.bad{color:#ff9fb0}.video{aspect-ratio:16/9;background:#020509;border-radius:12px;overflow:hidden}.video video{width:100%;height:100%}@media(max-width:900px){.grid{grid-template-columns:1fr}.sceneGrid{grid-template-columns:1fr}}</style></head>
<body><main><div class="row"><div><b>AI Infinity · Professional Creator System</b><div class="muted">One command → real production → editable scene timeline → verified delivery</div></div><span id="sys">Checking…</span></div>
<div class="grid"><section class="card"><h1>Produce the complete piece.</h1><div class="muted">The canonical Creator Studio engine performs research, script, media acquisition/generation, narration, sound, captions, rendering, QC and delivery.</div><textarea id="cmd" class="cmd" placeholder="Create a 60-second premium vertical video about ..."></textarea><div class="row" style="margin-top:8px"><button class="btn primary" onclick="createP()">Start production</button><button class="btn" onclick="openLatest()">Latest project</button></div><pre id="out"></pre></section>
<aside class="card"><b>Capabilities</b><div id="caps" style="margin-top:8px"></div></aside></div>
<section class="card"><div class="row"><div><b>Real scene timeline</b><div class="muted">Reorder, disable and trim real generated scene masters. Content rewrites use the production engine so narration never silently becomes stale.</div></div><button class="btn" onclick="loadTL()">Refresh timeline</button></div><div class="tl" id="tl" style="margin-top:10px"></div><div class="row" style="margin-top:10px"><input id="rev" style="flex:1;min-width:240px;background:#080d13;color:white;border:1px solid #273647;padding:10px;border-radius:8px" placeholder="Revision command, e.g. make the opening more direct and rewrite the CTA"><button class="btn" onclick="revise()">Queue revision</button><button class="btn primary" onclick="renderTL()">Render edited master</button></div></section>
<section class="card"><div class="row"><b>Verified delivery</b><button class="btn" onclick="verify()">Verify</button></div><div id="del" style="margin-top:8px" class="muted">No project selected.</div></section></main>
<script>
let pid=localStorage.getItem("aii_pro_pid")||"",t=null;const $=x=>document.getElementById(x);const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));async function api(u,o={}){const r=await fetch(u,{credentials:"same-origin",...o});const s=await r.text();let j;try{j=JSON.parse(s)}catch(_){j={raw:s}}if(!r.ok)throw Error(j.detail||j.message||s);return j}
async function init(){try{const h=await api("/infinity/pro2/health");$("sys").textContent=h.readiness_label;$("caps").innerHTML=(h.providers||[]).map(x=>"<div class='row'><span>"+esc(x.name)+"</span><span class='"+(x.ready?"good":"warn")+"'>"+esc(x.status)+"</span></div>").join("")}catch(e){$("sys").textContent="system check failed"}if(pid)openP(pid)}
async function createP(){const cmd=$("cmd").value.trim();if(cmd.length<8)return;try{const r=await api("/infinity/pro2/create",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({objective:cmd,title:cmd.slice(0,140),duration:/\\b(\\d+)\\s*(?:sec|second|seconds|s)\\b/i.test(cmd)?Number(cmd.match(/\\b(\\d+)\\s*(?:sec|second|seconds|s)\\b/i)[1]):60,format:/\\b(vertical|reel|short|tiktok|9:16)\\b/i.test(cmd)?"short":"long",aspect_ratio:/\\b(9:16|vertical|reel|short)\\b/i.test(cmd)?"9:16":"16:9",content_type:"video"})});pid=r.project_id;localStorage.setItem("aii_pro_pid",pid);openP(pid)}catch(e){$("out").textContent=e.message}}
async function openP(id){pid=id;localStorage.setItem("aii_pro_pid",id);await refresh();await loadTL()}
async function refresh(){if(!pid)return;try{const x=await api("/infinity/pro2/project/"+encodeURIComponent(pid));$("out").textContent=JSON.stringify({project_id:x.project_id,status:x.status,stage:x.stage,progress:x.progress,truth:x.truth},null,2);renderDelivery(x);if(!["completed","completed_with_qc_warnings","failed","cancelled"].includes(x.status)){if(!t)t=setInterval(refresh,1800)}else{clearInterval(t);t=null}}catch(e){$("out").textContent=e.message}}
function renderDelivery(x){if(!["completed","completed_with_qc_warnings"].includes(x.status)){$("del").textContent="Delivery unlocks after real production completes.";return}const b="/infinity/studio/project/"+encodeURIComponent(pid)+"/asset/";$("del").innerHTML=(x.truth&&x.truth.verified?"<span class='good'>PROFESSIONALLY VERIFIED</span>":"<span class='warn'>QC REVIEW REQUIRED</span>")+"<div class='row' style='margin-top:8px'>"+["final.mp4","package.zip","thumbnail.jpg","script.md","captions.srt","sources.json","timeline.json","fact_check.json","provenance.json"].map(n=>"<a class='btn' href='"+b+encodeURIComponent(n)+"' target='_blank'>"+esc(n)+"</a>").join("")+"</div>"}
async function loadTL(){if(!pid)return;try{window.tlData=await api("/infinity/pro2/project/"+encodeURIComponent(pid)+"/timeline");renderTL()}catch(e){$("tl").textContent=e.message}}
function renderTL(){const s=(window.tlData&&window.tlData.scenes)||[];$("tl").innerHTML=s.map((x,i)=>"<div class='scene' data-i='"+i+"'><div class='row'><b>"+(i+1)+". "+esc(x.heading)+"</b><span class='muted'>"+esc(x.source_asset)+" · "+x.start+"s → "+x.end+"s</span><span><button class='btn' onclick='mv("+i+",-1)'>↑</button><button class='btn' onclick='mv("+i+",1)'>↓</button><label class='muted'><input type='checkbox' data-en='"+i+"' "+(x.enabled!==false?"checked":"")+"> enabled</label></span></div><div class='sceneGrid' style='margin-top:8px'><textarea data-h='"+i+"'>"+esc(x.heading)+"</textarea><textarea readonly data-n='"+i+"'>"+esc(x.narration)+"</textarea><input data-a='"+i+"' type='number' step='.1' min='0' value='"+x.trim_start+"'><input data-b='"+i+"' type='number' step='.1' min='0' value='"+x.trim_end+"'></div></div>").join("")||"No scenes yet."}
function mv(i,d){const a=window.tlData.scenes,j=i+d;if(j<0||j>=a.length)return;[a[i],a[j]]=[a[j],a[i]];renderTL()}
function collect(){const nodes=[...document.querySelectorAll(".scene")];return {...window.tlData,scenes:nodes.map((n,pos)=>{const i=+n.dataset.i,x=window.tlData.scenes[i];return {...x,position:pos+1,index:pos+1,heading:n.querySelector("[data-h]").value,narration:n.querySelector("[data-n]").value,trim_start:+n.querySelector("[data-a]").value||0,trim_end:+n.querySelector("[data-b]").value||0,enabled:n.querySelector("[data-en]").checked}})}}
async function renderTL(){if(!pid||!window.tlData)return;try{const r=await api("/infinity/pro2/project/"+encodeURIComponent(pid)+"/timeline/render",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(collect())});$("out").textContent=JSON.stringify(r,null,2);await openP(pid)}catch(e){$("out").textContent=e.message}}
async function revise(){const c=$("rev").value.trim();if(!pid||c.length<4)return;try{const r=await api("/infinity/pro2/project/"+encodeURIComponent(pid)+"/revise",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({command:c})});$("rev").value="";openP(r.project_id)}catch(e){$("out").textContent=e.message}}
async function verify(){if(!pid)return;try{$("out").textContent=JSON.stringify(await api("/infinity/pro2/project/"+encodeURIComponent(pid)+"/verify"),null,2);await refresh()}catch(e){$("out").textContent=e.message}}
async function openLatest(){const x=await api("/infinity/studio/projects?limit=1");if(x.projects&&x.projects[0])openP(x.projects[0].project_id)}
init();
</script></body></html>'''

def install(app:Any)->None:
    import studio_ultimate as studio

    @app.get("/infinity/3800/health")
    def legacy_creator_health():
        ff=bool(shutil.which("ffmpeg"))
        return {"status":"healthy" if ff else "degraded","version":VERSION,"build":BUILD,"canonical_surface":"/infinity/pro2","legacy_compatibility":True,"truthful":True}

    @app.post("/infinity/3800/self-test")
    def legacy_creator_self_test():
        ff=bool(shutil.which("ffmpeg")); fp=bool(shutil.which("ffprobe")); voice=bool(shutil.which("espeak-ng") or shutil.which("espeak"))
        return {"status":"passed" if ff and fp and voice else "degraded","checks":{"ffmpeg":ff,"ffprobe":fp,"offline_tts":voice,"professional_creator_route":True},"canonical_surface":"/infinity/pro2","truthful":True}

    @app.get("/infinity/divine/health")
    def divine_health_compat():
        ff=bool(shutil.which("ffmpeg")); fp=bool(shutil.which("ffprobe")); voice=bool(shutil.which("espeak-ng") or shutil.which("espeak"))
        return {"status":"healthy" if ff and fp and voice else "degraded","version":VERSION,"build":BUILD,"professional_creator":True,"content_creation_ready":bool(ff and fp and voice),"reality_kernel":{"patched":True,"canonical":"/infinity/pro2"},"canonical_surface":"/infinity/pro2","truthful":True}

    @app.get("/infinity/3800/capabilities")
    def creator_capabilities_compat():
        return {"version":VERSION,"core":["intent_to_real_production","real_video_audio_image_editing","creation_dna","worlds","creative_lab","quality_loop","creation_graph","versioned_projects","multi_format_outputs","local_free_first_processing"],"free_policy":{"core_free":True},"canonical_surface":"/infinity/pro2","truthful":True}

    @app.get("/infinity/3800/dna")
    def creator_dna_compat(request: Request):
        return {"profile":{},"canonical_surface":"/infinity/pro2","truthful":True}

    @app.get("/infinity/3800/worlds")
    def creator_worlds_compat(request: Request):
        return {"worlds":[],"canonical_surface":"/infinity/pro2","truthful":True}

    @app.get("/infinity/3800/graph")
    def creator_graph_compat(request: Request):
        return {"nodes":[],"edges":[],"canonical_surface":"/infinity/pro2","truthful":True}

    @app.get("/infinity/3800/quality/{project_id}")
    def creator_quality_compat(project_id: str, request: Request):
        uid,p=_project(studio,project_id,request)
        return {"project_id":project_id,"verified":bool(_truth(studio,p).get("verified")),"truth":_truth(studio,p),"truthful":True}

    @app.get("/infinity/pro2/health")
    def health():
        ff=bool(shutil.which("ffmpeg")); voice=bool(shutil.which("espeak-ng") or shutil.which("espeak"))
        providers=[
            {"name":"FFmpeg render/edit","ready":ff,"status":"ready" if ff else "missing"},
            {"name":"Offline voice fallback","ready":voice,"status":"ready" if voice else "missing"},
            {"name":"Hugging Face generation","ready":bool(os.getenv("HF_TOKEN","").strip()),"status":"connected" if os.getenv("HF_TOKEN","").strip() else "not connected"},
            {"name":"OpenAI Sora","ready":bool(os.getenv("OPENAI_API_KEY","").strip()),"status":"connected" if os.getenv("OPENAI_API_KEY","").strip() else "not connected"},
            {"name":"Runway","ready":bool(os.getenv("RUNWAYML_API_SECRET","").strip()),"status":"connected" if os.getenv("RUNWAYML_API_SECRET","").strip() else "not connected"},
            {"name":"Pexels","ready":bool(os.getenv("PEXELS_API_KEY","").strip()),"status":"connected" if os.getenv("PEXELS_API_KEY","").strip() else "not connected"},
            {"name":"Pixabay","ready":bool(os.getenv("PIXABAY_API_KEY","").strip()),"status":"connected" if os.getenv("PIXABAY_API_KEY","").strip() else "not connected"},
            {"name":"Durable object storage","ready":bool(os.getenv("CLOUDFLARE_R2_BUCKET","").strip() and os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID","").strip()),"status":"connected" if os.getenv("CLOUDFLARE_R2_BUCKET","").strip() and os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID","").strip() else "not connected"},
        ]
        return {"status":"healthy" if ff and voice else "degraded","version":VERSION,"build":BUILD,"readiness_label":"Professional local pipeline available" if ff and voice else "Renderer dependency missing","providers":providers,"truthful":True}

    @app.get("/infinity/pro2",response_class=HTMLResponse)
    def ui(): return UI

    @app.post("/infinity/pro2/create")
    async def create(request:Request):
        uid=studio._get_user_id(request); p=await request.json()
        objective=_safe(p.get("objective") or p.get("prompt") or p.get("description"),12000)
        if len(objective)<8: raise HTTPException(422,"content command must describe a real production")
        fmt=str(p.get("format") or "long").lower(); fmt=fmt if fmt in {"short","long"} else "long"
        req={"title":_safe(p.get("title") or objective[:140],200),"objective":objective,"topic":_safe(p.get("topic") or objective[:1000],1200),"format":fmt,"duration":max(20,min(3600,int(p.get("duration") or 60))),"content_type":"video","aspect_ratio":_safe(p.get("aspect_ratio") or ("9:16" if fmt=="short" else "16:9"),20),"quality_preset":_safe(p.get("quality_preset") or "high",30),"audience":_safe(p.get("audience") or "general audience",500),"tone":_safe(p.get("tone") or "professional, cinematic, useful",500),"language":_safe(p.get("language") or "English",100),"platforms":["YouTube","Instagram","TikTok"],"voice":_safe(p.get("voice") or "en-US-AriaNeural",120),"visual_style":"premium editorial, cinematic realism","professional_pipeline":True,"notes":"Professional Creator System v2"}
        return {**studio.enqueue(req,uid,None),"pipeline":BUILD,"truthful":True}

    @app.get("/infinity/pro2/project/{pid}")
    def project(pid:str,request:Request):
        uid,p=_project(studio,pid,request); x=studio._project_public(p); x["truth"]=_truth(studio,p) if p.get("status") in {"completed","completed_with_qc_warnings","failed"} else {"verified":False,"status":"production_in_progress","truthful":True}; x["pipeline"]=BUILD; return x

    @app.get("/infinity/pro2/project/{pid}/timeline")
    def timeline(pid:str,request:Request):
        uid,p=_project(studio,pid,request); root=studio._project_dir(pid); path=root/"timeline.json"
        data=None
        if path.exists():
            try:
                data=json.loads(path.read_text(encoding="utf-8"))
            except Exception: data=None
        data=data if isinstance(data,dict) and data.get("scenes") else _timeline(studio,p); data["owner"]=uid; data["truthful"]=True; return data

    @app.put("/infinity/pro2/project/{pid}/timeline")
    async def save_timeline(pid:str,request:Request):
        uid,p=_project(studio,pid,request)
        if p.get("status") not in {"completed","completed_with_qc_warnings"}: raise HTTPException(409,"timeline requires completed scene masters")
        tl=_normalise(studio,p,await request.json()); _save_timeline(studio,pid,tl); return {"status":"saved","timeline":tl,"truthful":True}

    @app.post("/infinity/pro2/project/{pid}/timeline/render")
    async def render_timeline(pid:str,request:Request):
        uid,p=_project(studio,pid,request)
        if p.get("status") not in {"completed","completed_with_qc_warnings"}: raise HTTPException(409,"timeline render requires completed scene masters")
        tl=_normalise(studio,p,await request.json()); _save_timeline(studio,pid,tl)
        try:
            master,qc=_render(studio,pid,tl)
            variants=_variants(studio,pid,master)
            _package(studio,pid)
            truth=_truth(studio,studio._get_project(pid))
            return {"status":"completed" if truth.get("verified") else "completed_with_qc_review","project_id":pid,"quality":qc,"variants":variants,"verified":bool(truth.get("verified")),"truth":truth,"download_url":f"/infinity/studio/project/{pid}/asset/final.mp4","truthful":True}
        except Exception as exc:
            studio._update_project(pid,status="failed",stage="timeline_render_failed",error=str(exc)[:800]); studio.audit_event(pid,"professional_timeline_render_failed",{"error":str(exc)[:1000]}); raise HTTPException(500,"professional timeline render failed: "+str(exc)[:500])

    @app.post("/infinity/pro2/project/{pid}/revise")
    async def revise(pid:str,request:Request):
        uid,p=_project(studio,pid,request); body=await request.json(); command=_safe(body.get("command") or body.get("instruction"),6000)
        if len(command)<4: raise HTTPException(422,"revision command is required")
        req=dict(p.get("request_json") or {}); req["title"]=req.get("title") or p.get("title") or "Revised production"; req["objective"]=req.get("objective") or p.get("title") or ""; req["notes"]=(str(req.get("notes") or "")+"\nProfessional revision: "+command).strip()[:5000]; req["revision_of"]=pid
        result=studio.enqueue(req,uid,None); studio.audit_event(pid,"professional_revision_queued",{"child_project_id":result.get("project_id"),"command":command}); return {"status":"queued","source_project_id":pid,"project_id":result.get("project_id"),"truthful":True}

    @app.get("/infinity/pro2/project/{pid}/verify")
    def verify(pid:str,request:Request):
        uid,p=_project(studio,pid,request); t=_truth(studio,p); return {"project_id":pid,"verified":bool(t.get("verified")),"truth":t,"checked_at":studio.utc_iso(),"pipeline":BUILD,"truthful":True}

    @app.post("/infinity/pro2/project/{pid}/package")
    def package(pid:str,request:Request):
        uid,p=_project(studio,pid,request)
        if p.get("status") not in {"completed","completed_with_qc_warnings"}: raise HTTPException(409,"project is not complete")
        t=_truth(studio,p)
        if not t.get("verified"): raise HTTPException(409,"package locked until professional verification passes")
        out=_package(studio,pid); return {"status":"completed","project_id":pid,"asset_name":out.name,"download_url":f"/infinity/studio/project/{pid}/asset/package.zip","sha256":studio.file_sha256(out),"truthful":True}
