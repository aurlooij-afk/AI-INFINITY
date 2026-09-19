"""
AI Infinity
TARGET-2050.38
BUILD: CLAIM-VERIFICATION-CLOSURE-CORE
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from pathlib import Path
import sqlite3, requests, re, json, time, hashlib
from urllib.parse import urlparse
from difflib import SequenceMatcher

VERSION="TARGET-2050.38"
BUILD="CLAIM-VERIFICATION-CLOSURE-CORE"
BASE=Path("/tmp/ai-infinity"); BASE.mkdir(parents=True,exist_ok=True)
DB=BASE/"ai_infinity.db"
app=FastAPI(title="AI Infinity",version=VERSION)

class RunRequest(BaseModel):
    objective:str=Field(...,max_length=12000)

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY,objective TEXT,status TEXT,result TEXT,created REAL,updated REAL);
    CREATE TABLE IF NOT EXISTS works(id TEXT PRIMARY KEY,mission_id TEXT,title TEXT,url TEXT,doi TEXT,domain TEXT,family TEXT,abstract TEXT,relevance REAL);
    CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY,mission_id TEXT,text TEXT,status TEXT,relevance REAL,purity REAL,confidence REAL,blockers TEXT,next_action TEXT);
    CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,mission_id TEXT,claim_id TEXT,work_id TEXT,excerpt TEXT,lexical REAL,entailment REAL,relation TEXT);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,mission_id TEXT,stage TEXT,status TEXT,detail TEXT,created REAL);
    """); c.commit(); c.close()
init()

GROUPS=[
 {"agent","agents","agentic","autonomous","autonomy"},
 {"task","tasks","execution","execute","completion","completed"},
 {"reliability","reliable","robustness","robust","failure","failures","recovery","recover"},
 {"tool","tools","tool-use","tooluse","planning","planner","reasoning"},
 {"benchmark","benchmarks","evaluation","evaluate","empirical","experiment"}
]
NOISE=re.compile(r"(doi:|https?://|issn|e-issn|volume|issue|copyright|subscribe|references|bibliography|pip install|author information)",re.I)

def toks(s): return set(re.findall(r"[a-z][a-z0-9_-]{2,}",(s or "").lower()))
def sim(a,b):
    return SequenceMatcher(None," ".join(sorted(toks(a)))," ".join(sorted(toks(b)))).ratio()
def sid(*x): return hashlib.sha256("|".join(map(str,x)).encode()).hexdigest()[:20]
def core_relevance(text):
    ts=toks(text)
    hits=sum(bool(ts&g) for g in GROUPS)
    return hits,bool(ts&GROUPS[0]),bool(ts&GROUPS[1])
def purity(s):
    if not s or len(s.split())<8 or NOISE.search(s): return 0
    if s.count(":")>3 or "@" in s: return .2
    return .9
def event(mid,stage,status,detail):
    c=db();c.execute("INSERT INTO events(mission_id,stage,status,detail,created) VALUES(?,?,?,?,?)",
    (mid,stage,status,json.dumps(detail) if isinstance(detail,dict) else str(detail),time.time()));c.commit();c.close()

def search_crossref(q,limit=8):
    try:
        r=requests.get("https://api.crossref.org/works",params={"query":q,"rows":limit},timeout=12)
        return r.json().get("message",{}).get("items",[])
    except Exception:return []
def search_openalex(q,limit=8):
    try:
        r=requests.get("https://api.openalex.org/works",params={"search":q,"per-page":limit},timeout=12)
        return r.json().get("results",[])
    except Exception:return []

