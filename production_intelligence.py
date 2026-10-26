from __future__ import annotations

"""AI Infinity TARGET-2050.4001 — Production Intelligence Layer.

Adds real provider-backed research, multimodal visual review, timestamped voice,
sound-effect generation and candidate auto-selection to the production graph.
No provider credential => truthful not_configured response.
"""

import base64, json, mimetypes, os, re, sqlite3, subprocess, time, uuid
import api_anything_connector
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional
import requests
from fastapi import HTTPException, Request

VERSION="TARGET-2050.4001"
BUILD="PRODUCTION-INTELLIGENCE-VISION-VOICE-RESEARCH-QC"
LOCK=None

def pg():
    import production_graph
    return production_graph

def studio():
    import studio_ultimate
    return studio_ultimate

def db():
    c=sqlite3.connect(pg().dbpath(),timeout=30,check_same_thread=False)
    c.row_factory=sqlite3.Row
    return c

def uid(request):
    try:return studio()._get_user_id(request)
    except Exception:return "anonymous"

def own(pid,request):
    p=studio()._get_project(pid)
    if not p or p.get("user_id")!=uid(request):raise HTTPException(404,"project not found")
    return p

def provider_status():
    return {
      "version":VERSION,"build":BUILD,
      "research":{
        "tavily":bool(os.getenv("TAVILY_API_KEY","").strip()),
        "brave":bool(os.getenv("BRAVE_SEARCH_API_KEY","").strip()),
        "api_anything":api_anything_connector.status(),
        "fallback":"Wikipedia + DuckDuckGo"
      },
      "vision":{"openai":bool(os.getenv("OPENAI_API_KEY","").strip()),"model":os.getenv("AI_INFINITY_VISION_MODEL","gpt-5.6-luna")},
      "voice":{"elevenlabs":bool(os.getenv("ELEVENLABS_API_KEY","").strip()),"model":os.getenv("AI_INFINITY_ELEVEN_MODEL","eleven_multilingual_v2")},
      "sound_effects":{"elevenlabs":bool(os.getenv("ELEVENLABS_API_KEY","").strip())},
      "truthful":True
    }

def parse_output_text(data):
    if isinstance(data,dict) and data.get("output_text"):return str(data["output_text"])
    out=(data.get("output") or []) if isinstance(data,dict) else []
    parts=[]
    for item in out:
        for c in item.get("content") or []:
            if isinstance(c,dict) and c.get("type") in {"output_text","text"} and c.get("text"):
                parts.append(str(c["text"]))
    return "\n".join(parts).strip()

def parse_json_text(text):
    t=str(text or "").strip()
    m=re.search(r"\{.*\}",t,re.S)
    if m:t=m.group(0)
    try:return json.loads(t)
    except Exception:return {"raw":t[:8000],"parse_error":True}

