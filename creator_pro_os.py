"""AI Infinity Creator Pro extension layer.

Keeps the canonical Studio backend intact while adding deeper creator controls:
advanced local editing, storyboard editing, transcript-first project context,
open-source/local provider discovery, and a self-contained interactive web
experience generator.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse


def register_pro(app: Any) -> None:
    """Register creator-pro routes against the already-mounted Studio app."""
    from studio_ultimate import (
        DB_LOCK,
        _connect,
        _get_project,
        _get_user_id,
        _project_dir,
        _set_session,
        _save_asset,
        register_artifact,
        audit_event,
        file_sha256,
        probe_duration,
        ffmpeg,
        jdump,
        url_host_safe,
        now,
    )

    def owned_project(pid: str, request: Request, response: Any):
        uid = _get_user_id(request)
        _set_session(response, request, uid)
        p = _get_project(pid)
        if not p or p.get("user_id") != uid:
            raise HTTPException(404, "project not found")
        return uid, p

    def read_json_file(path: Path) -> Dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def project_blueprint(p: Dict[str, Any]) -> Dict[str, Any]:
        b = p.get("blueprint_json")
        if isinstance(b, dict):
            return b
        if isinstance(b, str):
            try:
                x = json.loads(b)
                return x if isinstance(x, dict) else {}
            except Exception:
                return {}
        return {}

    def safe_text(value: Any, limit: int = 6000) -> str:
        return str(value or "").replace("\x00", "").strip()[:limit]

    def run_local_http_probe(url: str, timeout: float = 4.0) -> Dict[str, Any]:
        # Intentionally use the standard library. No new runtime dependency is
        # required for local/open-source integrations.
        from urllib.request import Request as URequest, urlopen
        if not url.startswith("http://") and not url.startswith("https://"):
            return {"ok": False, "error": "endpoint must be http(s)"}
        if url.startswith("https://") and not url_host_safe(url):
            return {"ok": False, "error": "endpoint rejected by SSRF policy"}
        try:
            req = URequest(url, headers={"User-Agent": "AI-Infinity/Creator-Pro"}, method="GET")
            with urlopen(req, timeout=timeout) as r:
                body = r.read(1600).decode("utf-8", "replace")
                return {"ok": True, "status": int(getattr(r, "status", 200)), "preview": body[:800]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:400]}

    @app.get("/infinity/studio/creator-pro")
    def creator_pro_catalog(request: Request, response: Any):
        uid = _get_user_id(request)
        _set_session(response, request, uid)
        return {
            "surface": "AI Infinity Creator Pro",
            "editor": {
                "color": True, "brightness": True, "contrast": True, "saturation": True,
                "sharpen": True, "denoise": True, "fade": True, "audio_normalize": True,
                "audio_gain": True, "mute": True, "speed": True, "reframe": True,
                "watermark_text": True, "burn_captions": True
            },
            "workflow": {
                "storyboard_editing": True,
                "transcript_first_context": True,
                "interactive_html_export": True,
                "variant_matrix": True,
                "open_source_connectors": True
            },
            "free_first": True,
            "truthful": True
        }

    @app.get("/infinity/studio/open-source")
    def open_source_status(request: Request, response: Any):
        uid = _get_user_id(request)
        _set_session(response, request, uid)
        ff = shutil.which("ffmpeg") is not None
        voice = bool(shutil.which("espeak-ng") or shutil.which("espeak"))
        ollama = bool(os.getenv("OLLAMA_BASE_URL", "").strip())
        comfy = bool(os.getenv("COMFYUI_URL", "").strip())
        try:
            import faster_whisper  # type: ignore
            whisper = True
        except Exception:
            whisper = False
        return {
            "license_goal": "open-source application + local-first connectors",
            "built_in": [
                {"name":"FFmpeg","capability":"video render/edit","status":"ready" if ff else "missing"},
                {"name":"eSpeak-NG","capability":"offline TTS","status":"ready" if voice else "missing"},
            ],
            "connectors": [
                {"name":"Ollama","capability":"local LLM","status":"configured" if ollama else "not_configured","env":"OLLAMA_BASE_URL"},
                {"name":"ComfyUI","capability":"local image/video workflow engine","status":"configured" if comfy else "not_configured","env":"COMFYUI_URL"},
                {"name":"faster-whisper","capability":"local transcription + word timestamps + VAD","status":"installed" if whisper else "optional_not_installed"}
            ],
            "references": [
                {"name":"ComfyUI","url":"https://github.com/Comfy-Org/ComfyUI","scope":"local workflow execution"},
                {"name":"Open WebUI","url":"https://github.com/open-webui/open-webui","scope":"agentic local/cloud model UI"},
                {"name":"OpenShot","url":"https://www.openshot.org/","scope":"open-source NLE benchmark"},
                {"name":"faster-whisper","url":"https://github.com/SYSTRAN/faster-whisper","scope":"speech transcription"},
                {"name":"Remotion","url":"https://github.com/remotion-dev/remotion","scope":"programmatic video rendering"}
            ],
            "truthful": True
        }

    @app.post("/infinity/studio/open-source/test")
    async def open_source_test(request: Request, response: Any):
        uid = _get_user_id(request)
        _set_session(response, request, uid)
        payload = await request.json()
        provider = str(payload.get("provider") or "").strip().lower()
        endpoint = str(payload.get("endpoint") or "").strip()
        if provider == "ollama":
            endpoint = endpoint or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
            # Localhost probes are intentionally allowed for user-owned local engines.
            result = run_local_http_probe(endpoint.rstrip("/") + "/api/tags")
        elif provider == "comfyui":
            endpoint = endpoint or os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
            result = run_local_http_probe(endpoint.rstrip("/") + "/system_stats")
        elif provider == "whisper":
            try:
                import faster_whisper  # noqa: F401
                result = {"ok": True, "status": "installed"}
            except Exception as exc:
                result = {"ok": False, "status": "not_installed", "error": str(exc)[:300]}
        else:
            raise HTTPException(400, "unsupported open-source provider")
        return {"provider":provider,"endpoint":endpoint,"result":result,"free_first":True,"truthful":True}

    @app.get("/infinity/studio/project/{project_id}/storyboard")
    def storyboard_get(project_id: str, request: Request, response: Any):
        uid, p = owned_project(project_id, request, response)
        path = _project_dir(project_id) / "storyboard.json"
        if path.exists():
            data = read_json_file(path)
        else:
            b = project_blueprint(p)
            plan = b.get("plan") if isinstance(b.get("plan"), dict) else b
            chapters = (plan or {}).get("chapters") if isinstance(plan, dict) else []
            data = {
                "project_id": project_id,
                "title": p.get("title") or "",
                "scenes": [
                    {
                        "index": i + 1,
                        "heading": safe_text(ch.get("heading"), 240),
                        "narration": safe_text(ch.get("narration"), 4000),
                        "image_prompt": safe_text(ch.get("image_prompt"), 700),
                        "on_screen": safe_text(ch.get("on_screen"), 350),
                        "duration": float(ch.get("actual_duration") or ch.get("duration") or 0),
                    }
                    for i, ch in enumerate(chapters or [])
                ],
                "source": "production blueprint",
                "truthful": True,
            }
        data.setdefault("project_id", project_id)
        data["owner"] = uid
        data["truthful"] = True
        return data

    @app.put("/infinity/studio/project/{project_id}/storyboard")
    async def storyboard_put(project_id: str, request: Request, response: Any):
        uid, p = owned_project(project_id, request, response)
        payload = await request.json()
        scenes = payload.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise HTTPException(422, "storyboard must contain at least one scene")
        clean = []
        for i, raw in enumerate(scenes[:40], 1):
            if not isinstance(raw, dict):
                continue
            clean.append({
                "index": i,
                "heading": safe_text(raw.get("heading"), 240),
                "narration": safe_text(raw.get("narration"), 4000),
                "image_prompt": safe_text(raw.get("image_prompt"), 700),
                "on_screen": safe_text(raw.get("on_screen"), 350),
                "duration": max(1.0, min(600.0, float(raw.get("duration") or 6))),
            })
        if not clean:
            raise HTTPException(422, "storyboard contains no valid scenes")
        data={"project_id":project_id,"title":safe_text(payload.get("title") or p.get("title"),240),"scenes":clean,"updated_at":now(),"truthful":True}
        path=_project_dir(project_id)/"storyboard.json"
        path.write_text(jdump(data),encoding="utf-8")
        audit_event(project_id,"storyboard_updated",{"user_id":uid,"scene_count":len(clean)})
        return {"status":"saved","project_id":project_id,"storyboard":data,"truthful":True}

    @app.get("/infinity/studio/project/{project_id}/transcript")
    def transcript_context(project_id: str, request: Request, response: Any):
        uid, p = owned_project(project_id, request, response)
        root = _project_dir(project_id)
        srt = root / "captions.srt"
        script = root / "script.md"
        rows=[]
        if srt.exists():
            raw=srt.read_text(encoding="utf-8",errors="replace")
            blocks=re.split(r"\n\s*\n",raw)
            for block in blocks:
                ls=[x.strip("\ufeff ") for x in block.splitlines() if x.strip()]
                if len(ls)>=3 and re.match(r"\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+",ls[1]):
                    start,end=ls[1].split(" --> ",1)
                    txt=" ".join(ls[2:])
                    rows.append({"start":start,"end":end,"text":txt})
        if not rows and script.exists():
            lines=script.read_text(encoding="utf-8",errors="replace").splitlines()
            current=None
            for line in lines:
                if line.startswith("## "):
                    current={"section":line[3:].strip(),"text":[]}
                    rows.append(current)
                elif current and line.strip():
                    current["text"].append(line.strip())
            for r in rows:
                if isinstance(r.get("text"),list):
                    r["text"]=" ".join(r["text"])
        return {
            "project_id":project_id,
            "source":"captions.srt" if srt.exists() else ("script.md" if script.exists() else "none"),
            "segments":rows[:2000],
            "word_level_available": bool(safe_text(os.getenv("FASTER_WHISPER_MODEL"))),
            "editor_actions":["remove phrase","rewrite sentence","extract clip","tighten intro","change CTA"],
            "note":"This endpoint exposes the project's real generated transcript/context. Word-level local transcription becomes available when faster-whisper is installed/configured.",
            "truthful":True
        }

    @app.post("/infinity/studio/project/{project_id}/edit/advanced")
    async def advanced_edit(project_id: str, request: Request, response: Any):
        uid, p = owned_project(project_id, request, response)
        if p.get("status") not in {"completed","completed_with_qc_warnings"}:
            raise HTTPException(409,"project master is not ready")
        payload=await request.json()
        master=_project_dir(project_id)/"final.mp4"
        if not master.exists(): raise HTTPException(404,"final master not found")
        duration=probe_duration(master)
        try: trim_start=max(0.0,float(payload.get("trim_start") or 0))
        except Exception: trim_start=0.0
        try: trim_end=max(0.0,float(payload.get("trim_end") or 0))
        except Exception: trim_end=0.0
        try: speed=min(2.0,max(0.5,float(payload.get("speed") or 1)))
        except Exception: speed=1.0
        if trim_start+trim_end>=max(1.0,duration): raise HTTPException(422,"trim removes the whole master")
        allowed={"16:9":(1920,1080),"9:16":(1080,1920),"1:1":(1080,1080),"4:5":(1080,1350)}
        ratio=str(payload.get("aspect_ratio") or "original")
        vf=[]
        if ratio in allowed:
            w,h=allowed[ratio]
            anchor=str(payload.get("crop_anchor") or "center")
            xexpr="(iw-ow)/2"
            yexpr="(ih-oh)/2"
            if anchor=="top": yexpr="0"
            elif anchor=="bottom": yexpr="ih-oh"
            elif anchor=="left": xexpr="0"
            elif anchor=="right": xexpr="iw-ow"
            vf.append(f"scale={w}:{h}:force_original_aspect_ratio=increase")
            vf.append(f"crop={w}:{h}:{xexpr}:{yexpr}")
        def num(k, default, lo, hi):
            try:return max(lo,min(hi,float(payload.get(k) if payload.get(k) is not None else default)))
            except Exception:return default
        brightness=num("brightness",0,-1,1)
        contrast=num("contrast",1,0,3)
        saturation=num("saturation",1,0,3)
        sharpness=num("sharpness",0,0,2)
        denoise=bool(payload.get("denoise"))
        if any(abs(x) > 1e-9 for x in (brightness,contrast-1,saturation-1)) or sharpness>0 or denoise:
            vf.append(f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}:saturation={saturation:.4f}")
            if sharpness>0: vf.append(f"unsharp=5:5:{sharpness:.3f}:5:5:0")
            if denoise: vf.append("hqdn3d=1.5:1.5:6:6")
        if speed!=1:
            vf.append(f"setpts={1.0/speed:.8f}*PTS")
        fade_in=num("fade_in",0,0,30)
        fade_out=num("fade_out",0,0,30)
        if fade_in>0: vf.append(f"fade=t=in:st=0:d={fade_in:.3f}")
        if fade_out>0 and duration>fade_out: vf.append(f"fade=t=out:st={max(0,duration/speed-fade_out):.3f}:d={fade_out:.3f}")
        watermark=safe_text(payload.get("watermark_text"),160)
        if watermark:
            safe_wm=watermark.replace("\\","\\\\").replace("'","\\'").replace(":","\\:")
            vf.append(f"drawtext=text='{safe_wm}':x=w-tw-28:y=h-th-24:fontsize=28:fontcolor=white@0.85:box=1:boxcolor=black@0.45:boxborderw=10")
        af=[]
        mute=bool(payload.get("mute"))
        gain=num("audio_gain",1,0,3)
        if mute or abs(gain-1)>1e-9: af.append(f"volume={0 if mute else gain:.5f}")
        if bool(payload.get("audio_normalize")): af.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if speed!=1: af.append(f"atempo={speed:.8f}")
        burn=bool(payload.get("burn_captions"))
        if burn:
            caps=_project_dir(project_id)/"captions.srt"
            if caps.exists():
                sub=str(caps).replace("\\","/").replace(":","\\:")
                vf.append(f"subtitles={sub}:force_style='FontName=DejaVu Sans,FontSize=20,Outline=3,Shadow=1,MarginV=52,Alignment=2'")
        out=_project_dir(project_id)/(f"pro_edit_{int(time.time())}_{uuid.uuid4().hex[:8]}.mp4")
        args=[]
        if trim_start>0: args += ["-ss",trim_start]
        args += ["-i",master]
        if trim_start+trim_end<duration:
            args += ["-t",max(0.1,(duration-trim_start-trim_end)/speed)]
        if vf: args += ["-vf",",".join(vf)]
        if af: args += ["-af",",".join(af)]
        args += ["-map","0:v:0","-map","0:a?","-c:v","libx264","-preset",os.getenv("AI_INFINITY_VIDEO_PRESET","veryfast"),"-crf","18","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k","-movflags","+faststart",out]
        audit_event(project_id,"creator_pro_edit_started",{"user_id":uid,"options":{k:payload.get(k) for k in ["trim_start","trim_end","speed","aspect_ratio","crop_anchor","brightness","contrast","saturation","sharpness","denoise","fade_in","fade_out","watermark_text","audio_gain","audio_normalize","mute","burn_captions"]}})
        try:
            ffmpeg(*args,timeout=1200)
        except Exception as exc:
            out.unlink(missing_ok=True)
            audit_event(project_id,"creator_pro_edit_failed",{"error":str(exc)[:500]})
            raise HTTPException(500,"advanced edit failed: "+str(exc)[:300])
        meta={k:payload.get(k) for k in ["trim_start","trim_end","speed","aspect_ratio","crop_anchor","brightness","contrast","saturation","sharpness","denoise","fade_in","fade_out","watermark_text","audio_gain","audio_normalize","mute","burn_captions"]}
        meta.update({"source":"final.mp4","editor":"AI Infinity Creator Pro","duration_seconds":probe_duration(out),"sha256":file_sha256(out),"truthful":True})
        _save_asset(project_id,"pro_edit",out,"video/mp4",meta)
        art=register_artifact(project_id,out,"video/mp4",meta)
        audit_event(project_id,"creator_pro_edit_completed",{"asset":out.name,"sha256":art.get("sha256")})
        return {"status":"completed","project_id":project_id,"asset_name":out.name,"download_url":f"/infinity/studio/project/{project_id}/asset/{out.name}","metadata":meta,"truthful":True}

    @app.post("/infinity/studio/project/{project_id}/interactive")
    async def interactive_export(project_id: str, request: Request, response: Any):
        uid, p = owned_project(project_id, request, response)
        payload=await request.json()
        title=safe_text(payload.get("title") or p.get("title") or "AI Infinity Experience",120)
        headline=safe_text(payload.get("headline") or title,220)
        body=safe_text(payload.get("body") or payload.get("objective") or "Explore this creator project.",1800)
        cta=safe_text(payload.get("cta") or "Explore",80)
        sections=payload.get("sections") if isinstance(payload.get("sections"),list) else []
        clean=[]
        for s in sections[:12]:
            if isinstance(s,dict):
                clean.append({"title":safe_text(s.get("title"),120),"text":safe_text(s.get("text"),800)})
        if not clean:
            clean=[{"title":"The idea","text":body},{"title":"The story","text":"Research, narrative, media and publishing were assembled in one workspace."},{"title":"Next step","text":"Customize this experience, connect your channels and publish when ready."}]
        cards="".join(f'<article><h2>{_escape_html(x["title"])}</h2><p>{_escape_html(x["text"])}</p></article>' for x in clean)
        html_doc=f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="{_escape_html(body[:150])}"><title>{_escape_html(title)}</title><style>body{{margin:0;font:16px/1.6 system-ui;background:#070a0d;color:#f5f7fa}}main{{max-width:1080px;margin:auto;padding:8vh 6vw}}header{{min-height:58vh;display:grid;align-content:center}}.eyebrow{{letter-spacing:.2em;text-transform:uppercase;opacity:.6;font-size:12px}}h1{{font-size:clamp(46px,8vw,96px);line-height:.94;margin:.25em 0}}.cta{{display:inline-block;padding:12px 18px;border:1px solid #718097;border-radius:999px;color:inherit;text-decoration:none}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}}article{{padding:22px;border:1px solid #23303e;border-radius:20px;background:#0c1117}}footer{{opacity:.55;margin-top:12vh;font-size:12px}}</style></head><body><main><header><div class="eyebrow">AI Infinity · Creator experience</div><h1>{_escape_html(headline)}</h1><p>{_escape_html(body)}</p><a class="cta" href="#story">{_escape_html(cta)}</a></header><section id="story" class="grid">{cards}</section><footer>Built with AI Infinity · open-source application · generated locally</footer></main></body></html>"""
        out=_project_dir(project_id)/f"interactive_{uuid.uuid4().hex[:8]}.html"
        out.write_text(html_doc,encoding="utf-8")
        meta={"project_id":project_id,"title":title,"kind":"interactive_html","sections":len(clean),"size_bytes":out.stat().st_size,"sha256":file_sha256(out),"truthful":True}
        _save_asset(project_id,"interactive_html",out,"text/html",meta)
        register_artifact(project_id,out,"text/html",meta)
        audit_event(project_id,"interactive_export_completed",{"asset":out.name})
        return {"status":"completed","project_id":project_id,"asset_name":out.name,"download_url":f"/infinity/studio/project/{project_id}/asset/{out.name}","preview_url":f"/infinity/studio/project/{project_id}/asset/{out.name}","metadata":meta,"truthful":True}

    @app.post("/infinity/studio/project/{project_id}/variant-matrix")
    async def variant_matrix(project_id: str, request: Request, response: Any):
        uid, p = owned_project(project_id, request, response)
        if p.get("status") not in {"completed","completed_with_qc_warnings"}:
            raise HTTPException(409,"project master is not ready")
        master=_project_dir(project_id)/"final.mp4"
        if not master.exists(): raise HTTPException(404,"final master not found")
        payload=await request.json()
        ratios=payload.get("ratios") if isinstance(payload.get("ratios"),list) else ["9:16","1:1","4:5","16:9"]
        allowed={"16:9":(1920,1080),"9:16":(1080,1920),"1:1":(1080,1080),"4:5":(1080,1350)}
        results=[]
        for ratio in ratios[:4]:
            ratio=str(ratio)
            if ratio not in allowed: continue
            w,h=allowed[ratio]
            out=_project_dir(project_id)/f"variant_{ratio.replace(':','x')}.mp4"
            ffmpeg("-i",master,"-vf",f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}","-c:v","libx264","-preset","veryfast","-crf","19","-c:a","aac","-b:a","160k","-movflags","+faststart",out,timeout=900)
            meta={"ratio":ratio,"source":"final.mp4","truthful":True}
            _save_asset(project_id,"variant",out,"video/mp4",meta)
            register_artifact(project_id,out,"video/mp4",meta)
            results.append({"ratio":ratio,"asset_name":out.name,"download_url":f"/infinity/studio/project/{project_id}/asset/{out.name}"})
        audit_event(project_id,"variant_matrix_completed",{"user_id":uid,"ratios":[x["ratio"] for x in results]})
        return {"status":"completed","project_id":project_id,"variants":results,"truthful":True}


def _escape_html(s: str) -> str:
    import html
    return html.escape(str(s), quote=True)
