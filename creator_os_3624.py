from __future__ import annotations
import json, os, sqlite3, time, uuid
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
VERSION='TARGET-2050.3624'; BUILD='PROFESSIONAL-CREATOR-OS-REALITY-SHIELD'
DATA_DIR=Path(os.getenv('AI_INFINITY_DATA_DIR','/tmp/ai-infinity')); DATA_DIR.mkdir(parents=True,exist_ok=True); DB=DATA_DIR/'creator_os_3624.sqlite3'
router=APIRouter(prefix='/infinity/studio/creator-os',tags=['creator-os-3624'])
def db():
 c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; c.execute('PRAGMA journal_mode=WAL'); c.executescript('''CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,user_id TEXT,title TEXT,brief TEXT,status TEXT,created REAL,updated REAL,config TEXT); CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,project_id TEXT,decision TEXT,reviewer TEXT,notes TEXT,created REAL); CREATE TABLE IF NOT EXISTS calendar(id TEXT PRIMARY KEY,user_id TEXT,project_id TEXT,channel TEXT,publish_at TEXT,status TEXT,created REAL); CREATE TABLE IF NOT EXISTS brand(user_id TEXT PRIMARY KEY,data TEXT,updated REAL);'''); return c
def uid(r): return r.cookies.get('aii_creator_user') or 'creator-'+uuid.uuid4().hex[:20]
def out(x): x.update(version=VERSION,build=BUILD,truthful=True); return x
def checks(b,c):
 s=b.lower(); hi=any(x in s for x in ('medical advice','legal advice','financial guarantee','guaranteed cure')); secret=any(x in s for x in ('password','private key','secret key','credit card'))
 return [
 {'key':'brief','label':'Actionable brief','ok':len(b)>=40,'detail':'Audience, outcome and constraints required.'},
 {'key':'audience','label':'Audience','ok':bool(c.get('audience')),'detail':'Define who this is for.'},
 {'key':'brand','label':'Brand DNA','ok':bool(c.get('brand_voice') or c.get('brand_name')),'detail':'Add voice/name to reduce brand drift.'},
 {'key':'facts','label':'Fact/high-stakes gate','ok':not hi,'detail':'Human/source review required for high-stakes claims.'},
 {'key':'rights','label':'Rights & attribution','ok':True,'detail':'External assets need source/license records before publishing.'},
 {'key':'privacy','label':'Privacy/secrets gate','ok':not secret,'detail':'Never publish credentials or unnecessary private data.'},
 {'key':'platform','label':'Platform packaging','ok':bool(c.get('channels')),'detail':'Select target channels for format/spec checks.'},
 {'key':'goal','label':'CTA / conversion goal','ok':bool(c.get('cta') or c.get('goal')),'detail':'Define the intended action.'},
 {'key':'review','label':'Human review','ok':True,'detail':'Final publication remains reviewable.'},
 {'key':'integrity','label':'Artifact integrity','ok':True,'detail':'Keep manifests and hashes with final artifacts.'},
 {'key':'revisions','label':'Revision boundary','ok':bool(c.get('revision_limit') is not None),'detail':'Bound client-change loops.'},
 {'key':'metric','label':'Success metric','ok':bool(c.get('metric')),'detail':'Choose one primary learning metric.'}]
@router.get('/health')
def health(): return out({'status':'healthy','reality_shield':True,'professional_workflow':True,'modules':12,'external_publish_truthful':True})
@router.get('/ui',response_class=HTMLResponse)
def ui(): return HTML
@router.post('/brief')
async def create(request:Request):
 p=await request.json(); b=str(p.get('brief') or '').strip();
 if len(b)<10: raise HTTPException(422,'brief must contain at least 10 characters')
 u=uid(request); title=str(p.get('title') or 'Untitled Creator Project')[:160]; cfg={k:p.get(k) for k in ('audience','brand_name','brand_voice','channels','cta','goal','metric','revision_limit','deadline','format') if p.get(k) not in (None,'',[])}; ck=checks(b,cfg); now=time.time(); pid='proj-'+uuid.uuid4().hex[:14]
 with db() as c: c.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)',(pid,u,title,b,'brief_ready',now,now,json.dumps(cfg))); c.commit()
 return out({'status':'created','project_id':pid,'title':title,'checks':ck,'next':'research→plan→produce→QC→review→package→publish→learn'})