def research(topic,limit=10):
    topic=re.sub(r"\s+"," ",str(topic or "").strip())[:500]
    limit=max(1,min(20,int(limit or 10)))
    if not topic:raise HTTPException(422,"topic required")
    # Honor the canonical runtime policy before calling any remote research provider.
    policy_check=getattr(studio(),"_external_providers_disabled",None)
    if callable(policy_check) and policy_check():
        blocked=studio().research_topic(topic,limit=min(limit,10))
        if not isinstance(blocked,dict):blocked={}
        blocked.update({
            "topic":topic,
            "provider":"disabled_by_policy",
            "providers_used":[],
            "api_anything":{"status":"disabled_by_policy","items_added":0,"operation":"youtube.search","truthful":True},
            "truthful":True,
        })
        return blocked
    sources=[]
    key=os.getenv("TAVILY_API_KEY","").strip()
    if key:
        try:
            r=requests.post("https://api.tavily.com/search",
                headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
                json={"query":topic,"search_depth":"advanced","max_results":min(limit,20),"include_answer":False},
                timeout=35)
            r.raise_for_status()
            for x in r.json().get("results") or []:
                sources.append({"source":"Tavily","title":x.get("title"),"url":x.get("url"),"summary":x.get("content") or x.get("snippet") or "","score":x.get("score"),"published_at":x.get("published_date")})
        except Exception as e:
            sources.append({"source":"Tavily","error":str(e)[:500]})
    key=os.getenv("BRAVE_SEARCH_API_KEY","").strip()
    if key and len([x for x in sources if not x.get("error")]) < limit:
        try:
            r=requests.get("https://api.search.brave.com/res/v1/web/search",
                headers={"Accept":"application/json","Accept-Encoding":"gzip","X-Subscription-Token":key},
                params={"q":topic,"count":min(20,max(1,limit))},
                timeout=25)
            r.raise_for_status()
            for x in (r.json().get("web") or {}).get("results") or []:
                sources.append({"source":"Brave Search","title":x.get("title"),"url":x.get("url"),"summary":x.get("description") or x.get("snippet") or "","published_at":x.get("age")})
        except Exception as e:
            sources.append({"source":"Brave Search","error":str(e)[:500]})
    # Supplemental competitor-video discovery through a pinned, bundled, read-only operation.
    # The connector uses a fresh private CLI home, validates the bundled spec, and forces tier 1.
    api_anything_result=api_anything_connector.search_candidates(topic,limit=min(limit,10))
    for item in (api_anything_result.get("sources") or []):
        if not isinstance(item,dict) or not item.get("url") or not item.get("title"):
            continue
        source_name=str(item.get("source") or "")
        if source_name not in {"API Anything / YouTube", "API Anything / Hacker News"}:
            continue
        sources.append({
            "source":source_name,
            "title":str(item.get("title") or "")[:300],
            "url":str(item.get("url") or "")[:1000],
            "summary":(
                "Channel: "+str(item.get("channel") or "unknown")[:160]+"; views: "+str(item.get("views") or "unknown")[:80]
                if source_name=="API Anything / YouTube"
                else "Hacker News discussion; points: "+str(item.get("points") or "unknown")[:40]+"; comments: "+str(item.get("comments") or "unknown")[:40]
            ),
            "retrieved_at":studio().utc_iso(),
        })
    clean=[]
    seen=set()
    for x in sources:
        if x.get("error"):continue
        u=str(x.get("url") or "")
        t=str(x.get("title") or "")
        k=(u,t)
        if k in seen or not (u or t):continue
        seen.add(k);clean.append(x)
    if not clean:
        try:
            legacy=studio().research_topic(topic,limit=min(limit,10))
            clean=[x for x in legacy.get("sources") or [] if not x.get("error")]
            provider="legacy" if clean else "none"
        except Exception:
            clean=[]
            provider="none"
    else:
        used=[]
        if any(x.get("source")=="Tavily" for x in clean): used.append("tavily")
        if any(x.get("source")=="Brave Search" for x in clean): used.append("brave")
        if any(x.get("source")=="API Anything / YouTube" for x in clean): used.append("api-anything-youtube")
        if any(x.get("source")=="API Anything / Hacker News" for x in clean): used.append("api-anything-hacker-news")
        provider="+".join(used) or "unknown"
    providers_used=sorted({str(x.get("source") or "unknown") for x in clean})
    return {"topic":topic,"provider":provider,"providers_used":providers_used,"api_anything":{"status":api_anything_result.get("status","unknown"),"items_added":sum(1 for x in clean if x.get("source") in {"API Anything / YouTube","API Anything / Hacker News"}),"operations":api_anything_result.get("operations",{}),"truthful":True},"source_count":len(clean),"sources":clean[:limit],"generated_at":studio().utc_iso(),"truthful":True}

def frame_image(path:Path)->tuple[Path,Optional[Path]]:
    if path.suffix.lower() in {".png",".jpg",".jpeg",".webp"}:return path,None
    ff="ffmpeg"
    if subprocess.run([ff,"-version"],capture_output=True).returncode!=0:raise RuntimeError("ffmpeg unavailable")
    out=path.with_name(path.stem+"_vision_frame.jpg")
    subprocess.run([ff,"-y","-ss","1","-i",str(path),"-frames:v","1","-vf","scale=1280:-2",str(out)],check=True,timeout=90,capture_output=True)
    return out,out

