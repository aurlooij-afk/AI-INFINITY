from __future__ import annotations

"""AI Infinity TARGET-2050.3900 — Divine Closure.

Canonical product layer over the existing real Creator Studio engine.
One user path: command -> project -> real worker -> actual artifacts -> QC/result.
No simulated completion. External publishing/accounts remain authority-gated.
"""

import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse

VERSION = "TARGET-2050.3900"
BUILD = "DIVINE-CLOSURE-ONE-CANONICAL-REAL-CREATOR-PATH"
_REGISTERED = False
_GUARD_STARTED = False


def _studio():
    import studio_ultimate
    return studio_ultimate


def _session(request: Request, response: Response) -> str:
    s = _studio()
    uid = s._get_user_id(request)
    s._set_session(response, request, uid)
    return uid


def _clean(v: Any, limit: int = 12000) -> str:
    return re.sub(r"\s+", " ", str(v or "").replace("\x00", " ")).strip()[:limit]


def _parse(command: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    text = _clean(command)
    low = text.lower()
    ctype = _clean(raw.get("content_type"), 30).lower()
    if ctype not in {"video", "podcast", "article", "social"}:
        ctype = "video"
        if re.search(r"\b(podcast|audio show)\b", low): ctype = "podcast"
        elif re.search(r"\b(article|blog post|essay)\b", low): ctype = "article"
        elif re.search(r"\b(social post|social campaign|linkedin post|instagram post|x post|tweet)\b", low): ctype = "social"

    short = bool(re.search(r"\b(short|shorts|reel|reels|tiktok|vertical|portrait)\b", low))
    fmt = _clean(raw.get("format"), 20).lower()
    if fmt not in {"long", "short", "shorts", "reel", "tiktok"}:
        fmt = "short" if short else "long"

    try: duration = int(raw.get("duration") or (60 if short else 180))
    except Exception: duration = 60 if short else 180
    m = re.search(r"\b(\d{1,4})\s*(seconds?|secs?|s|minutes?|mins?|m)\b", low)
    if m:
        n = int(m.group(1)); duration = n * 60 if m.group(2).startswith("m") else n
    duration = max(20, min(duration, 3600))

    aspect = _clean(raw.get("aspect_ratio"), 10)
    if aspect not in {"16:9","9:16","1:1","4:5"}: aspect = "9:16" if short else "16:9"
    if re.search(r"\b9\s*:?\s*16\b", low): aspect = "9:16"
    elif re.search(r"\b1\s*:?\s*1\b|\bsquare\b", low): aspect = "1:1"
    elif re.search(r"\b4\s*:?\s*5\b", low): aspect = "4:5"
    elif re.search(r"\b16\s*:?\s*9\b|\blandscape\b|\bwide\b", low): aspect = "16:9"

    platforms = raw.get("platforms") if isinstance(raw.get("platforms"), list) else []
    platforms = [_clean(x, 40).lower() for x in platforms if _clean(x, 40)][:10]
    for p in re.findall(r"\b(youtube|instagram|tiktok|linkedin|facebook|x|twitter)\b", low):
        p = "x" if p == "twitter" else p
        if p not in platforms: platforms.append(p)
    if not platforms and short: platforms = ["youtube","instagram","tiktok"]

    title = _clean(raw.get("title"), 180)
    if not title:
        title = re.split(r"[.!?\n]", text, maxsplit=1)[0][:150] or "AI Infinity Creation"

    language = _clean(raw.get("language"), 80) or "English"
    for lang in ("English","Urdu","Hindi","Pashto","Arabic"):
        if re.search(rf"\b{lang}\b", low): language = lang; break

    return {
        "title": title, "objective": text, "topic": text, "format": fmt,
        "duration": duration, "content_type": ctype,
        "audience": _clean(raw.get("audience"), 500) or "general audience",
        "tone": _clean(raw.get("tone"), 500) or "cinematic, intelligent, useful",
        "voice": _clean(raw.get("voice"), 120) or "en-US-AriaNeural",
        "language": language, "platforms": platforms,
        "brand_voice": _clean(raw.get("brand_voice"), 1200),
        "visual_style": _clean(raw.get("visual_style"), 800) or "premium editorial",
        "call_to_action": _clean(raw.get("call_to_action"), 500),
        "quality_preset": _clean(raw.get("quality_preset"), 30) or "balanced",
        "aspect_ratio": aspect,
        "reference_urls": [str(x).strip()[:1000] for x in (raw.get("reference_urls") or [])[:12] if str(x).strip()],
        "source_file_names": [str(x).strip()[:260] for x in (raw.get("source_file_names") or [])[:12] if str(x).strip()],
        "notes": _clean(raw.get("notes"), 3000),
        "priority": max(0, min(10, int(raw.get("priority") or 5))),
    }


def _idem(uid: str, command: str, data: Dict[str, Any]) -> str:
    x = {"u":uid,"c":command.lower(),"d":data.get("duration"),"f":data.get("format"),
         "t":data.get("content_type"),"a":data.get("aspect_ratio"),"l":data.get("language"),
         "p":data.get("platforms")}
    return "divine-" + hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()[:48]


def _artifacts(pid: str):
    s = _studio(); rows = []
    try: raw = s._asset_rows(pid)
    except Exception: raw = []
    for row in raw:
        path = Path(str(row.get("path") or ""))
        if path.name:
            rows.append({"name":path.name,"kind":row.get("kind"),
                         "media_type":row.get("media_type"),
                         "size_bytes":path.stat().st_size if path.is_file() else None,
                         "exists":path.is_file(),
                         "download_url":f"/infinity/studio/project/{pid}/asset/{path.name}"})
    return rows


def _project(pid: str, uid: str):
    s = _studio(); p = s._get_project(pid)
    if not p or p.get("user_id") != uid: raise HTTPException(404, "project not found")
    out = s._project_public(p); out["artifacts"] = _artifacts(pid); out["truthful"] = True
    try:
        import reality_first_3901 as _rk
        out["reality"] = _rk.truth_for_project(pid)
    except Exception as exc:
        out["reality"] = {"enabled": False, "verified": False, "state": "UNKNOWN", "error": str(exc)[:400], "truthful": True}
    return out


def _storage():
    s = _studio()
    root = Path(os.getenv("AI_INFINITY_DATA_DIR", str(getattr(s,"DATA_DIR","/tmp/ai-infinity")))).resolve()
    probe = root / ".divine-probe"
    try:
        root.mkdir(parents=True, exist_ok=True); probe.write_text("ok", encoding="utf-8"); probe.unlink(missing_ok=True)
        writable, error = True, None
    except Exception as exc:
        writable, error = False, str(exc)[:300]
    return {"path":str(root),"writable":writable,
            "persistent_server_storage":not str(root).startswith("/tmp/"),
            "persistence_mode":os.getenv("AI_INFINITY_PERSISTENCE_MODE","portable"),
            "error":error}


def health_payload():
    s = _studio()
    ffmpeg = shutil.which("ffmpeg") is not None
    ffprobe = shutil.which("ffprobe") is not None
    speech = bool(shutil.which("espeak-ng") or shutil.which("espeak"))
    try: studio_health = s.studio_health()
    except Exception as exc: studio_health = {"status":"degraded","error":str(exc)[:500],"truthful":True}
    try:
        import production_hardening
        hardening = production_hardening.runtime_health()
    except Exception:
        hardening = None
    storage = _storage()
    ready = ffmpeg and ffprobe and speech and storage["writable"]
    try:
        import reality_first_3901 as _rk
        reality = _rk.runtime_health()
    except Exception as exc:
        reality = {"enabled": False, "patched": False, "verified": False, "error": str(exc)[:400], "truthful": True}
    return {
        "status":"healthy" if ready and reality.get("patched") else "degraded",
        "version":VERSION,"build":BUILD,"truthful":True,
        "content_creation_ready":bool(ready and reality.get("patched")),
        "real_media_engine":{"ffmpeg":ffmpeg,"ffprobe":ffprobe},
        "offline_voice_engine":speech,
        "worker_started":bool(getattr(s,"WORKER_STARTED",False)),
        "free_first":True,"external_ai_optional":True,
        "hf_provider_configured":bool(os.getenv("HF_TOKEN","").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN","").strip()),
        "storage":storage,"studio":studio_health,"hardening":hardening,
        "reality_kernel":reality,
    }


def _guardian():
    while True:
        time.sleep(15)
        try:
            s = _studio()
            s._ensure_worker(None); s._ensure_scheduler()
        except Exception:
            pass


UI = r'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#05070a"><title>AI Infinity — Divine Creator OS</title>
<style>
:root{--bg:#05070a;--p:#0b1118;--p2:#101823;--l:#273443;--t:#f3f7fb;--m:#96a6b8;--g:#d1ff8a;--mint:#9cf2ca;--w:#f2d47c;--r:#ff9aaa;--R:20px}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,rgba(156,242,202,.11),transparent 32%),linear-gradient(180deg,#070a0e,#030507);color:var(--t);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}.app{display:grid;grid-template-columns:210px 1fr;min-height:100vh}.side{position:sticky;top:0;height:100vh;padding:16px 10px;background:#06090ded;border-right:1px solid var(--l)}.brand{padding:8px 9px 18px;font-weight:950}.brand small{display:block;color:var(--m);font-size:9px;margin-top:2px}.nav{display:grid;gap:5px}.nav button{padding:11px;border:1px solid transparent;background:transparent;color:#aeb9c6;border-radius:12px;text-align:left;cursor:pointer}.nav button:hover,.nav button.on{background:#111a23;border-color:#2a3948;color:#fff}.truth{margin-top:18px;padding:11px;border:1px solid #315747;border-radius:12px;background:#09110e;color:var(--mint);font-size:9px}.main{min-width:0}.top{height:62px;position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;padding:0 18px;border-bottom:1px solid var(--l);background:#05080ddd;backdrop-filter:blur(18px)}.pill{padding:6px 9px;border:1px solid #2f3d4b;border-radius:999px;font-size:9px;color:var(--m)}.pill.good{border-color:#315747;color:var(--mint)}.pill.free{border-color:#4e5e31;color:var(--g)}.wrap{max-width:1450px;margin:auto;padding:22px}.hero{display:grid;grid-template-columns:1.5fr .5fr;gap:12px}.card{border:1px solid var(--l);border-radius:var(--R);background:linear-gradient(180deg,#0e151d,#070b10);box-shadow:0 24px 80px #0007}.pad{padding:17px}.maincard{padding:28px;overflow:hidden}.eyebrow{color:#748399;font-size:8px;text-transform:uppercase;letter-spacing:.18em;font-weight:950}.hero h1{font-size:clamp(42px,6vw,80px);line-height:.88;letter-spacing:-.07em;margin:10px 0 13px}.lead{max-width:860px;color:#adbac7}.cmd{margin-top:18px;border:1px solid #3b4d60;border-radius:17px;background:#05090d;overflow:hidden}.cmd textarea{width:100%;min-height:145px;border:0;outline:0;resize:vertical;background:transparent;color:#f4f8fb;padding:16px;font-size:15px}.bar{padding:9px;border-top:1px solid #202a35;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}.btn{padding:9px 11px;border-radius:11px;border:1px solid #324252;background:#0d151d;color:#eef5fa;cursor:pointer}.btn:hover{background:#16212c}.primary{background:linear-gradient(135deg,var(--g),#91ecbf);color:#061008;border-color:#e4ffc0;font-weight:950}.danger{color:var(--r)}.controls{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:8px}.field{width:100%;height:39px;border:1px solid #2a3745;border-radius:10px;background:#080e14;color:#edf4f8;padding:0 10px}.label{display:block;color:#657489;font-size:8px;text-transform:uppercase;letter-spacing:.1em;font-weight:950;margin:0 0 4px}.stats{display:grid;grid-template-columns:1fr 1fr;gap:7px}.stat{padding:11px;border:1px solid #222d38;border-radius:12px;background:#090f15}.stat span{display:block;color:#637286;font-size:8px;text-transform:uppercase}.stat b{display:block;font-size:24px;margin-top:3px}.notice{padding:9px 10px;border:1px solid #2a3744;border-radius:11px;background:#090f15;color:#aab6c4;font-size:9px}.notice.good{border-color:#315847;color:var(--mint)}.section{display:none}.section.on{display:block}.head{display:flex;justify-content:space-between;align-items:end;margin:6px 0 10px}.head h2{margin:0;font-size:19px}.head p{margin:3px 0 0;color:var(--m);font-size:9px}.grid2{display:grid;grid-template-columns:1.2fr .8fr;gap:12px}.list{display:grid;gap:7px}.item,.project{padding:11px;border:1px solid #222d38;border-radius:12px;background:#090f15}.project{cursor:pointer}.row{display:flex;justify-content:space-between;gap:8px;align-items:center}.badge{font-size:8px;padding:4px 7px;border-radius:999px;border:1px solid #314050;color:#aebbc9}.good{color:var(--mint)}.bad{color:var(--r)}.live{color:var(--w)}.progress{height:7px;margin-top:10px;background:#131c25;border-radius:99px;overflow:hidden}.progress i{display:block;height:100%;width:0;background:linear-gradient(90deg,#7fe0bf,#d1ff8a)}.meta{display:flex;justify-content:space-between;color:#66758a;font-size:8px;margin-top:4px}.stages{display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin-top:10px}.stage{padding:7px;border:1px solid #222d38;border-radius:9px;font-size:8px}.stage.done{border-color:#315847;color:var(--mint)}.stage.live{border-color:#5b512f;color:var(--w)}.preview{margin-top:10px;aspect-ratio:16/9;background:#020406;border:1px solid #222d38;border-radius:14px;overflow:hidden;display:grid;place-items:center}.preview video{width:100%;height:100%;object-fit:contain}.assets{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:10px}.asset{display:flex;justify-content:space-between;padding:8px;border:1px solid #24303c;border-radius:10px;background:#080e14;font-size:8px}.asset b{max-width:65%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.mono{white-space:pre-wrap;word-break:break-word;font:8px/1.55 ui-monospace,monospace;color:#8291a5}.empty{text-align:center;padding:26px;color:#647287;font-size:9px}@media(max-width:1000px){.hero,.grid2{grid-template-columns:1fr}.controls{grid-template-columns:1fr 1fr}}@media(max-width:650px){.app{display:block}.side{position:fixed;left:0;right:0;bottom:0;top:auto;width:auto;height:auto;padding:5px;z-index:20;border-top:1px solid var(--l)}.brand,.truth{display:none}.nav{grid-template-columns:repeat(3,1fr)}.nav button{text-align:center;padding:8px}.top{height:56px;padding:0 9px}.top .pill{display:none}.wrap{padding:9px 8px 72px}.maincard{padding:18px}.controls,.assets{grid-template-columns:1fr 1fr}.hero h1{font-size:45px}}@media(max-width:420px){.controls,.assets{grid-template-columns:1fr}}
</style></head>
<body><div class="app"><aside class="side"><div class="brand">∞ AI Infinity<small>Divine Creator OS · real execution surface</small></div><nav class="nav"><button class="on" onclick="go('create')">✦ Create</button><button onclick="go('projects')">▦ Projects</button><button onclick="go('system')">✓ System</button></nav><div class="truth"><b>Reality rule</b><br>Complete means actual backend state + actual artifact.</div></aside><section class="main"><header class="top"><div><b>AI Infinity · One canonical creation path</b><div style="color:#637184;font-size:9px">intent → research → production → QC → artifact</div></div><div style="display:flex;gap:7px;align-items:center"><span id="hp" class="pill">checking…</span><span class="pill free">FREE-FIRST CORE</span><button class="btn" onclick="refresh()">Refresh</button></div></header><main class="wrap">
<section id="create" class="section on"><div class="hero"><div class="card maincard"><div class="eyebrow">TARGET 3900 · DIVINE CLOSURE</div><h1>Make the thing.<br>For real.</h1><p class="lead">A single natural-language command controls the existing production engine. AI Infinity queues the real job, exposes its live state, and puts actual output files in your hands.</p><div class="cmd"><textarea id="cmd" placeholder="Create a 45-second cinematic short about solar power for students, with narration, captions, thumbnail and social package."></textarea><div class="bar"><span style="color:#637184;font-size:9px">Built-in media + offline voice path; external AI is optional.</span><button class="btn primary" onclick="createJob()">Create real content ↗</button></div></div><div class="controls"><div><label class="label">Content</label><select id="ct" class="field"><option value="video">Video</option><option value="podcast">Podcast</option><option value="article">Article</option><option value="social">Social package</option></select></div><div><label class="label">Duration</label><input id="dur" class="field" type="number" min="20" max="3600" value="60"></div><div><label class="label">Format</label><select id="fmt" class="field"><option value="short">Short</option><option value="long">Long-form</option></select></div><div><label class="label">Aspect</label><select id="asp" class="field"><option>9:16</option><option>16:9</option><option>1:1</option><option>4:5</option></select></div></div><div id="notice" style="margin-top:9px"></div></div><div><div class="card pad"><div class="head"><div><h2>Live state</h2><p>real runtime facts</p></div></div><div class="stats"><div class="stat"><span>Projects</span><b id="pc">—</b></div><div class="stat"><span>Active</span><b id="ac">—</b></div><div class="stat"><span>Completed</span><b id="dc">—</b></div><div class="stat"><span>Free core</span><b>ON</b></div></div><div id="sn" class="notice" style="margin-top:9px">Loading…</div></div><div class="card pad" style="margin-top:10px"><div class="head"><div><h2>Fast starts</h2><p>choose a ready brief</p></div></div><div class="list"><button class="btn" onclick="preset('Create a 45-second cinematic short explaining how solar power works for students, with narration, captions, thumbnail and social package.')">45s Solar Explainer</button><button class="btn" onclick="preset('Create a 2-minute factual documentary short about the rise of artificial intelligence, elegant and cinematic.')">2m AI Documentary</button><button class="btn" onclick="preset('Create a vertical motivational reel about learning from failure, with voiceover, captions and a strong closing message.')">Vertical Motivation</button></div></div></div></div></section>
<section id="projects" class="section"><div class="head"><div><h2>Your production universe</h2><p>every row is actual Studio state</p></div><button class="btn primary" onclick="go('create')">＋ New</button></div><div class="grid2"><div class="card pad"><div id="plist" class="list"><div class="empty">No projects yet.</div></div></div><div class="card pad"><div id="detail"><div class="empty">Select a project.</div></div></div></div></section>
<section id="system" class="section"><div class="head"><div><h2>System truth</h2><p>proof, not marketing</p></div></div><div class="grid2"><div class="card pad"><div class="eyebrow">Runtime</div><pre id="rt" class="mono">loading…</pre></div><div class="card pad"><div class="eyebrow">Studio</div><pre id="st" class="mono">loading…</pre></div></div><div class="card pad" style="margin-top:12px"><div class="notice good"><b>Genuine closure:</b> built-in production is considered ready only when the runtime checks are green and the existing Studio worker accepts the job. External publishing/accounts remain real only when connected.</div></div></section>
</main></section></div><div id="toast" class="notice" style="display:none;position:fixed;right:12px;bottom:12px;z-index:50;max-width:380px"></div>
<script>
const $=x=>document.getElementById(x);let current=null,timer=null;
async function api(u,o){const r=await fetch(u,{credentials:'same-origin',...o});const t=await r.text();let j={};try{j=JSON.parse(t)}catch{throw Error(t||('HTTP '+r.status))}if(!r.ok)throw Error(j.detail||j.error||t);return j}
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function go(id){document.querySelectorAll('.section').forEach(x=>x.classList.toggle('on',x.id===id));document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('on',(b.getAttribute('onclick')||'').includes("'"+id+"'")));if(id==='projects')loadProjects();if(id==='system')loadSystem()}
function msg(s){$('notice').innerHTML='<div class="notice">'+esc(s)+'</div>'}
function preset(s){$('cmd').value=s;$('cmd').focus()}
async function refresh(){try{const b=await api('/infinity/divine/bootstrap'),h=b.health,p=b.projects||[];$('pc').textContent=p.length;$('ac').textContent=p.filter(x=>['queued','running','producing'].includes(x.status)).length;$('dc').textContent=p.filter(x=>String(x.status||'').startsWith('completed')).length;$('hp').textContent=h.content_creation_ready?'● creator ready':'● needs attention';$('hp').className='pill '+(h.content_creation_ready?'good':'');$('sn').innerHTML=h.content_creation_ready?'<b>Real creation engine ready.</b> Built-in media path is available.':'<b>Runtime attention required.</b> System shows the exact gap.'}catch(e){$('sn').textContent=e.message}}
async function createJob(){const c=$('cmd').value.trim();if(!c)return msg('Describe what you want to create first.');msg('Queueing the real production job…');try{const r=await api('/infinity/divine/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:c,content_type:$('ct').value,duration:+$('dur').value,format:$('fmt').value,aspect_ratio:$('asp').value})});current=r.project_id;localStorage.setItem('ai-infinity-divine-project',current);msg('Production queued: '+r.project_id);go('projects');inspect(current)}catch(e){$('notice').innerHTML='<div class="notice warn">'+esc(e.message)+'</div>'}}
function cls(s){s=String(s||'');return s.startsWith('completed')?'good':(s==='failed'||s==='cancelled')?'bad':['queued','running','producing'].includes(s)?'live':''}
function render(p){const done=String(p.status||'').startsWith('completed'),failed=['failed','cancelled'].includes(p.status),pct=Math.max(0,Math.min(100,Number(p.progress||0))),r=p.reality||{},verified=!!r.verified,truthState=String(r.state||'UNKNOWN');const order=['queued','research','creative_direction','production_ready','producing','quality_control','packaging','complete'],bi=order.indexOf(String(p.stage||'queued'));const stages=order.map((x,i)=>'<div class="stage '+(bi>i?'done':bi===i?'live':'')+'">'+esc(x.replaceAll('_',' '))+'</div>').join('');const assets=(p.artifacts||[]).filter(a=>a.exists).map(a=>'<a class="asset" href="'+esc(a.download_url)+'" target="_blank" rel="noopener"><b>'+esc(a.name)+'</b><span>open</span></a>').join('');const proof=verified?'<div class="notice good" style="margin-top:9px"><b>VERIFIED BY REALITY KERNEL</b> · filesystem artifact, independent inspection, hashes and QC agree.</div>':truthState==='DELIVERED_WITH_QC_WARNINGS'?'<div class="notice" style="margin-top:9px;color:var(--w)"><b>DELIVERED WITH QC WARNINGS</b> · output exists, but independent quality proof is not fully green.</div>':'<div class="notice" style="margin-top:9px">Reality state: <b>'+esc(truthState)+'</b> · completion is not asserted without artifact evidence.</div>';$('detail').innerHTML='<div class="row"><div><div class="eyebrow">REAL PROJECT</div><h2 style="margin:5px 0">'+esc(p.title||p.project_id)+'</h2><div class="muted">'+esc(p.project_id)+'</div></div><span class="badge '+cls(p.status)+'">'+esc(p.status)+'</span></div><div class="progress"><i style="width:'+pct+'%"></i></div><div class="meta"><span>'+esc(p.stage||'queued')+'</span><span>'+pct+'%</span></div><div class="stages">'+stages+'</div>'+proof+(done&&verified?'<div class="preview"><video controls playsinline preload="metadata" src="/infinity/studio/project/'+encodeURIComponent(p.project_id)+'/asset/final.mp4"></video></div>':'<div class="preview"><div class="empty">'+(failed?'Production did not complete; exact error below.':verified?'Real preview appears after the artifact exists.':'Preview appears only after the kernel proves a real artifact exists.')+'</div></div>')+(failed?'<div class="notice" style="margin-top:9px;color:var(--r)">'+esc(p.error||'Production failed')+'</div>':'')+(assets?'<div class="assets">'+assets+'</div>':'')+'<div class="bar" style="margin-top:10px;padding:0;border:0">'+(!done&&!failed?'<button class="btn danger" onclick="cancelJob(\\''+encodeURIComponent(p.project_id)+'\\')">Cancel</button>':'')+(failed?'<button class="btn" onclick="retryJob(\\''+encodeURIComponent(p.project_id)+'\\')">Retry</button>':'')+'</div>'}
async function inspect(pid){clearTimeout(timer);try{const p=await api('/infinity/divine/project/'+encodeURIComponent(pid));render(p);if(['completed','completed_with_qc_warnings','failed','cancelled'].includes(p.status)){loadProjects();return}timer=setTimeout(()=>inspect(pid),1500)}catch(e){$('detail').innerHTML='<div class="notice" style="color:var(--r)">'+esc(e.message)+'</div>'}}
async function loadProjects(){try{const d=await api('/infinity/divine/projects'),rows=d.projects||[];$('plist').innerHTML=rows.length?rows.map(p=>'<div class="project" onclick="inspect(\\''+esc(p.project_id)+'\\')"><div class="row"><b>'+esc(p.title||'Untitled')+'</b><span class="badge '+cls(p.status)+'">'+esc(p.status)+'</span></div><div class="muted" style="margin-top:4px">'+esc(p.stage||'queued')+' · '+Math.round(Number(p.progress||0))+'%</div></div>').join(''):'<div class="empty">No projects yet.</div>'}catch(e){$('plist').innerHTML='<div class="notice" style="color:var(--r)">'+esc(e.message)+'</div>'}}
async function cancelJob(pid){try{await api('/infinity/divine/project/'+pid+'/cancel',{method:'POST'});inspect(decodeURIComponent(pid))}catch(e){msg(e.message)}}
async function retryJob(pid){try{await api('/infinity/divine/project/'+pid+'/retry',{method:'POST'});inspect(decodeURIComponent(pid))}catch(e){msg(e.message)}}
async function loadSystem(){try{const d=await api('/infinity/divine/health');$('rt').textContent=JSON.stringify({status:d.status,content_creation_ready:d.content_creation_ready,media:d.real_media_engine,offline_voice_engine:d.offline_voice_engine,worker_started:d.worker_started,storage:d.storage,free_first:d.free_first,external_ai_optional:d.external_ai_optional,hf_provider_configured:d.hf_provider_configured,reality_kernel:d.reality_kernel},null,2);$('st').textContent=JSON.stringify(d.studio||{},null,2)}catch(e){$('rt').textContent=e.message}}
async function boot(){await refresh();await loadProjects();const p=localStorage.getItem('ai-infinity-divine-project');if(p){current=p;go('projects');inspect(p)}}boot();
</script></body></html>''';


def register(app: Any) -> None:
    global _REGISTERED, _GUARD_STARTED
    if _REGISTERED:
        return

    try:
        import reality_first_3901 as _rk
        _rk.register(app)
    except Exception:
        pass

    async def create_impl(payload: Dict[str, Any], request: Request, response: Response):
        uid = _session(request, response)
        command = _clean(payload.get("command") or payload.get("objective"))
        if not command:
            raise HTTPException(422, "command is required")
        s = _studio()
        data = _parse(command, payload)
        data["idempotency_key"] = _idem(uid, command, data)
        result = s.enqueue(data, uid, None)
        return {
            "status": result.get("status","queued"),
            "project_id": result.get("project_id"),
            "title": result.get("title"),
            "interpretation": {k:data.get(k) for k in ("title","format","duration","content_type","aspect_ratio","language","platforms")},
            "version":VERSION,"build":BUILD,"truthful":True
        }

    @app.get("/infinity/divine/health")
    def divine_health():
        return health_payload()

    @app.get("/infinity/divine/bootstrap")
    def divine_bootstrap(request: Request, response: Response):
        uid = _session(request, response)
        return {"version":VERSION,"build":BUILD,"health":health_payload(),
                "projects":_studio()._project_list(uid,100),"truthful":True}

    @app.get("/infinity/divine/projects")
    def divine_projects(request: Request, response: Response):
        uid = _session(request, response)
        return {"projects":_studio()._project_list(uid,100),"truthful":True}

    @app.post("/infinity/divine/create")
    async def divine_create(payload: Dict[str, Any], request: Request, response: Response):
        return await create_impl(payload, request, response)

    @app.post("/infinity/divine/command")
    async def divine_command(payload: Dict[str, Any], request: Request, response: Response):
        return await create_impl(payload, request, response)

    @app.get("/infinity/divine/project/{project_id}")
    def divine_project(project_id: str, request: Request, response: Response):
        return _project(project_id, _session(request, response))

    @app.post("/infinity/divine/project/{project_id}/cancel")
    def divine_cancel(project_id: str, request: Request, response: Response):
        uid = _session(request,response); s = _studio(); p = s._get_project(project_id)
        if not p or p.get("user_id") != uid: raise HTTPException(404,"project not found")
        if p.get("status") in {"completed","completed_with_qc_warnings"}: raise HTTPException(409,"project is already complete")
        s.stop_project_process(project_id); s._update_project(project_id,status="cancelled",stage="cancelled",error="Cancelled by creator")
        try: s.audit_event(project_id,"divine_cancelled",{"by":"creator"})
        except Exception: pass
        return {"status":"cancelled","project_id":project_id,"truthful":True}

    @app.post("/infinity/divine/project/{project_id}/retry")
    def divine_retry(project_id: str, request: Request, response: Response):
        uid = _session(request,response); s = _studio(); p = s._get_project(project_id)
        if not p or p.get("user_id") != uid: raise HTTPException(404,"project not found")
        if p.get("status") not in {"failed","cancelled","completed_with_qc_warnings"}: raise HTTPException(409,"project is not retryable")
        s._update_project(project_id,status="queued",stage="queued",progress=0,error=None); s._ensure_worker(None)
        return {"status":"queued","project_id":project_id,"truthful":True}

    @app.get("/infinity/divine", response_class=HTMLResponse, include_in_schema=False)
    def divine_surface():
        return HTMLResponse(UI, headers={"cache-control":"no-store, no-cache, must-revalidate, max-age=0","x-ai-infinity-ui":"3900-divine"})

    try:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r,"path",None) not in {"/","/home","/studio"}]
    except Exception:
        pass

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def root():
        return divine_surface()

    @app.get("/home", response_class=HTMLResponse, include_in_schema=False)
    def home():
        return divine_surface()

    @app.get("/studio", response_class=HTMLResponse, include_in_schema=False)
    def studio():
        return divine_surface()

    try:
        app.state.ai_infinity_divine = {"version":VERSION,"build":BUILD,"truthful":True}
    except Exception:
        pass

    _REGISTERED = True
    if not _GUARD_STARTED:
        guard = threading.Thread(target=_guardian, name="ai-infinity-divine-guardian", daemon=True)
        guard.start(); _GUARD_STARTED = True