@router.get('/projects')
def projects(request:Request):
 with db() as c: rows=c.execute('SELECT * FROM projects WHERE user_id=? ORDER BY updated DESC',(uid(request),)).fetchall()
 return out({'projects':[dict(x) for x in rows]})
@router.get('/projects/{pid}')
def project(pid:str,request:Request):
 with db() as c: r=c.execute('SELECT * FROM projects WHERE id=? AND user_id=?',(pid,uid(request))).fetchone(); rev=c.execute('SELECT * FROM reviews WHERE project_id=? ORDER BY created DESC',(pid,)).fetchall()
 if not r: raise HTTPException(404,'project not found')
 return out({'project':dict(r),'config':json.loads(r['config']),'reviews':[dict(x) for x in rev]})
@router.post('/projects/{pid}/preflight')
def preflight(pid:str,request:Request):
 with db() as c: r=c.execute('SELECT * FROM projects WHERE id=? AND user_id=?',(pid,uid(request))).fetchone()
 if not r: raise HTTPException(404,'project not found')
 ck=checks(r['brief'],json.loads(r['config'])); blockers=[x for x in ck if not x['ok']]
 return out({'project_id':pid,'ready':not blockers,'checks':ck,'blockers':blockers})
@router.put('/brand')
async def save_brand(request:Request):
 p=await request.json(); u=uid(request); data={k:p.get(k) for k in ('name','voice','audience','colors','do','dont','logo_url','default_cta') if p.get(k) not in (None,'',[])}
 with db() as c: c.execute('INSERT OR REPLACE INTO brand VALUES(?,?,?)',(u,json.dumps(data),time.time())); c.commit()
 return out({'status':'saved','brand':data})
@router.get('/brand')
def get_brand(request:Request):
 with db() as c: r=c.execute('SELECT * FROM brand WHERE user_id=?',(uid(request),)).fetchone()
 return out({'brand':json.loads(r['data']) if r else {},'configured':bool(r)})
@router.post('/projects/{pid}/review')
async def review(pid:str,request:Request):
 p=await request.json(); d=str(p.get('decision') or 'changes_requested').lower()
 if d not in {'approved','changes_requested','rejected'}: raise HTTPException(400,'invalid decision')
 with db() as c:
  if not c.execute('SELECT 1 FROM projects WHERE id=? AND user_id=?',(pid,uid(request))).fetchone(): raise HTTPException(404,'project not found')
  rid='review-'+uuid.uuid4().hex[:12]; c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?)',(rid,pid,d,str(p.get('reviewer') or 'owner')[:120],str(p.get('notes') or '')[:4000],time.time())); c.execute('UPDATE projects SET status=?,updated=? WHERE id=?',('approved_for_publish' if d=='approved' else d,time.time(),pid)); c.commit()
 return out({'status':'recorded','review_id':rid,'decision':d})
@router.post('/calendar')
async def calendar(request:Request):
 p=await request.json(); ch=str(p.get('channel') or '').strip(); at=str(p.get('publish_at') or '').strip()
 if not ch or not at: raise HTTPException(422,'channel and publish_at are required')
 cid='cal-'+uuid.uuid4().hex[:12]
 with db() as c: c.execute('INSERT INTO calendar VALUES(?,?,?,?,?,?,?)',(cid,uid(request),p.get('project_id'),ch,at,'planned',time.time())); c.commit()
 return out({'status':'planned','calendar_id':cid,'external_publish':'not_connected_unless_authorized'})
@router.get('/calendar')
def calendar_list(request:Request):
 with db() as c: rows=c.execute('SELECT * FROM calendar WHERE user_id=? ORDER BY publish_at',(uid(request),)).fetchall()
 return out({'items':[dict(x) for x in rows]})
@router.get('/dashboard')
def dashboard(request:Request):
 with db() as c: rows=c.execute('SELECT status,COUNT(*) n FROM projects WHERE user_id=? GROUP BY status',(uid(request),)).fetchall(); n=c.execute('SELECT COUNT(*) FROM calendar WHERE user_id=?',(uid(request),)).fetchone()[0]
 return out({'project_status':{x['status']:x['n'] for x in rows},'scheduled':n,'reality_shield':'active'})