def openai_vision(path:Path,shot:Dict[str,Any])->Dict[str,Any]:
    key=os.getenv("OPENAI_API_KEY","").strip()
    if not key:return {"status":"not_configured","provider":"openai","truthful":True}
    frame,temporary=frame_image(path)
    try:
        mime=mimetypes.guess_type(str(frame))[0] or "image/jpeg"
        b64=base64.b64encode(frame.read_bytes()).decode()
        prompt={
          "role":"strict senior film editor and visual QC director",
          "shot":{"purpose":shot.get("purpose"),"duration":shot.get("duration"),"narration":shot.get("narration"),"visual_prompt":shot.get("visual_prompt"),"metadata":shot.get("metadata")},
          "task":"Judge this actual generated frame against the shot intent. Do not reward technical existence alone.",
          "return_json":{
            "narration_alignment":"0-100",
            "composition":"0-100",
            "visual_quality":"0-100",
            "cinematography":"0-100",
            "continuity":"0-100",
            "brand_match":"0-100",
            "artifact_free":"0-100",
            "specific_issues":["string"],
            "revision_prompt":"string",
            "decision":"accept|review|reject"
          }
        }
        payload={"model":os.getenv("AI_INFINITY_VISION_MODEL","gpt-5.6-luna"),
                 "input":[{"role":"user","content":[
                   {"type":"input_text","text":"Return JSON only.\n"+json.dumps(prompt,ensure_ascii=False)},
                   {"type":"input_image","image_url":f"data:{mime};base64,{b64}","detail":"high"}
                 ]}]}
        r=requests.post("https://api.openai.com/v1/responses",
            headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
            json=payload,timeout=150)
        r.raise_for_status()
        parsed=parse_json_text(parse_output_text(r.json()))
        parsed["provider"]="openai"
        parsed["model"]=payload["model"]
        parsed["truthful"]=True
        return parsed
    finally:
        if temporary and temporary.exists():
            try:temporary.unlink()
            except Exception:pass

