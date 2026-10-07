"""
AI Infinity — Professional Creator System
Canonical orchestration surface over the existing Creator Studio engine.

This module adds the missing "professional studio" control plane without
duplicating the existing media engine:
- one-command production entry
- real project/timeline model
- non-destructive scene timeline revisions
- real timeline re-rendering from generated scene masters
- platform delivery variants
- package/export assembly
- independent verification
- provider/capability truth surface
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse


VERSION = "TARGET-2050.PRO-CREATOR.1"
BUILD = "PROFESSIONAL-CREATOR-CANONICAL-PIPELINE"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _safe(value: Any, limit: int = 4000) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _parse_project(studio, project_id: str, request: Request):
    uid = studio._get_user_id(request)
    p = studio._get_project(project_id)
    if not p or p.get("user_id") != uid:
        raise HTTPException(404, "project not found")
    return uid, p


def _project_truth(studio, p: Dict[str, Any]) -> Dict[str, Any]:
    gate = getattr(studio, "_professional_truth", None)
    if not gate:
        return {"verified": False, "status": "verification_engine_unavailable", "truthful": True}
    try:
        return gate(p, reconcile=True)
    except Exception as exc:
        return {"verified": False, "status": "verification_failed", "error": str(exc)[:800], "truthful": True}


def _blueprint(p: Dict[str, Any]) -> Dict[str, Any]:
    b = p.get("blueprint_json")
    if isinstance(b, dict):
        return b
    try:
        return json.loads(b or "{}")
    except Exception:
        return {}


def _result(p: Dict[str, Any]) -> Dict[str, Any]:
    b = p.get("result_json")
    if isinstance(b, dict):
        return b
    try:
        return json.loads(b or "{}")
    except Exception:
        return {}


def _chapters(p: Dict[str, Any], studio) -> List[Dict[str, Any]]:
    root = studio._project_dir(p["project_id"])
    storyboard = root / "storyboard.json"
    candidates: List[Any] = []
    if storyboard.exists():
        try:
            data = json.loads(storyboard.read_text(encoding="utf-8"))
            candidates.append(data)
        except Exception:
            pass
    b = _blueprint(p)
    candidates.append(b)
    if isinstance(b.get("plan"), dict):
        candidates.append(b["plan"])
    r = _result(p)
    candidates.append(r)
    if isinstance(r.get("plan"), dict):
        candidates.append(r["plan"])
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("scenes", "chapters"):
            rows = candidate.get(key)
            if isinstance(rows, list) and rows:
                clean = []
                for i, raw in enumerate(rows, 1):
                    if not isinstance(raw, dict):
                        continue
                    try:
                        dur = float(raw.get("actual_duration") or raw.get("duration") or 6)
                    except Exception:
                        dur = 6.0
                    clean.append({
                        "index": i,
                        "heading": _safe(raw.get("heading") or raw.get("title") or f"Scene {i}", 240),
                        "narration": _safe(raw.get("narration") or raw.get("text"), 5000),
                        "image_prompt": _safe(raw.get("image_prompt") or raw.get("visual_query") or raw.get("heading"), 900),
                        "on_screen": _safe(raw.get("on_screen") or "", 500),
                        "duration": max(0.5, min(600.0, dur)),
                    })
                if clean:
                    return clean
    return []


def _timeline_payload(studio, p: Dict[str, Any]) -> Dict[str, Any]:
    scenes = _chapters(p, studio)
    root = studio._project_dir(p["project_id"])
    cursor = 0.0
    timeline = []
    for i, scene in enumerate(scenes, 1):
        raw = dict(scene)
        path = root / f"scene_{i:02d}.mp4"
        try:
            media_duration = float(studio.probe_duration(path)) if path.exists() else 0.0
        except Exception:
            media_duration = 0.0
        duration = media_duration if media_duration > 0 else float(scene.get("duration") or 6)
        timeline.append({
            **raw,
            "index": i,
            "source_asset": path.name if path.exists() else None,
            "source_exists": path.exists(),
            "source_duration": round(media_duration, 3),
            "start": round(cursor, 3),
            "end": round(cursor + duration, 3),
            "trim_start": 0.0,
            "trim_end": 0.0,
            "enabled": True,
        })
        cursor += duration
    return {
        "version": VERSION,
        "project_id": p["project_id"],
        "title": p.get("title"),
        "duration_seconds": round(cursor, 3),
        "fps": 30,
        "resolution": [1920, 1080],
        "tracks": [
            {"id": "video-main", "type": "video", "locked": False},
            {"id": "scene-audio", "type": "audio", "locked": False},
        ],
        "scenes": timeline,
        "truthful": True,
    }


def _write_timeline(studio, project_id: str, payload: Dict[str, Any]) -> Path:
    root = studio._project_dir(project_id)
    path = root / "timeline.json"
    path.write_text(_json(payload), encoding="utf-8")
    studio.register_artifact(project_id, path, "application/json", {"editor": VERSION})
    studio.audit_event(project_id, "professional_timeline_saved", {
        "scene_count": len(payload.get("scenes") or []),
        "duration_seconds": payload.get("duration_seconds"),
    })
    return path


def _normalise_scene_rows(payload: Dict[str, Any], studio) -> List[Dict[str, Any]]:
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise HTTPException(422, "timeline.scenes must contain at least one scene")
    clean = []
    seen = set()
    for raw in scenes[:40]:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("index"))
        except Exception:
            continue
        if index in seen or index < 1 or index > 40:
            continue
        seen.add(index)
        try:
            trim_start = max(0.0, float(raw.get("trim_start") or 0))
            trim_end = max(0.0, float(raw.get("trim_end") or 0))
        except Exception:
            trim_start, trim_end = 0.0, 0.0
        clean.append({
            "index": index,
            "heading": _safe(raw.get("heading") or f"Scene {index}", 240),
            "narration": _safe(raw.get("narration"), 6000),
            "image_prompt": _safe(raw.get("image_prompt"), 1000),
            "on_screen": _safe(raw.get("on_screen"), 500),
            "trim_start": min(trim_start, 600.0),
            "trim_end": min(trim_end, 600.0),
            "enabled": bool(raw.get("enabled", True)),
        })
    if not clean:
        raise HTTPException(422, "timeline contains no valid scenes")
    clean.sort(key=lambda x: x["index"])
    root = studio._project_dir(payload.get("project_id") or "")
    out = []
    cursor = 0.0
    for position, row in enumerate(clean, 1):
        src = root / f"scene_{row['index']:02d}.mp4"
        if not src.exists():
            raise HTTPException(409, f"scene source scene_{row['index']:02d}.mp4 is unavailable")
        source_duration = float(studio.probe_duration(src))
        usable = source_duration - row["trim_start"] - row["trim_end"]
        if usable <= 0.25:
            raise HTTPException(422, f"scene {row['index']} trim removes the whole scene")
        row["source_asset"] = src.name
        row["source_duration"] = round(source_duration, 3)
        row["duration"] = round(usable, 3)
        row["start"] = round(cursor, 3)
        row["end"] = round(cursor + usable, 3)
        row["position"] = position
        cursor += usable
        out.append(row)
    return out


def _package(studio, project_id: str) -> Path:
    root = studio._project_dir(project_id)
    out = root / "package.zip"
    members = []
    preferred = [
        "final.mp4", "short_01.mp4", "short_02.mp4", "short_03.mp4",
        "thumbnail.jpg", "script.md", "article.md", "captions.srt",
        "audio_master.mp3", "sources.json", "fact_check.json",
        "provenance.json", "visual_rights.json", "seo.json",
        "social_campaign.json", "accessibility.json", "platform_manifest.json",
        "timeline.json", "manifest.json", "production_truth.json",
    ]
    for name in preferred:
        p = root / name
        if p.exists() and p.is_file():
            members.append(p)
    for p in sorted(root.glob("variant_*.mp4")):
        if p.is_file():
            members.append(p)
    for p in sorted(root.glob("short_*.mp4")):
        if p.is_file() and p not in members:
            members.append(p)
    if not members:
        raise RuntimeError("no production artifacts available for packaging")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in members:
            z.write(p, arcname=p.name)
    studio.register_artifact(project_id, out, "application/zip", {
        "type": "professional_delivery_package",
        "member_count": len(members),
        "editor": VERSION,
    })
    return out


def _render_variants(studio, project_id: str, master: Path) -> List[Dict[str, Any]]:
    root = studio._project_dir(project_id)
    ratios = {
        "16:9": (1920, 1080),
        "9:16": (1080, 1920),
        "1:1": (1080, 1080),
        "4:5": (1080, 1350),
    }
    results = []
    for ratio, (w, h) in ratios.items():
        name = f"variant_{ratio.replace(':','x')}.mp4"
        out = root / name
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},fps=30"
        )
        studio.ffmpeg(
            "-i", master, "-vf", vf,
            "-c:v", "libx264",
            "-preset", os.getenv("AI_INFINITY_VIDEO_PRESET", "veryfast"),
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", out, timeout=1200,
        )
        meta = {
            "ratio": ratio,
            "width": w, "height": h,
            "source": master.name,
            "variant_engine": VERSION,
            "duration_seconds": round(studio.probe_duration(out), 3),
            "sha256": studio.file_sha256(out),
            "truthful": True,
        }
        studio._save_asset(project_id, "variant", out, "video/mp4", meta)
        studio.register_artifact(project_id, out, "video/mp4", meta)
        results.append({
            "ratio": ratio, "asset_name": name,
            "download_url": f"/infinity/studio/project/{project_id}/asset/{name}",
            "duration_seconds": meta["duration_seconds"],
        })
    return results


def _render_timeline(studio, project_id: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    root = studio._project_dir(project_id)
    segment_paths = []
    materialised = []
    selected_chapters = []
    for pos, row in enumerate(rows, 1):
        if not row.get("enabled", True):
            continue
        src = root / Path(str(row["source_asset"])).name
        out = root / f"timeline_segment_{pos:02d}.mp4"
        start = float(row.get("trim_start") or 0)
        duration = float(row["duration"])
        args = []
        if start > 0:
            args += ["-ss", start]
        args += [
            "-i", src, "-t", max(0.25, duration),
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", os.getenv("AI_INFINITY_VIDEO_PRESET", "veryfast"),
            "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", out,
        ]
        studio.ffmpeg(*args, timeout=max(300, int(duration * 20)))
        if not out.exists() or out.stat().st_size < 10000:
            raise RuntimeError(f"timeline segment render failed: {out.name}")
        segment_paths.append(out)
        materialised.append(out.name)
        selected_chapters.append({
            "heading": row["heading"],
            "narration": row["narration"],
            "duration": duration,
            "actual_duration": duration,
        })
    if not segment_paths:
        raise HTTPException(422, "timeline has no enabled scenes")
    concat_manifest = root / "timeline_concat.txt"
    concat_manifest.write_text(
        "".join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in segment_paths),
        encoding="utf-8",
    )
    master_tmp = root / "final.timeline.tmp.mp4"
    studio.ffmpeg(
        "-f", "concat", "-safe", "0", "-i", concat_manifest,
        "-c", "copy", "-movflags", "+faststart", master_tmp, timeout=1800,
    )
    master = root / "final.mp4"
    master_tmp.replace(master)

    captions = root / "captions.srt"
    studio.write_srt(selected_chapters, captions)
    studio.register_artifact(project_id, captions, "application/x-subrip", {"editor": VERSION})

    thumbnail = studio.make_thumbnail(master, str(selected_chapters[0]["heading"]), root)
    if thumbnail.exists():
        studio._save_asset(project_id, "thumbnail", thumbnail, "image/jpeg", {"editor": VERSION})
        studio.register_artifact(project_id, thumbnail, "image/jpeg", {"editor": VERSION})

    qc = studio.extended_quality_check(master, selected_chapters, captions, root, assets=[])
    result = _result(studio._get_project(project_id))
    result["professional_timeline"] = {
        "scene_count": len(selected_chapters),
        "duration_seconds": round(studio.probe_duration(master), 3),
        "rendered_at": studio.utc_iso(),
        "quality": qc,
        "segments": materialised,
    }
    studio._update_project(
        project_id,
        status="completed" if qc.get("passed") else "completed_with_qc_warnings",
        stage="quality_control",
        progress=100 if qc.get("passed") else 98,
        result_json=studio.jdump(result),
        error=None if qc.get("passed") else "Timeline render completed but quality review requires attention.",
    )
    studio.register_artifact(project_id, master, "video/mp4", {
        "editor": VERSION, "quality": qc, "sha256": studio.file_sha256(master),
    })
    return {"master": master, "quality": qc, "chapters": selected_chapters}


def _html() -> str:
    return r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#070a0f">
<title>AI Infinity — Professional Creator Studio</title>
<style>
:root{--bg:#070a0f;--panel:#0d131c;--panel2:#101923;--line:#253141;--text:#f3f6fa;--muted:#93a0b2;--accent:#d9f99d;--good:#9fe8cb;--warn:#f7d78a;--bad:#ff9fb0}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% -10%,#14202f 0,transparent 36%),var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
button,input,textarea,select{font:inherit}button{cursor:pointer}.shell{max-width:1500px;margin:auto;padding:22px}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:18px}.brand{display:flex;gap:10px;align-items:center}.mark{width:38px;height:38px;border:1px solid #364557;border-radius:12px;display:grid;place-items:center;background:#101722;font-weight:900;font-size:20px}.title{font-weight:850}.sub{color:var(--muted);font-size:11px}.grid{display:grid;gap:14px}.cols{grid-template-columns:minmax(0,1.45fr) minmax(320px,.55fr)}.card{border:1px solid var(--line);background:rgba(13,19,28,.94);border-radius:18px;padding:18px;box-shadow:0 16px 50px #0005}.eyebrow{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#99a6b6;font-weight:800}.hero h1{font-size:clamp(32px,5vw,60px);letter-spacing:-.045em;line-height:.98;margin:10px 0}.lead{color:#b7c2d1;max-width:800px}.cmd{width:100%;min-height:150px;background:#080c12;color:white;border:1px solid #334155;border-radius:14px;padding:15px;resize:vertical;outline:none}.cmd:focus,input:focus,select:focus{border-color:#5a7087;outline:none;box-shadow:0 0 0 3px #89b7ff12}.row{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}.controls{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:9px}.field,.select{width:100%;background:#090f16;border:1px solid var(--line);color:white;border-radius:10px;padding:10px}.btn{background:#101823;color:#e7edf5;border:1px solid #304051;border-radius:10px;padding:10px 13px}.btn:hover{border-color:#52667b}.primary{background:var(--accent);color:#0b0e11;border-color:var(--accent);font-weight:800}.badge{display:inline-flex;align-items:center;border:1px solid #304051;border-radius:999px;padding:5px 8px;font-size:10px;color:#c6d0dc}.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}.status{display:inline-flex;padding:5px 9px;border:1px solid #304051;border-radius:999px;font-size:10px}.progress{height:8px;background:#151e29;border-radius:99px;overflow:hidden}.progress i{display:block;height:100%;width:0;background:linear-gradient(90deg,#9ac97f,#d9f99d)}.video{aspect-ratio:16/9;background:#020509;border:1px solid var(--line);border-radius:14px;overflow:hidden}.video video{width:100%;height:100%;display:block}.stage{display:grid;grid-template-columns:repeat(8,minmax(60px,1fr));gap:6px;margin-top:12px}.stage div{border:1px solid var(--line);padding:8px;border-radius:10px;background:#0a0f16;font-size:10px;color:#8e9aab}.timeline{display:grid;gap:9px}.scene{border:1px solid var(--line);border-radius:14px;background:#0a1017;padding:12px}.scene.on{border-color:#566a41}.sceneHead{display:flex;justify-content:space-between;gap:10px;align-items:center}.sceneTitle{font-weight:800}.sceneMeta{font-size:10px;color:var(--muted)}.sceneGrid{display:grid;grid-template-columns:1.1fr 1fr 120px 120px;gap:8px;margin-top:9px}.sceneGrid textarea{min-height:72px}.sceneGrid textarea,.sceneGrid input{width:100%;background:#080d13;color:#eef2f6;border:1px solid #263444;border-radius:9px;padding:9px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.kpi{padding:12px;border:1px solid var(--line);border-radius:12px;background:#0a1017}.kpi span{font-size:10px;color:var(--muted);display:block}.kpi b{display:block;font-size:20px;margin-top:3px}.out{margin-top:10px}.notice{padding:11px 12px;border:1px solid var(--line);border-radius:11px;background:#0a1017}.list{display:grid;gap:7px}.small{font-size:11px;color:var(--muted)}pre{white-space:pre-wrap;word-break:break-word;font:11px ui-monospace,monospace;color:#c7d0dc}
@media(max-width:980px){.cols{grid-template-columns:1fr}.controls{grid-template-columns:repeat(2,1fr)}.stage{grid-template-columns:repeat(4,1fr)}.sceneGrid{grid-template-columns:1fr 1fr}}
@media(max-width:640px){.shell{padding:13px}.controls{grid-template-columns:1fr}.sceneGrid{grid-template-columns:1fr}.kpis{grid-template-columns:1fr 1fr}.top{align-items:flex-start}}
</style>
</head>
<body>
<div class="shell">
<div class="top"><div class="brand"><div class="mark">∞</div><div><div class="title">AI Infinity · Professional Creator Studio</div><div class="sub">One command → real production → editable timeline → verified delivery</div></div></div><div class="badge good" id="sys">Checking system…</div></div>

<div class="grid cols">
<section class="card hero">
<div class="eyebrow">Director command</div>
<h1>Produce the complete piece, not a placeholder.</h1>
<p class="lead">The command is passed into the existing AI Infinity production engine. Real research, media acquisition/generation, narration, sound, captions, rendering, QC and delivery remain authoritative.</p>
<textarea id="cmd" class="cmd" placeholder="Create a 90-second premium vertical video about [topic]. Research current facts, write a strong hook, create cinematic visuals, natural narration, music, captions, thumbnail and platform-ready metadata."></textarea>
<div class="controls">
<input id="duration" class="field" type="number" value="90" min="20" max="3600" placeholder="Seconds">
<select id="format" class="select"><option value="short">Short / Reel</option><option value="long">Long form</option></select>
<select id="quality" class="select"><option value="balanced">Balanced</option><option value="high">High</option><option value="fast">Fast</option><option value="draft">Draft</option></select>
<select id="ratio" class="select"><option>9:16</option><option>16:9</option><option>1:1</option><option>4:5</option></select>
</div>
<div class="actions"><button class="btn primary" onclick="createProject()">Start real production</button><button class="btn" onclick="loadLatest()">Open latest</button></div>
<div id="createOut" class="out"></div>
</section>

<aside class="grid">
<div class="card"><div class="row"><div><div class="eyebrow">System contract</div><div style="font-weight:800;margin-top:5px">Production truth</div></div><span class="status good">No fake success</span></div><div id="providerList" class="list" style="margin-top:10px"></div></div>
<div class="card"><div class="eyebrow">Live project</div><div id="live"><div class="notice">No project selected.</div></div></div>
</aside>
</div>

<div class="card" style="margin-top:14px">
<div class="row"><div><div class="eyebrow">Timeline editor</div><div style="font-size:20px;font-weight:850;margin-top:4px">Scene-level non-destructive control</div><div class="small">Reorder, disable, trim and rewrite a scene, then render the revised master without pretending it happened.</div></div><div class="actions"><button class="btn" onclick="loadTimeline()">Load timeline</button><button class="btn primary" onclick="saveTimelineAndRender()">Render edited master</button></div></div>
<div id="tl" class="timeline" style="margin-top:12px"><div class="notice">Create or select a project to load its timeline.</div></div>
</div>

<div class="card" style="margin-top:14px">
<div class="row"><div><div class="eyebrow">Verified delivery</div><div style="font-size:20px;font-weight:850;margin-top:4px">Master, variants, evidence and package</div></div><div class="actions"><button class="btn" onclick="verify()">Run verification</button><button class="btn" onclick="refresh()">Refresh project</button></div></div>
<div id="delivery" style="margin-top:12px"><div class="notice">No project selected.</div></div>
</div>
</div>
<script>
let pid=localStorage.getItem("aii_pro_project")||"",timer=null,timelineData=null;
const $=x=>document.getElementById(x);
const esc=v=>String(v==null?"":v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
async function api(u,o={}){const r=await fetch(u,{credentials:"same-origin",...o});const t=await r.text();let j;try{j=JSON.parse(t)}catch(_){j={raw:t}}if(!r.ok)throw new Error(j.detail||j.message||t||("HTTP "+r.status));return j}
async function init(){try{const h=await api("/infinity/pro/health");$("sys").textContent=h.status+" · "+h.readiness_label;$("providerList").innerHTML=(h.providers||[]).map(x=>"<div class='row'><span>"+esc(x.name)+"</span><span class='badge "+(x.ready?"good":"warn")+"'>"+esc(x.status)+"</span></div>").join("");}catch(e){$("sys").textContent="System check failed"}if(pid)open(pid)}
async function createProject(){const cmd=$("cmd").value.trim();if(cmd.length<8){alert("Describe the content you want produced.");return} $("createOut").innerHTML="<div class='notice'>Queueing the real production graph…</div>";try{const p=await api("/infinity/pro/create",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({objective:cmd,title:cmd.slice(0,140),duration:+$("duration").value||90,format:$("format").value,quality_preset:$("quality").value,aspect_ratio:$("ratio").value,content_type:"video"})});pid=p.project_id;localStorage.setItem("aii_pro_project",pid);$("createOut").innerHTML="<div class='notice good'>Production queued: <b>"+esc(pid)+"</b></div>";open(pid)}catch(e){$("createOut").innerHTML="<div class='notice bad'>"+esc(e.message)+"</div>"}}
async function open(id){pid=id;localStorage.setItem("aii_pro_project",id);await refresh();await loadTimeline()}
async function refresh(){if(!pid)return;try{const x=await api("/infinity/pro/project/"+encodeURIComponent(pid));renderLive(x);if(["completed","completed_with_qc_warnings","failed","cancelled"].includes(x.status)){clearInterval(timer);timer=null}else if(!timer){timer=setInterval(refresh,1800)}}catch(e){$("live").innerHTML="<div class='notice bad'>"+esc(e.message)+"</div>"}}
function renderLive(x){const r=x.result||{},q=x.truth||{};$("live").innerHTML="<div class='row'><b>"+esc(x.title||x.project_id)+"</b><span class='status "+(String(x.status).includes("complete")?"good":"")+"'>"+esc(x.status)+"</span></div><div class='small' style='margin:6px 0'>"+esc(x.stage||"queued")+" · "+Math.round(x.progress||0)+"%</div><div class='progress'><i style='width:"+Math.round(x.progress||0)+"%'></i></div><div class='kpis' style='margin-top:9px'><div class='kpi'><span>Scenes</span><b>"+esc(x.total_scenes||0)+"</b></div><div class='kpi'><span>Attempt</span><b>"+esc(x.attempt||1)+"</b></div><div class='kpi'><span>Truth</span><b>"+(q.verified?"YES":"PENDING")+"</b></div><div class='kpi'><span>Duration</span><b>"+esc((r.duration_seconds||0).toFixed?.(1)||r.duration_seconds||0)+"s</b></div></div>";renderDelivery(x)}
function renderDelivery(x){const done=["completed","completed_with_qc_warnings"].includes(x.status);if(!done){$("delivery").innerHTML="<div class='notice'>Delivery remains locked until production completes and the verification gate passes.</div>";return}const base="/infinity/studio/project/"+encodeURIComponent(pid)+"/asset/";const links=[["Master","final.mp4"],["Package","package.zip"],["Thumbnail","thumbnail.jpg"],["Script","script.md"],["Captions","captions.srt"],["Sources","sources.json"],["Fact check","fact_check.json"],["Provenance","provenance.json"],["Timeline","timeline.json"]];$("delivery").innerHTML="<div class='row'><span class='badge "+(x.truth&&x.truth.verified?"good":"warn")+"'>"+(x.truth&&x.truth.verified?"PROFESSIONALLY VERIFIED":"QC REVIEW REQUIRED")+"</span><div class='actions'>"+links.map(a=>"<a class='btn' href='"+base+encodeURIComponent(a[1])+"' target='_blank'>"+a[0]+"</a>").join("")+"</div></div>"+(x.truth&&x.truth.failures?("<div class='notice warn' style='margin-top:10px'>"+esc(x.truth.failures.join(" · "))+"</div>"):"")}
async function loadTimeline(){if(!pid)return;try{timelineData=await api("/infinity/pro/project/"+encodeURIComponent(pid)+"/timeline");renderTimeline()}catch(e){$("tl").innerHTML="<div class='notice bad'>"+esc(e.message)+"</div>"}}
function renderTimeline(){const s=timelineData.scenes||[];$("tl").innerHTML=s.map((x,i)=>"<div class='scene "+(x.enabled!==false?"on":"")+"'><div class='sceneHead'><div><div class='sceneTitle'>"+esc(i+1)+". "+esc(x.heading)+"</div><div class='sceneMeta'>"+esc(x.source_asset||"missing")+" · "+x.start+"s → "+x.end+"s · "+x.source_duration+"s source</div></div><label class='badge'><input type='checkbox' "+(x.enabled!==false?"checked":"")+" data-enable='"+i+"'> enabled</label></div><div class='sceneGrid'><textarea data-heading='"+i+"' placeholder='Scene heading'>"+esc(x.heading)+"</textarea><textarea data-narr='"+i+"' placeholder='Narration'>"+esc(x.narration)+"</textarea><input data-trims='"+i+"' type='number' step='0.1' min='0' value='"+x.trim_start+"' placeholder='Trim start'><input data-trime='"+i+"' type='number' step='0.1' min='0' value='"+x.trim_end+"' placeholder='Trim end'></div></div>").join("")||"<div class='notice'>No scene timeline is available yet. Complete production first.</div>"}
function collectTimeline(){if(!timelineData)return null;const out={...timelineData,scenes:(timelineData.scenes||[]).map((x,i)=>({...x,heading:$("[data-heading='"+i+"']").value,narration:$("[data-narr='"+i+"']").value,trim_start:+$("[data-trims='"+i+"']").value||0,trim_end:+$("[data-trime='"+i+"']").value||0,enabled:$("[data-enable='"+i+"']").checked}))};return out}
async function saveTimelineAndRender(){const t=collectTimeline();if(!t)return;try{$("tl").insertAdjacentHTML("afterbegin","<div class='notice'>Rendering revised master from the real scene files…</div>");const r=await api("/infinity/pro/project/"+encodeURIComponent(pid)+"/timeline/render",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(t)});$("tl").insertAdjacentHTML("afterbegin","<div class='notice good'>Re-render complete. QC: "+esc(r.quality&&r.quality.passed?"PASS":"REVIEW REQUIRED")+"</div>");await open(pid)}catch(e){$("tl").insertAdjacentHTML("afterbegin","<div class='notice bad'>"+esc(e.message)+"</div>")}}
async function verify(){if(!pid)return;try{const r=await api("/infinity/pro/project/"+encodeURIComponent(pid)+"/verify");$("delivery").innerHTML="<div class='notice "+(r.verified?"":"warn")+"'><b>"+(r.verified?"Professional verification passed":"Verification did not pass")+"</b></div><pre>"+esc(JSON.stringify(r,null,2))+"</pre>"+$("delivery").innerHTML}catch(e){alert(e.message)}}
async function loadLatest(){const x=await api("/infinity/studio/projects?limit=1");if(x.projects&&x.projects[0])open(x.projects[0].project_id)}
init();
</script>
</body>
</html>'''


def install(app: Any) -> None:
    import studio_ultimate as studio

    @app.get("/infinity/pro/health")
    def professional_health():
        providers = [
            {"name": "FFmpeg master renderer", "ready": bool(__import__("shutil").which("ffmpeg")), "status": "ready" if __import__("shutil").which("ffmpeg") else "missing"},
            {"name": "Offline voice fallback", "ready": bool(__import__("shutil").which("espeak-ng") or __import__("shutil").which("espeak")), "status": "ready" if (__import__("shutil").which("espeak-ng") or __import__("shutil").which("espeak")) else "missing"},
            {"name": "Hugging Face media generation", "ready": bool(os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()), "status": "connected" if (os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()) else "not connected"},
            {"name": "OpenAI Sora video", "ready": bool(os.getenv("OPENAI_API_KEY", "").strip()), "status": "connected" if os.getenv("OPENAI_API_KEY", "").strip() else "not connected"},
            {"name": "Runway video", "ready": bool(os.getenv("RUNWAYML_API_SECRET", "").strip()), "status": "connected" if os.getenv("RUNWAYML_API_SECRET", "").strip() else "not connected"},
            {"name": "Pexels video", "ready": bool(os.getenv("PEXELS_API_KEY", "").strip()), "status": "connected" if os.getenv("PEXELS_API_KEY", "").strip() else "not connected"},
            {"name": "Pixabay video", "ready": bool(os.getenv("PIXABAY_API_KEY", "").strip()), "status": "connected" if os.getenv("PIXABAY_API_KEY", "").strip() else "not connected"},
            {"name": "Durable object storage", "ready": bool(os.getenv("CLOUDFLARE_R2_BUCKET", "").strip() and os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "").strip()), "status": "connected" if os.getenv("CLOUDFLARE_R2_BUCKET", "").strip() and os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "").strip() else "not connected"},
        ]
        local_ready = all(x["ready"] for x in providers[:2])
        premium_connected = any(x["ready"] for x in providers[2:7])
        return {
            "status": "healthy" if local_ready else "degraded",
            "version": VERSION,
            "build": BUILD,
            "readiness_label": "Professional local pipeline available" if local_ready else "Renderer dependency missing",
            "local_pipeline": local_ready,
            "premium_generation_connected": premium_connected,
            "providers": providers,
            "truthful": True,
        }

    @app.get("/infinity/pro", response_class=HTMLResponse)
    def professional_ui():
        return _html()

    @app.post("/infinity/pro/create")
    async def professional_create(request: Request):
        uid = studio._get_user_id(request)
        payload = await request.json()
        objective = _safe(payload.get("objective") or payload.get("prompt") or payload.get("description"), 12000)
        if len(objective) < 8:
            raise HTTPException(422, "content command must describe a real production")
        req = {
            "title": _safe(payload.get("title") or objective[:140], 200),
            "objective": objective,
            "topic": _safe(payload.get("topic") or objective[:1000], 1200),
            "content_type": _safe(payload.get("content_type") or "video", 60),
            "format": "short" if str(payload.get("format") or "long").lower() in {"short","shorts","reel","tiktok"} else "long",
            "duration": max(20, min(3600, int(payload.get("duration") or 90))),
            "quality_preset": _safe(payload.get("quality_preset") or "balanced", 40),
            "aspect_ratio": _safe(payload.get("aspect_ratio") or "16:9", 20),
            "audience": _safe(payload.get("audience") or "general audience", 500),
            "tone": _safe(payload.get("tone") or "professional, cinematic, useful", 500),
            "language": _safe(payload.get("language") or "English", 100),
            "platforms": payload.get("platforms") if isinstance(payload.get("platforms"), list) else ["YouTube","Instagram","TikTok"],
            "voice": _safe(payload.get("voice") or "en-US-AriaNeural", 120),
            "visual_style": _safe(payload.get("visual_style") or "premium editorial, cinematic realism", 500),
            "reference_urls": payload.get("reference_urls") if isinstance(payload.get("reference_urls"), list) else [],
            "source_file_names": payload.get("source_file_names") if isinstance(payload.get("source_file_names"), list) else [],
            "professional_pipeline": True,
            "notes": "Created through canonical Professional Creator System.",
        }
        result = studio.enqueue(req, uid, None)
        result["pipeline"] = BUILD
        result["truthful"] = True
        return result

    @app.get("/infinity/pro/project/{project_id}")
    def professional_project(project_id: str, request: Request):
        uid, p = _parse_project(studio, project_id, request)
        out = studio._project_public(p)
        out["truth"] = _project_truth(studio, p) if p.get("status") in {"completed","completed_with_qc_warnings","failed"} else {"verified": False, "status": "production_in_progress", "truthful": True}
        out["pipeline"] = BUILD
        return out

    @app.get("/infinity/pro/project/{project_id}/timeline")
    def professional_timeline_get(project_id: str, request: Request):
        uid, p = _parse_project(studio, project_id, request)
        payload = _timeline_payload(studio, p)
        path = studio._project_dir(project_id) / "timeline.json"
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(stored, dict) and isinstance(stored.get("scenes"), list) and stored["scenes"]:
                    payload = stored
            except Exception:
                pass
        payload["owner"] = uid
        payload["truthful"] = True
        return payload

    @app.put("/infinity/pro/project/{project_id}/timeline")
    async def professional_timeline_put(project_id: str, request: Request):
        uid, p = _parse_project(studio, project_id, request)
        if p.get("status") not in {"completed", "completed_with_qc_warnings"}:
            raise HTTPException(409, "timeline is editable after real scene production is available")
        payload = await request.json()
        payload["project_id"] = project_id
        rows = _normalise_scene_rows(payload, studio)
        out = {
            "version": VERSION,
            "project_id": project_id,
            "title": p.get("title"),
            "fps": 30,
            "resolution": [1920,1080],
            "scenes": rows,
            "duration_seconds": round(sum(float(x["duration"]) for x in rows if x.get("enabled", True)), 3),
            "updated_at": studio.utc_iso(),
            "truthful": True,
        }
        _write_timeline(studio, project_id, out)
        return {"status":"saved","timeline":out,"truthful":True}

    @app.post("/infinity/pro/project/{project_id}/timeline/render")
    async def professional_timeline_render(project_id: str, request: Request):
        uid, p = _parse_project(studio, project_id, request)
        if p.get("status") not in {"completed", "completed_with_qc_warnings"}:
            raise HTTPException(409, "timeline render requires an existing completed scene set")
        payload = await request.json()
        payload["project_id"] = project_id
        rows = _normalise_scene_rows(payload, studio)
        timeline = {
            "version": VERSION, "project_id": project_id, "title": p.get("title"),
            "fps": 30, "resolution": [1920,1080], "scenes": rows,
            "duration_seconds": round(sum(float(x["duration"]) for x in rows if x.get("enabled", True)), 3),
            "updated_at": studio.utc_iso(), "truthful": True,
        }
        _write_timeline(studio, project_id, timeline)
        studio.audit_event(project_id, "professional_timeline_render_started", {"user_id":uid,"scene_count":len(rows)})
        try:
            rendered = _render_timeline(studio, project_id, rows)
            variants = _render_variants(studio, project_id, rendered["master"])
            try:
                _package(studio, project_id)
            except Exception as exc:
                studio.audit_event(project_id, "professional_package_failed", {"error":str(exc)[:600]})
            truth = _project_truth(studio, studio._get_project(project_id))
            studio.audit_event(project_id, "professional_timeline_render_completed", {"verified":truth.get("verified"),"quality_passed":rendered["quality"].get("passed"),"variant_count":len(variants)})
            return {
                "status": "completed" if truth.get("verified") else "completed_with_qc_review",
                "project_id": project_id,
                "quality": rendered["quality"],
                "variants": variants,
                "verified": bool(truth.get("verified")),
                "truth": truth,
                "truthful": True,
            }
        except HTTPException:
            raise
        except Exception as exc:
            studio.audit_event(project_id, "professional_timeline_render_failed", {"error":str(exc)[:1000]})
            studio._update_project(project_id, status="failed", stage="timeline_render_failed", error=str(exc)[:800])
            raise HTTPException(500, "professional timeline render failed: " + str(exc)[:500])

    @app.post("/infinity/pro/project/{project_id}/revise")
    async def professional_revise(project_id: str, request: Request):
        uid, p = _parse_project(studio, project_id, request)
        payload = await request.json()
        command = _safe(payload.get("command") or payload.get("instruction"), 6000)
        if len(command) < 4:
            raise HTTPException(422, "revision command is required")
        current = _timeline_payload(studio, p)
        if not current.get("scenes"):
            raise HTTPException(409, "no storyboard scenes available")
        # Keep the revision explicit and auditable. Natural language changes the
        # production brief; the canonical creator engine performs the actual rebuild.
        req = dict(p.get("request_json") or {})
        req["title"] = _safe(req.get("title") or p.get("title") or "Revised production", 200)
        req["objective"] = _safe(req.get("objective") or p.get("title") or "", 12000)
        req["topic"] = _safe(req.get("topic") or p.get("title") or "", 1200)
        req["notes"] = (str(req.get("notes") or "") + "\nProfessional revision: " + command).strip()[:4000]
        req["revision_of"] = project_id
        result = studio.enqueue(req, uid, None)
        studio.audit_event(project_id, "professional_revision_queued", {"child_project_id":result.get("project_id"),"command":command})
        return {"status":"queued","source_project_id":project_id,"project_id":result.get("project_id"),"truthful":True}

    @app.get("/infinity/pro/project/{project_id}/verify")
    def professional_verify(project_id: str, request: Request):
        uid, p = _parse_project(studio, project_id, request)
        truth = _project_truth(studio, p)
        return {
            "project_id": project_id,
            "verified": bool(truth.get("verified")),
            "truth": truth,
            "pipeline": BUILD,
            "checked_at": studio.utc_iso(),
            "truthful": True,
        }

    @app.post("/infinity/pro/project/{project_id}/package")
    def professional_package(project_id: str, request: Request):
        uid, p = _parse_project(studio, project_id, request)
        if p.get("status") not in {"completed","completed_with_qc_warnings"}:
            raise HTTPException(409, "project is not complete")
        truth = _project_truth(studio, p)
        if not truth.get("verified"):
            raise HTTPException(409, "delivery package is locked until professional verification passes")
        out = _package(studio, project_id)
        return {
            "status":"completed",
            "project_id":project_id,
            "asset_name":out.name,
            "download_url":f"/infinity/studio/project/{project_id}/asset/{out.name}",
            "sha256":studio.file_sha256(out),
            "truthful":True,
        }