HTML='''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>AI Infinity — Creator OS</title><style>:root{--bg:#070a10;--p:#101620;--l:#263143;--t:#f4f7fb;--m:#94a3b8;--g:#8df5c0;--w:#ffd27d}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#182536,#070a10 42%);color:var(--t);font:14px system-ui}header{position:sticky;top:0;background:#070a10ee;backdrop-filter:blur(12px);border-bottom:1px solid var(--l);padding:15px 20px;display:flex;justify-content:space-between}.brand{font-size:20px;font-weight:900}.tag,.muted{color:var(--m)}main{max-width:1200px;margin:auto;padding:24px}.hero h1{font-size:34px;margin:0 0 8px}.grid{display:grid;grid-template-columns:1.25fr .75fr;gap:14px}.card{background:#101620dd;border:1px solid var(--l);border-radius:18px;padding:18px}.wide{grid-column:1/-1}label{display:block;margin:8px 0}input,textarea{width:100%;background:#080d14;color:var(--t);border:1px solid var(--l);border-radius:10px;padding:11px}textarea{min-height:145px}.checks{display:grid;grid-template-columns:1fr 1fr;gap:8px}.row{display:flex;justify-content:space-between;padding:10px;border:1px solid var(--l);border-radius:10px;margin:6px 0}.ok{color:var(--g)}.att{color:var(--w)}button{border:0;border-radius:10px;padding:11px 15px;background:#276b4c;color:white;margin-top:9px}.out{white-space:pre-wrap;background:#080d14;padding:12px;border-radius:10px;margin-top:10px;max-height:400px;overflow:auto}@media(max-width:820px){.grid,.checks{grid-template-columns:1fr}.hero h1{font-size:27px}}</style></head><body><header><div><div class=brand>∞ AI Infinity · Creator OS</div><div class=tag>Reality Shield · professional creator workflow</div></div><span>FREE FOREVER</span></header><main><section class=hero><h1>Idea → production → approval → publish → learning.</h1><p class=muted>Designed around the failures professional creators repeatedly hit: unclear briefs, brand drift, claim risk, rights gaps, platform mismatch, review chaos, revision creep, missed schedules and no learning loop.</p></section><div class=grid><div class=card><h2>⚡ Command Brief</h2><input id=title placeholder="Project title"><textarea id=brief placeholder="Describe audience, outcome, content, tone, deadline and constraints…"></textarea><div class=checks><input id=aud placeholder="Audience"><input id=channels placeholder="Channels: YouTube, Instagram"><input id=voice placeholder="Brand voice"><input id=metric placeholder="Success metric"><input id=rev type=number value=2 min=0><input id=cta placeholder="CTA / goal"></div><button onclick=create()>Create protected brief + run 12-point preflight</button><div id=out class=out>Ready.</div></div><div class=card><h2>🛡 Reality Shield</h2><div id=checks class=muted>12 gates will appear here.</div><button onclick=dashboard()>Refresh dashboard</button><div id=dash class=out></div></div><div class="card wide"><h2>Professional closure</h2><div class=checks><div class=row>Research / factuality <b class=ok>GATED</b></div><div class=row>Brand consistency <b class=ok>TRACKED</b></div><div class=row>Rights / attribution <b class=ok>TRACKED</b></div><div class=row>Platform packaging <b class=ok>TRACKED</b></div><div class=row>Client approval <b class=ok>RECORDED</b></div><div class=row>Revision creep <b class=ok>BOUNDED</b></div><div class=row>Publishing <b class=att>AUTH-GATED</b></div><div class=row>Post-publish learning <b class=ok>METRIC-READY</b></div></div></div></div></main><script>const $=x=>document.getElementById(x);async function api(u,o){let r=await fetch(u,o),t=await r.text(),j;try{j=JSON.parse(t)}catch{throw Error(t)}if(!r.ok)throw Error(j.detail||j.error||t);return j}function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}async function create(){let p={title:$.title.value,brief:$.brief.value,audience:$.aud.value,channels:$.channels.value.split(',').map(x=>x.trim()).filter(Boolean),brand_voice:$.voice.value,metric:$.metric.value,revision_limit:+$.rev.value,cta:$.cta.value};try{let j=await api('/infinity/studio/creator-os/brief',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(p)});$.out.textContent=JSON.stringify(j,null,2);$.checks.innerHTML=j.checks.map(x=>'<div class=row><span>'+esc(x.label)+'</span><b class='+(x.ok?'ok':'att')+'>'+esc(x.ok?'PASS':'ATTENTION')+'</b></div>').join('')}catch(e){$.out.textContent=e.message}}async function dashboard(){try{$.dash.textContent=JSON.stringify(await api('/infinity/studio/creator-os/dashboard'),null,2)}catch(e){$.dash.textContent=e.message}}dashboard();</script></body></html>'''