def vision_review(pid,cid,user):
    with db() as c:
        row=c.execute("SELECT * FROM pg_candidates WHERE candidate_id=? AND project_id=?",(cid,pid)).fetchone()
        shot=c.execute("SELECT * FROM pg_shots WHERE shot_id=?",(row["shot_id"],)).fetchone() if row else None
    if not row:raise HTTPException(404,"candidate not found")
    path=Path(row["asset_path"] or "")
    tech=pg().tech(path)
    visual=openai_vision(path,dict(shot or {}))
    nums=[]
    for k in ("narration_alignment","composition","visual_quality","cinematography","continuity","brand_match","artifact_free"):
        try:
            v=float(visual.get(k)); nums.append(v)
        except Exception:pass
    score=round(sum(nums)/len(nums),1) if nums else float(tech.get("score") or 0)
    if visual.get("status")=="not_configured": score=float(tech.get("score") or 0)
    decision="accept" if score>=80 else ("review" if score>=60 else "reject")
    rid="rev_"+uuid.uuid4().hex
    report={"technical":tech,"vision":visual,"score":score,"decision":decision,"truthful":True}
    with db() as c:
        c.execute("INSERT INTO pg_reviews(review_id,project_id,shot_id,candidate_id,reviewer,score,decision,report_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (rid,pid,row["shot_id"],cid,"multimodal-qc" if visual.get("provider") else "technical-qc",score,decision,json.dumps(report,ensure_ascii=False,sort_keys=True),time.time()))
        c.execute("UPDATE pg_candidates SET score=?,critic_json=?,status=?,updated_at=? WHERE candidate_id=?",
                  (score,json.dumps(report,ensure_ascii=False,sort_keys=True),"accepted" if decision=="accept" else "reviewed",time.time(),cid))
    return {"review_id":rid,"candidate_id":cid,"score":score,"decision":decision,"report":report,"truthful":True}

def autoselect(pid,user,threshold=80):
    with db() as c:
        shots=c.execute("SELECT * FROM pg_shots WHERE project_id=? ORDER BY seq",(pid,)).fetchall()
        result=[]
        for sh in shots:
            rows=c.execute("SELECT * FROM pg_candidates WHERE shot_id=? AND status IN ('ready','reviewed','accepted') ORDER BY created_at DESC",(sh["shot_id"],)).fetchall()
            ranked=[]
            for row in rows:
                if row["score"] is not None: ranked.append((float(row["score"]),row))
                elif row["asset_path"]: ranked.append((float(pg().tech(Path(row["asset_path"])).get("score") or 0),row))
            ranked.sort(key=lambda x:x[0],reverse=True)
            winner=None
            if ranked and ranked[0][0]>=threshold:
                winner=ranked[0][1]
                c.execute("UPDATE pg_candidates SET status='accepted',updated_at=? WHERE candidate_id=?",(time.time(),winner["candidate_id"]))
                c.execute("UPDATE pg_shots SET winner_candidate_id=?,critic_score=?,status='approved',updated_at=? WHERE shot_id=?",(winner["candidate_id"],ranked[0][0],time.time(),sh["shot_id"]))
            result.append({"shot_id":sh["shot_id"],"candidate_count":len(ranked),"winner":winner["candidate_id"] if winner else None,"score":ranked[0][0] if ranked else None})
    return {"project_id":pid,"threshold":threshold,"shots":result,"truthful":True}

def char_srt(text_value,alignment):
    chars=list(alignment.get("characters") or [])
    starts=list(alignment.get("character_start_times_seconds") or [])
    ends=list(alignment.get("character_end_times_seconds") or [])
    if not chars:return ""
    words=[]
    start_i=0
    for m in re.finditer(r"\S+",text_value):
        a,b=m.span()
        idx=[i for i in range(a,min(b,len(starts))) if i<len(ends) and i<len(chars)]
        if idx:
            words.append((m.group(0),float(starts[idx[0]]),float(ends[idx[-1]])))
    cues=[]
    for i in range(0,len(words),8):
        chunk=words[i:i+8]
        if not chunk:continue
        st,et=chunk[0][1],chunk[-1][2]
        lines=[];line=""
        for w,_,_ in chunk:
            if line and len(line)+1+len(w)>42:lines.append(line);line=w
            else:line=(line+" "+w).strip()
        if line:lines.append(line)
        cues.append((st,et,"\n".join(lines[:2])))
    def ts(v):
        ms=int(round((v-int(v))*1000));sec=int(v)
        return f"{sec//3600:02}:{(sec%3600)//60:02}:{sec%60:02},{ms:03}"
    return "\n\n".join(f"{i+1}\n{ts(a)} --> {ts(b)}\n{t}" for i,(a,b,t) in enumerate(cues))+"\n"

def eleven_voice(pid,user,text_value,voice_id):
    key=os.getenv("ELEVENLABS_API_KEY","").strip()
    if not key:return {"status":"not_configured","required_secret":"ELEVENLABS_API_KEY","truthful":True}
    r=requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
      headers={"xi-api-key":key,"Content-Type":"application/json"},
      params={"output_format":"mp3_44100_128"},
      json={"text":text_value,"model_id":os.getenv("AI_INFINITY_ELEVEN_MODEL","eleven_multilingual_v2")},
      timeout=180)
    r.raise_for_status(); data=r.json(); root=Path(os.getenv("AI_INFINITY_DATA_DIR","/tmp/ai-infinity"))/"production-intelligence"/pid;root.mkdir(parents=True,exist_ok=True)
    stem="voice_"+uuid.uuid4().hex[:12];audio=root/(stem+".mp3");srt=root/(stem+".srt")
    audio.write_bytes(base64.b64decode(data.get("audio_base64") or ""));srt.write_text(char_srt(text_value,data.get("alignment") or {}),encoding="utf-8")
    try:
        studio()._save_asset(pid,"voice",audio,"audio/mpeg",{"provider":"elevenlabs","model":os.getenv("AI_INFINITY_ELEVEN_MODEL","eleven_multilingual_v2"),"voice_id":voice_id})
        studio()._save_asset(pid,"captions",srt,"application/x-subrip",{"provider":"elevenlabs","timing":"character-alignment"})
        studio().register_artifact(pid,audio,"audio/mpeg",{"provider":"elevenlabs"})
        studio().register_artifact(pid,srt,"application/x-subrip",{"provider":"elevenlabs","timing":"character-alignment"})
    except Exception:pass
    return {"status":"completed","provider":"elevenlabs","audio_path":str(audio),"captions_path":str(srt),"request_id":r.headers.get("request-id"),"truthful":True}