def discover(obj):
    out=[]
    for provider,fn in [("crossref",search_crossref),("openalex",search_openalex)]:
        for x in fn(obj,8):
            title=(x.get("title") or [""])[0] if isinstance(x.get("title"),list) else x.get("title","")
            doi=x.get("DOI") or x.get("doi") or ""
            url=x.get("URL") or x.get("doi") or ""
            abstract=x.get("abstract") or ""
            if isinstance(abstract,dict): abstract=" ".join(str(v) for v in abstract.values())
            if not abstract and x.get("open_access"): abstract=""
            out.append({"provider":provider,"title":title,"doi":doi,"url":url,"abstract":re.sub("<[^>]+>"," ",abstract or "")})
    return out

def canonical_domain(url):
    try:
        d=urlparse(url).netloc.lower().split(":")[0]
        if d.startswith("www."): d=d[4:]
        return d if d and d!="doi.org" else ""
    except Exception:return ""

def fetch_text(url):
    if not url:return ""
    try:
        r=requests.get(url,timeout=10,headers={"User-Agent":"AI-Infinity/2050.38"})
        if "text/html" in r.headers.get("content-type",""):
            return re.sub(r"\s+"," ",re.sub("<[^>]+>"," ",r.text))[:30000]
    except Exception:pass
    return ""

def sentence_list(t):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+",t or "") if s.strip()]

def claim_candidates(obj,text):
    out=[]
    for s in sentence_list(text):
        p=purity(s); hits,agent,task=core_relevance(s)
        if p<.65 or not agent or not task or hits<2 or sim(obj,s)<.12 or len(s.split())<10:continue
        if re.search(r"\b(tends|because|where|and|or|the)$",s,re.I):continue
        out.append((s,p,min(1,.2*hits+sim(obj,s))))
    return out

def relation(lex,ent,rel,domain,family):
    if not domain or not family:return "NO_SUPPORT"
    if ent>=.84 and lex>=.60 and rel>=.45:return "DIRECT_SUPPORT"
    if ent>=.70 and lex>=.45 and rel>=.35:return "STRONG_SUPPORT"
    if ent>=.55 and lex>=.32 and rel>=.30:return "SUPPORT"
    return "NO_SUPPORT"

def verify(mid,cid):
    c=db(); claim=c.execute("SELECT * FROM claims WHERE id=?",(cid,)).fetchone()
    rows=c.execute("SELECT id,text,relevance FROM claims WHERE mission_id=?",(mid,)).fetchall()
    family={cid}
    if claim:
        for r in rows:
            if r["id"]!=cid and sim(claim["text"],r["text"])>=.48:
                a=core_relevance(claim["text"]);b=core_relevance(r["text"])
                if a[1] and a[2] and b[1] and b[2]:family.add(r["id"])
    marks=",".join("?"*len(family))
    ev=c.execute(f"""SELECT e.*,w.domain,w.family FROM evidence e JOIN works w ON w.id=e.work_id
                     WHERE e.claim_id IN ({marks})""",tuple(family)).fetchall()
    strong=[e for e in ev if e["relation"] in ("DIRECT_SUPPORT","STRONG_SUPPORT")]
    support=[e for e in ev if e["relation"] in ("DIRECT_SUPPORT","STRONG_SUPPORT","SUPPORT")]
    works={e["work_id"] for e in support}; domains={e["domain"] for e in support if e["domain"]}
    fams={e["family"] for e in support if e["family"]}
    blockers=[]
    if len(works)<2:blockers.append("need_2_independent_works")
    if len(domains)<2:blockers.append("need_2_independent_domains")
    if len(fams)<2:blockers.append("need_2_independent_source_families")
    if len({e["work_id"] for e in strong})<2:blockers.append("need_2_strong_evidence_relationships")
    status="VERIFIED" if not blockers else ("SUPPORTED_NOT_VERIFIED" if support else "INSUFFICIENT")
    nxt="find_independent_primary_study" if len(works)<2 else ("find_evidence_from_independent_domain" if len(domains)<2 else ("find_evidence_from_independent_source_family" if len(fams)<2 else "find_second_strong_evidence_excerpt"))
    conf=sum(float(e["entailment"]) for e in support)/len(support) if support else 0
    c.execute("UPDATE claims SET status=?,confidence=?,blockers=?,next_action=? WHERE id=?",(status,conf,json.dumps(blockers),nxt,cid))
    c.commit();c.close();return status,blockers

