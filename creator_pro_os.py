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

from fastapi import HTTPException, Request, Response
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
        _asset_rows,
        register_artifact,
        audit_event,
        file_sha256,
        probe_duration,
        ffmpeg,
        jdump,
        url_host_safe,
        now,
    )

    def owned_project(pid: str, request: Request):
        uid = _get_user_id(request)
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
        # HTTP is permitted only for an explicitly local engine by default.
        # Public HTTP and arbitrary private-network probing are rejected.
        from urllib.request import Request as URequest, urlopen
        from urllib.parse import urlparse
        try:
            parsed = urlparse(str(url).strip())
            host = (parsed.hostname or "").lower().rstrip(".")
            if parsed.scheme not in {"http", "https"} or not host:
                return {"ok": False, "error": "endpoint must be http(s)"}
            if parsed.scheme == "https":
                if not url_host_safe(url):
                    return {"ok": False, "error": "endpoint rejected by SSRF policy"}
            else:
                local_hosts = {"localhost", "localhost.localdomain", "127.0.0.1", "::1"}
                if host not in local_hosts:
                    return {"ok": False, "error": "plain HTTP is restricted to localhost/local loopback"}
        except Exception as exc:
            return {"ok": False, "error": "invalid endpoint: " + str(exc)[:200]}
        try:
            req = URequest(url, headers={"User-Agent": "AI-Infinity/Creator-Pro"}, method="GET")
            with urlopen(req, timeout=timeout) as r:
                body = r.read(1600).decode("utf-8", "replace")
                return {"ok": True, "status": int(getattr(r, "status", 200)), "preview": body[:800]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:400]}

    @app.get("/infinity/studio/creator-pro")
    def creator_pro_catalog(request: Request):
        uid = _get_user_id(request)
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
                "storyboard_rebuild": True,
                "transcript_clip_extraction": True,
                "image_lab": True,
                "interactive_html_export": True,
                "variant_matrix": True,
                "open_source_connectors": True
            },
            "free_first": True,
            "truthful": True
        }


    def local_engine_url(provider: str) -> str:
        key = str(provider or "").strip().lower()
        defaults = {
            "ollama": "http://127.0.0.1:11434",
            "comfyui": "http://127.0.0.1:8188",
        }
        env_key = {"ollama":"OLLAMA_BASE_URL","comfyui":"COMFYUI_URL"}.get(key)
        if not env_key:
            raise HTTPException(400, "unsupported local engine")
        return str(os.getenv(env_key, defaults[key])).strip().rstrip("/")

    def local_engine_post(provider: str, path: str, payload: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
        from urllib.request import Request as URequest, urlopen
        from urllib.parse import urlparse
        base = local_engine_url(provider)
        parsed = urlparse(base)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme == "http" and host not in {"localhost","localhost.localdomain","127.0.0.1","::1"}:
            raise HTTPException(400, "local engine HTTP endpoint must be loopback")
        if parsed.scheme == "https" and not url_host_safe(base):
            raise HTTPException(400, "local engine HTTPS endpoint rejected by SSRF policy")
        if parsed.scheme not in {"http","https"}:
            raise HTTPException(400, "local engine endpoint must be http(s)")
        url = base + path
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = URequest(url, data=body, headers={"Content-Type":"application/json","Accept":"application/json","User-Agent":"AI-Infinity/Creator-Pro"}, method="POST")
        try:
            with urlopen(req, timeout=max(5.0,min(float(timeout),180.0))) as resp:
                raw = resp.read(8_000_000).decode("utf-8","replace")
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {"raw": raw[:4000]}
                return {"ok": True, "status": int(getattr(resp,"status",200)), "data": data, "endpoint": url}
        except Exception as exc:
            raise HTTPException(502, f"{provider} request failed: {str(exc)[:500]}")

    @app.get("/infinity/studio/open-source")
    def open_source_status(request: Request):
        uid = _get_user_id(request)
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


    @app.post("/infinity/studio/open-source/ollama/chat")
    async def ollama_chat(request: Request):
        uid = _get_user_id(request); 
        payload = await request.json()
        model = safe_text(payload.get("model") or os.getenv("OLLAMA_MODEL") or "llama3.2", 120)
        prompt = safe_text(payload.get("prompt"), 12000)
        if not prompt:
            raise HTTPException(422, "prompt is required")
        result = local_engine_post("ollama","/api/chat",{
            "model": model,
            "messages": [{"role":"user","content":prompt}],
            "stream": False,
        }, timeout=120)
        return {"provider":"ollama","model":model,"result":result["data"],"free_first":True,"truthful":True}

    @app.post("/infinity/studio/open-source/comfyui/queue")
    async def comfyui_queue(request: Request):
        uid = _get_user_id(request); 
        payload = await request.json()
        workflow = payload.get("prompt")
        if not isinstance(workflow, dict) or not workflow:
            raise HTTPException(422, "prompt must be a non-empty ComfyUI workflow object")
        client_id = safe_text(payload.get("client_id") or ("ai-infinity-"+uuid.uuid4().hex[:16]), 120)
        result = local_engine_post("comfyui","/prompt",{"prompt":workflow,"client_id":client_id}, timeout=60)
        return {"provider":"comfyui","client_id":client_id,"result":result["data"],"free_first":True,"truthful":True}

    @app.post("/infinity/studio/open-source/test")
    async def open_source_test(request: Request):
        uid = _get_user_id(request)
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
    def storyboard_get(project_id: str, request: Request):
        uid, p = owned_project(project_id, request)
        path = _project_dir(project_id) / "storyboard.json"
        if path.exists():
            data = read_json_file(path)
        else:
            b = project_blueprint(p)
            # Accept all blueprint shapes used across the historical Creator
            # builds: top-level chapters, plan.chapters, result.plan.chapters,
            # and a serialized result/blueprint object.
            candidates = []
            if isinstance(b, dict):
                candidates.append(b)
                if isinstance(b.get("plan"), dict):
                    candidates.append(b.get("plan"))
            result_raw = p.get("result_json")
            if isinstance(result_raw, str):
                try:
                    result_raw = json.loads(result_raw)
                except Exception:
                    result_raw = {}
            if isinstance(result_raw, dict):
                candidates.append(result_raw)
                if isinstance(result_raw.get("plan"), dict):
                    candidates.append(result_raw.get("plan"))
            chapters = []
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                # Different production engines use chapters, scenes, or a nested
                # storyboard. Normalize all of them into the same editable scene model.
                for key in ("chapters", "scenes"):
                    value = candidate.get(key)
                    if isinstance(value, list) and value:
                        chapters = value
                        break
                if not chapters and isinstance(candidate.get("storyboard"), dict):
                    value = candidate["storyboard"].get("scenes")
                    if isinstance(value, list) and value:
                        chapters = value
                if chapters:
                    break
            # Last-resort real-project fallback: the production engine always
            # writes script.md. Convert its generated content into an editable scene
            # rather than returning an empty/mock storyboard.
            if not chapters:
                script_path = _project_dir(project_id) / "script.md"
                script_text = ""
                if script_path.exists():
                    script_text = script_path.read_text(encoding="utf-8", errors="ignore").strip()
                # The asset registry is authoritative for generated deliverables.
                # If a worker/runtime used a different project directory representation,
                # recover the script from the registered asset path before falling back.
                if not script_text:
                    try:
                        with DB_LOCK, _connect() as c:
                            row = c.execute(
                                "SELECT path FROM studio_assets_3610 WHERE project_id=? AND kind='script' ORDER BY created_at DESC LIMIT 1",
                                (project_id,),
                            ).fetchone()
                        asset_path = Path(row["path"]) if row and row["path"] else None
                        if asset_path and asset_path.is_file():
                            script_text = asset_path.read_text(encoding="utf-8", errors="ignore").strip()
                    except Exception:
                        script_text = ""
                # A completed production always has at least its generated title/script.
                chapters = [{"heading": p.get("title") or "Production scene",
                             "narration": script_text[:12000] if script_text else (p.get("title") or "AI Infinity production"),
                             "image_prompt": p.get("title") or "creator production scene",
                             "on_screen": "", "duration": 8}]
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
    async def storyboard_put(project_id: str, request: Request):
        uid, p = owned_project(project_id, request)
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


    @app.post("/infinity/studio/project/{project_id}/storyboard/apply")
    async def storyboard_apply(project_id: str, request: Request):
        uid, p = owned_project(project_id, request)
        current = await storyboard_get(project_id, request)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        scenes = payload.get("scenes") if isinstance(payload, dict) and isinstance(payload.get("scenes"), list) else current.get("scenes") or []
        if not scenes:
            raise HTTPException(422, "storyboard has no scenes")
        clean = []
        for i, raw in enumerate(scenes[:40], 1):
            if not isinstance(raw, dict):
                continue
            try:
                dur = float(raw.get("duration") or 6)
            except Exception:
                dur = 6
            clean.append({
                "index": i,
                "heading": safe_text(raw.get("heading"), 240),
                "narration": safe_text(raw.get("narration"), 4000),
                "image_prompt": safe_text(raw.get("image_prompt"), 700),
                "on_screen": safe_text(raw.get("on_screen"), 350),
                "duration": max(1.0, min(600.0, dur)),
            })
        if not clean:
            raise HTTPException(422, "storyboard contains no valid scenes")
        req = dict(p.get("request_json") or {})
        req["title"] = safe_text(payload.get("title") if isinstance(payload, dict) else None, 200) or safe_text((p.get("title") or "") + " — storyboard revision", 200)
        req["objective"] = req.get("objective") or req.get("topic") or p.get("title") or ""
        req["topic"] = req.get("topic") or p.get("title") or ""
        req["storyboard_override"] = {"title": req["title"], "scenes": clean, "source_project": project_id}
        req["notes"] = (str(req.get("notes") or "") + "\nRebuilt from editable storyboard of " + project_id).strip()[:2000]
        result = enqueue(req, uid, None)
        audit_event(project_id, "storyboard_revision_queued", {"child_project_id":result.get("project_id"),"scene_count":len(clean)})
        return {"status":"queued","source_project_id":project_id,"project_id":result.get("project_id"),"scene_count":len(clean),"truthful":True}

    @app.get("/infinity/studio/project/{project_id}/transcript")
    def transcript_context(project_id: str, request: Request):
        uid, p = owned_project(project_id, request)
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


    @app.post("/infinity/studio/project/{project_id}/transcript/clip")
    async def transcript_clip(project_id: str, request: Request):
        uid, p = owned_project(project_id, request)
        if p.get("status") not in {"completed","completed_with_qc_warnings"}:
            verified = False
            try:
                import reality_first_3901 as _reality_kernel
                verified = bool(_reality_kernel.truth_for_project(project_id).get("verified"))
            except Exception:
                verified = False
            if not verified:
                raise HTTPException(409, "project master is not ready")
            p = _get_project(project_id) or p
            if p.get("status") not in {"completed","completed_with_qc_warnings"}:
                raise HTTPException(409, "project master is not ready")
        master = _project_dir(project_id) / "final.mp4"
        if not master.exists():
            raise HTTPException(404, "final master not found")
        payload = await request.json()
        start = payload.get("start")
        end = payload.get("end")
        if start is None or end is None:
            transcript = await transcript_context(project_id, request)
            try:
                idx = int(payload.get("segment_index"))
            except Exception:
                raise HTTPException(422, "segment_index is required when start/end are omitted")
            segs = transcript.get("segments") or []
            if idx < 0 or idx >= len(segs):
                raise HTTPException(422, "segment_index is outside transcript range")
            def clock(v: str) -> float:
                h,m,s = v.split(":")
                sec,ms = s.split(",")
                return int(h)*3600 + int(m)*60 + int(sec) + int(ms)/1000.0
            try:
                start = clock(str(segs[idx]["start"]))
                end = clock(str(segs[idx]["end"]))
            except Exception:
                raise HTTPException(422, "selected transcript segment has no usable timestamps")
        try:
            start = max(0.0, float(start)); end = max(0.0, float(end))
        except Exception:
            raise HTTPException(422, "start/end must be numbers")
        duration = probe_duration(master)
        end = min(end, duration)
        if end <= start + 0.2:
            raise HTTPException(422, "clip window is empty")
        out = _project_dir(project_id) / f"transcript_clip_{int(time.time())}_{uuid.uuid4().hex[:8]}.mp4"
        import studio_ultimate as _studio
        _studio.ACTIVE_PROJECT.project_id = project_id
        try:
            ffmpeg("-ss",start,"-i",master,"-t",end-start,"-c:v","libx264","-preset","veryfast","-crf","19","-pix_fmt","yuv420p","-c:a","aac","-b:a","160k","-movflags","+faststart",out,timeout=600)
        except Exception as exc:
            out.unlink(missing_ok=True)
            audit_event(project_id,"transcript_clip_failed",{"error":str(exc)[:500]})
            raise HTTPException(500,"transcript clip failed: "+str(exc)[:300])
        finally:
            _studio.ACTIVE_PROJECT.project_id = None
        meta={"source":"final.mp4","start":start,"end":end,"duration":probe_duration(out),"transcript_segment":payload.get("segment_index"),"truthful":True}
        _save_asset(project_id,"transcript_clip",out,"video/mp4",meta)
        art=register_artifact(project_id,out,"video/mp4",meta)
        audit_event(project_id,"transcript_clip_completed",{"asset":out.name,"sha256":art.get("sha256")})
        return {"status":"completed","project_id":project_id,"asset_name":out.name,"download_url":f"/infinity/studio/project/{project_id}/asset/{out.name}","metadata":meta,"truthful":True}

    @app.post("/infinity/studio/project/{project_id}/edit/advanced")
    async def advanced_edit(project_id: str, request: Request):
        uid, p = owned_project(project_id, request)
        if p.get("status") not in {"completed","completed_with_qc_warnings"}:
            verified = False
            try:
                import reality_first_3901 as _reality_kernel
                verified = bool(_reality_kernel.truth_for_project(project_id).get("verified"))
            except Exception:
                verified = False
            if not verified:
                raise HTTPException(409,"project master is not ready")
            p = _get_project(project_id) or p
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


    @app.post("/infinity/studio/project/{project_id}/image/edit")
    async def image_edit(project_id: str, request: Request):
        uid, p = owned_project(project_id, request)
        payload = await request.json()
        source_name = safe_text(payload.get("source_asset_name"), 220)
        if not source_name:
            raise HTTPException(422, "source_asset_name is required")
        rows = _asset_rows(project_id)
        found = next((x for x in rows if Path(str(x.get("path") or "")).name == Path(source_name).name or str(x.get("metadata",{}).get("filename") or "") == Path(source_name).name), None)
        if not found:
            raise HTTPException(404, "image asset is not registered in this project")
        source = Path(str(found.get("path") or ""))
        if not source.exists() or source.suffix.lower() not in {".png",".jpg",".jpeg",".webp"}:
            raise HTTPException(422, "selected asset is not a supported image")
        try:
            from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageDraw, ImageFont
            im = Image.open(source).convert("RGBA")
            # Crop first, with pixel bounds clamped to the actual image.
            crop = payload.get("crop")
            if isinstance(crop, dict):
                try:
                    x=max(0,int(float(crop.get("x",0))))
                    y=max(0,int(float(crop.get("y",0))))
                    w=max(1,int(float(crop.get("width",im.width-x))))
                    h=max(1,int(float(crop.get("height",im.height-y))))
                    x=min(x,im.width-1); y=min(y,im.height-1)
                    im=im.crop((x,y,min(im.width,x+w),min(im.height,y+h)))
                except Exception:
                    pass
            try: rotate=float(payload.get("rotate") or 0)
            except Exception: rotate=0
            if abs(rotate)>0.001:
                im=im.rotate(max(-180,min(180,rotate)),expand=True,resample=Image.Resampling.BICUBIC)
            try: scale=float(payload.get("scale") or 1)
            except Exception: scale=1
            scale=max(0.25,min(4.0,scale))
            max_dim=4096
            if scale != 1:
                nw=max(1,min(max_dim,int(im.width*scale)))
                nh=max(1,min(max_dim,int(im.height*scale)))
                im=im.resize((nw,nh),Image.Resampling.LANCZOS)
            try:
                brightness=float(payload.get("brightness") if payload.get("brightness") is not None else 1)
            except Exception: brightness=1
            try:
                contrast=float(payload.get("contrast") if payload.get("contrast") is not None else 1)
            except Exception: contrast=1
            try:
                saturation=float(payload.get("saturation") if payload.get("saturation") is not None else 1)
            except Exception: saturation=1
            brightness=max(0,min(3,brightness)); contrast=max(0,min(3,contrast)); saturation=max(0,min(3,saturation))
            if brightness != 1: im=ImageEnhance.Brightness(im).enhance(brightness)
            if contrast != 1: im=ImageEnhance.Contrast(im).enhance(contrast)
            if saturation != 1: im=ImageEnhance.Color(im).enhance(saturation)
            try: sharp=float(payload.get("sharpness") if payload.get("sharpness") is not None else 1)
            except Exception: sharp=1
            sharp=max(0,min(5,sharp))
            if sharp > 1: im=ImageEnhance.Sharpness(im).enhance(sharp)
            if bool(payload.get("grayscale")):
                gray=ImageOps.grayscale(im)
                im=Image.merge("RGBA",(gray,gray,gray,im.getchannel("A")))
            text_overlay=safe_text(payload.get("text"),500)
            if text_overlay:
                draw=ImageDraw.Draw(im)
                try:
                    font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                    font=ImageFont.truetype(font_path,max(14,min(180,int(im.width*0.045))))
                except Exception:
                    font=ImageFont.load_default()
                try: alpha=float(payload.get("text_opacity") if payload.get("text_opacity") is not None else 0.9)
                except Exception: alpha=0.9
                alpha=max(0,min(1,alpha))
                bbox=draw.textbbox((0,0),text_overlay,font=font,stroke_width=2)
                tw=bbox[2]-bbox[0]; th=bbox[3]-bbox[1]
                pos=str(payload.get("text_position") or "bottom")
                margin=max(12,int(im.width*0.035))
                tx=(im.width-tw)//2
                ty=margin if pos=="top" else (im.height-th)//2 if pos=="center" else im.height-th-margin
                draw.rounded_rectangle((tx-18,ty-10,tx+tw+18,ty+th+10),radius=14,fill=(0,0,0,int(150*alpha)))
                draw.text((tx,ty),text_overlay,font=font,fill=(255,255,255,int(255*alpha)),stroke_width=2,stroke_fill=(0,0,0,int(170*alpha)))
            out=_project_dir(project_id)/f"image_edit_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
            im.save(out,format="PNG",optimize=True)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500,"image edit failed: "+str(exc)[:400])
        meta={
            "source":source.name,
            "editor":"AI Infinity Creator Pro Image Lab",
            "width":im.width,
            "height":im.height,
            "options":{k:payload.get(k) for k in ["crop","rotate","scale","brightness","contrast","saturation","sharpness","grayscale","text","text_opacity","text_position"]},
            "sha256":file_sha256(out),
            "truthful":True,
        }
        _save_asset(project_id,"image_edit",out,"image/png",meta)
        art=register_artifact(project_id,out,"image/png",meta)
        audit_event(project_id,"image_edit_completed",{"user_id":uid,"asset":out.name,"sha256":art.get("sha256")})
        return {"status":"completed","project_id":project_id,"asset_name":out.name,"download_url":f"/infinity/studio/project/{project_id}/asset/{out.name}","metadata":meta,"truthful":True}

    @app.post("/infinity/studio/project/{project_id}/interactive")
    async def interactive_export(project_id: str, request: Request):
        uid, p = owned_project(project_id, request)
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
    async def variant_matrix(project_id: str, request: Request):
        uid, p = owned_project(project_id, request)
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