def eleven_sfx(pid,prompt,duration=3):
    key=os.getenv("ELEVENLABS_API_KEY","").strip()
    if not key:return {"status":"not_configured","required_secret":"ELEVENLABS_API_KEY","truthful":True}
    duration=max(0.5,min(30,float(duration)))
    r=requests.post("https://api.elevenlabs.io/v1/sound-generation",
      headers={"xi-api-key":key,"Content-Type":"application/json"},
      params={"output_format":"mp3_44100_128"},
      json={"text":clean(prompt,1600),"duration_seconds":duration,"model_id":"eleven_text_to_sound_v2","prompt_influence":0.55},
      timeout=180);r.raise_for_status()
    root=Path(os.getenv("AI_INFINITY_DATA_DIR","/tmp/ai-infinity"))/"production-intelligence"/pid;root.mkdir(parents=True,exist_ok=True);p=root/("sfx_"+uuid.uuid4().hex[:12]+".mp3");p.write_bytes(r.content)
    return {"status":"completed","provider":"elevenlabs","path":str(p),"duration_seconds":duration,"truthful":True}

def install_creator_research_connector():
    """Augment the existing Creator Studio research function without replacing its providers."""
    target = studio()
    original = getattr(target, "research_topic", None)
    if not callable(original) or getattr(original, "_api_anything_research_wrapped", False):
        return {"status": "already_installed" if callable(original) else "unavailable", "truthful": True}

    def enriched_research_topic(topic, limit=10):
        result = original(topic, limit=limit)
        if not isinstance(result, dict):
            return result
        providers = result.get("providers")
        if isinstance(providers, dict) and providers and all(
            isinstance(item, dict) and item.get("status") == "disabled_by_policy"
            for item in providers.values()
        ):
            return result

        per = max(1, min(10, int(limit or 10)))
        connector_result = api_anything_connector.search_candidates(topic, limit=per)
        items = connector_result.get("sources") if isinstance(connector_result, dict) else []
        items = items if isinstance(items, list) else []
        sources = result.get("sources")
        if not isinstance(sources, list):
            sources = []
            result["sources"] = sources
        existing = {
            (str(item.get("url") or "").strip(), str(item.get("title") or "").strip().casefold())
            for item in sources if isinstance(item, dict)
        }
        added = 0
        added_by_source = {}
        relevant = getattr(target, "_research_source_relevant", None)
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()[:300]
            source_name = str(item.get("source") or "")
            if not title or not url.startswith("https://"):
                continue
            if source_name == "API Anything / YouTube":
                video_id = str(item.get("id") or "").strip()
                if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
                    continue
                if url != f"https://www.youtube.com/watch?v={video_id}":
                    continue
                row = {
                    "title": title, "url": url, "source": source_name,
                    "summary": "Channel: " + str(item.get("channel") or "unknown")[:160]
                    + "; views: " + str(item.get("views") or "unknown")[:80],
                    "video_id": video_id,
                    "channel": str(item.get("channel") or "")[:160],
                    "views": str(item.get("views") or "")[:80],
                    "retrieved_at": studio().utc_iso(),
                }
            elif source_name == "API Anything / Hacker News":
                parsed = urlparse(url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    continue
                host = parsed.hostname.rstrip(".").lower()
                if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".localhost")):
                    continue
                row = {
                    "title": title, "url": url, "source": source_name,
                    "summary": "Hacker News discussion; points: " + str(item.get("points") or "unknown")[:40]
                    + "; comments: " + str(item.get("comments") or "unknown")[:40],
                    "external_id": str(item.get("id") or "")[:80],
                    "author": str(item.get("author") or "")[:120],
                    "points": str(item.get("points") or "")[:40],
                    "comments": str(item.get("comments") or "")[:40],
                    "retrieved_at": studio().utc_iso(),
                }
            else:
                continue
            key = (url, title.casefold())
            if key in existing or (callable(relevant) and not relevant(str(topic or ""), row)):
                continue
            existing.add(key)
            sources.append(row)
            added += 1
            added_by_source[source_name] = added_by_source.get(source_name, 0) + 1
            if len(sources) >= per * 2:
                break

        provider_map = result.setdefault("providers", {})
        if isinstance(provider_map, dict):
            operation_statuses = connector_result.get("operations", {}) if isinstance(connector_result, dict) else {}
            for capability, source_name in (
                ("youtube.search", "API Anything / YouTube"),
                ("hacker-news.search", "API Anything / Hacker News"),
            ):
                count = added_by_source.get(source_name, 0)
                status_value = str(operation_statuses.get(capability) or "unknown")
                provider_map[source_name] = {
                    "status": "ready" if count else status_value,
                    "count": count,
                    "connector_status": status_value,
                    "operation": capability,
                    "transport_mode": "bundled_read_only_plain_http",
                    "truthful": True,
                }
        result["source_count"] = len(sources)
        result["provider_count"] = len({str(item.get("source") or "unknown") for item in sources if isinstance(item, dict)})
        result["degraded"] = not bool(sources)
        result["status"] = "READY" if sources else "DEGRADED"
        result["research_connector"] = {
            "api_anything_status": str(connector_result.get("status") or "unknown") if isinstance(connector_result, dict) else "failed",
            "items_added": added,
            "operations": connector_result.get("operations", {}) if isinstance(connector_result, dict) else {},
            "truthful": True,
        }
        policy = result.get("relevance_policy")
        if isinstance(policy, dict):
            retrieved_count = int(policy.get("retrieved_sources") or 0) + len(items)
            policy["accepted_sources"] = len(sources)
            policy["retrieved_sources"] = retrieved_count
            policy["rejected_irrelevant_sources"] = max(0, retrieved_count - len(sources))
        return result

    enriched_research_topic._api_anything_research_wrapped = True
    enriched_research_topic._api_anything_original = original
    target.research_topic = enriched_research_topic
    return {"status": "installed", "truthful": True}