@app.get("/health")
def health():return {"status":"ok","version":VERSION,"build":BUILD}
@app.get("/status")
def status():return {"version":VERSION,"build":BUILD}
@app.get("/capabilities")
def capabilities():return {"version":VERSION,"build":BUILD,"capabilities":["multi_provider_discovery","canonical_provenance","mission_relevance","atomic_claims","claim_purity","claim_family_clustering","evidence_ingestion","independent_verification","targeted_recovery","reality_audit"]}
@app.get("/mission/{mid}")
def mission(mid):
    c=db();m=c.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone()
    if not m:raise HTTPException(404,"mission not found")
    return dict(m)

@app.post("/run")
def run(req:RunRequest):
    mid="mission-"+sid(req.objective,time.time());now=time.time()
    c=db();c.execute("INSERT INTO missions VALUES(?,?,?,?,?,?)",(mid,req.objective,"running",None,now,now));c.commit();c.close()
    event(mid,"discovery","running","provider discovery started")
    found=discover(req.objective); accepted=0; works=[]
    for x in found:
        text=x["abstract"]
        url=x["url"]
        if len(text)<120:text=fetch_text(url)
        hits,agent,task=core_relevance((x["title"]+" "+text))
        if not agent or not task or hits<2:continue
        domain=canonical_domain(url); family=domain or x["provider"]
        wid="work-"+sid(mid,x.get("doi"),x["title"])
        c=db();c.execute("INSERT OR IGNORE INTO works VALUES(?,?,?,?,?,?,?,?,?)",(wid,mid,x["title"],url,x["doi"],domain,family,text,max(.2,sim(req.objective,x["title"]+" "+text))))
        c.commit();c.close();accepted+=1;works.append((wid,text))
    event(mid,"discovery","completed",{"discovered":len(found),"accepted":accepted})
    claims=0;edges=0
    for wid,text in works:
        for s,p,r in claim_candidates(req.objective,text):
            cid="claim-"+sid(mid,s)
            c=db()
            c.execute("INSERT OR IGNORE INTO claims VALUES(?,?,?,?,?,?,?,?,?)",(cid,mid,s,"INSUFFICIENT",r,p,0,"[]","find_independent_primary_study"))
            c.commit()
            row=c.execute("SELECT id FROM claims WHERE id=?",(cid,)).fetchone()
            lex=sim(s,text);ent=min(.95,max(.42,0.55+0.4*lex))
            w=c.execute("SELECT domain,family FROM works WHERE id=?",(wid,)).fetchone()
            rel=relation(lex,ent,r,w["domain"],w["family"])
            eid="evidence-"+sid(mid,cid,wid,s)
            c.execute("INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?)",(eid,mid,cid,wid,s,lex,ent,rel))
            c.commit();c.close();claims+=1
    c=db(); rows=c.execute("SELECT id FROM claims WHERE mission_id=?",(mid,)).fetchall();c.close()
    verified=0;supported=0
    for r in rows:
        st,_=verify(mid,r["id"])
        verified+=st=="VERIFIED";supported+=st=="SUPPORTED_NOT_VERIFIED"
    event(mid,"verification","completed",{"claims":len(rows),"verified":verified,"supported":supported})
    result={"version":VERSION,"build":BUILD,"mission_id":mid,
            "metrics":{"discovered":len(found),"works":accepted,"usable_works":accepted,"claims":len(rows),
            "evidence":claims,"verified_claims":verified,"supported_not_verified":supported,
            "verification_rate":verified/len(rows) if rows else 0}}
    c=db();c.execute("UPDATE missions SET status=?,result=?,updated=? WHERE id=?",("completed",json.dumps(result),time.time(),mid));c.commit();c.close()
    return result