def register(app):
    install_creator_research_connector()
    @app.get("/infinity/production/intelligence")
    def intelligence_status():return provider_status()
    @app.post("/infinity/production/research")
    async def research_route(request:Request):
        d=await request.json();return research(d.get("topic"),int(d.get("limit") or 10))
    @app.post("/infinity/production/project/{pid}/vision-review/{cid}")
    def vision_route(pid:str,cid:str,request:Request):
        own(pid,request);return vision_review(pid,cid,uid(request))
    @app.post("/infinity/production/project/{pid}/auto-select")
    async def select_route(pid:str,request:Request):
        own(pid,request);d=await request.json();return autoselect(pid,uid(request),float(d.get("threshold") or 80))
    @app.post("/infinity/production/project/{pid}/voice")
    async def voice_route(pid:str,request:Request):
        own(pid,request);d=await request.json();t=clean(d.get("text"),20000);v=clean(d.get("voice_id"),200)
        if not t or not v:raise HTTPException(422,"text and voice_id are required")
        return eleven_voice(pid,uid(request),t,v)
    @app.post("/infinity/production/project/{pid}/sfx")
    async def sfx_route(pid:str,request:Request):
        own(pid,request);d=await request.json();return eleven_sfx(pid,clean(d.get("prompt"),1600),float(d.get("duration") or 3))

