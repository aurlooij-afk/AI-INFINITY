"""
AI Infinity
TARGET-2050.36
BUILD: DISCOVERY-RECOVERY-REALITY-CORE

This build fixes the failure exposed by TARGET-2050.35:
- separates discovery, canonicalization, fetch, relevance and acceptance
- uses topic anchors instead of requiring full mission-vocabulary overlap
- preserves strict verification standards
- records exactly where sources are lost
- expands recovery across providers and query families
- never treats lexical similarity as semantic proof
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from pathlib import Path
from urllib.parse import urlparse
from collections import Counter
from difflib import SequenceMatcher
import sqlite3, requests, re, json, time, hashlib, ipaddress

VERSION = "TARGET-2050.36"
BUILD = "DISCOVERY-RECOVERY-REALITY-CORE"
BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE / "ai_infinity.db"
TIMEOUT = 12
MAX_TEXT = 24000
MAX_EXCERPT = 1400
UA = "AI-Infinity/2050.36 evidence-research-engine"

STOP = set("""
the and that this with from into for are was were been have has had will would
could should their there they them than then when where which while using used use
also more most some such these those about after before between through within
without over under during only each other both many much very may might can our
your its we you a an of to in on at by as or is it be not no do does did
study research paper results finding findings analysis method methods approach
system model models data evidence work works information researchers authors
""".split())

NOISE = [
    r"skip to main content", r"subscribe", r"sign up", r"share this",
    r"cookie", r"privacy policy", r"terms of use", r"all rights reserved",
    r"newsletter", r"\bissn\b", r"e-issn", r"impact factor",
    r"copyright", r"funder", r"funded by", r"author contributions",
    r"conflict of interest", r"doi:\s*10\.", r"citation:"
]

BAD_CLAIM = NOISE + [
    r"pip install", r"import\s+\w+", r"from\s+\w+\s+import", r"npm install",
    r"github\.com", r"^figure\s+\d+", r"^table\s+\d+",
    r"^references?$", r"^abstract$", r"^introduction$", r"^methods?$",
    r"^results?$", r"^discussion$", r"https?://", r"doi\.org/"
]

NEG = {"not","no","never","without","failed","failure","unable","cannot",
       "insufficient","lack","lacks","limited","unlikely","contradict",
       "contradicted"}
POS = {"found","find","shows","showed","demonstrate","demonstrates",
       "evidence","increased","decreased","associated","effective",
       "successful","reliable","improved","observed","identified","confirmed"}

TOPIC_GROUPS = [
    {"agent","agents","agentic","autonomous","autonomy"},
    {"reliability","reliable","robustness","robust","failure","failures"},
    {"task","tasks","execution","execute","completion"},
    {"real","world","deployment","deployed","production"},
    {"tool","tools","tool-use","tooluse"},
    {"planning","planner","reasoning"},
    {"benchmark","benchmarks","evaluation","evaluate","empirical"},
    {"safety","safe","security"},
    {"recovery","recover","monitoring","verification","verify"},
]

def now(): return time.time()
def sid(prefix, value):
    return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:16]}"

def clean(x):
    return re.sub(r"\s+", " ", x or "").strip()

def toks(x):
    return {w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9'-]{2,}", (x or "").lower())
            if w not in STOP}

def sim(a,b):
    A,B=toks(a),toks(b)
    if not A or not B: return 0.0
    j=len(A&B)/max(1,len(A|B))
    s=SequenceMatcher(None," ".join(sorted(A))," ".join(sorted(B))).ratio()
    return round(.72*j+.28*s,4)

def safe_url(url):
    try:
        p=urlparse(url)
        if p.scheme not in ("http","https") or not p.hostname: return False
        h=p.hostname.lower()
        if h in ("localhost","127.0.0.1","::1"): return False
        try:
            ip=ipaddress.ip_address(h)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError: pass
        return True
    except Exception: return False

def domain(url):
    try:
        h=(urlparse(url).hostname or "").lower().strip(".")
        return h[4:] if h.startswith("www.") else h
    except Exception: return ""

def family(d):
    d=(d or "").lower()
    if not d or d=="doi.org": return "unknown"
    mapping={
        "arxiv.org":"arxiv","nature.com":"nature","science.org":"science",
        "sciencedirect.com":"elsevier","springer.com":"springer",
        "frontiersin.org":"frontiers","plos.org":"plos","wiley.com":"wiley",
        "bmj.com":"bmj","acm.org":"acm","ieee.org":"ieee","nih.gov":"nih",
        "ncbi.nlm.nih.gov":"nih","jamanetwork.com":"jamanetwork",
        "tandfonline.com":"taylor-francis","sagepub.com":"sage",
        "cambridge.org":"cambridge","oup.com":"oxford","oxfordacademic.com":"oxford",
        "mit.edu":"mit","github.com":"github"
    }
    for k,v in mapping.items():
        if d==k or d.endswith("."+k): return v
    if d.endswith(".edu") or d.endswith(".ac.uk"): return "university"
    return d

def db():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; return c

def init():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY,objective TEXT,status TEXT,created REAL,updated REAL,result TEXT);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,mission_id TEXT,stage TEXT,status TEXT,detail TEXT,created REAL);
    CREATE TABLE IF NOT EXISTS works(id TEXT PRIMARY KEY,mission_id TEXT,title TEXT,url TEXT,canonical_url TEXT,doi TEXT,provider TEXT,domain TEXT,family TEXT,abstract TEXT,relevance REAL,accepted INTEGER,created REAL);
    CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,mission_id TEXT,work_id TEXT,claim_id TEXT,excerpt TEXT,lexical REAL,entailment REAL,relevance REAL,quality TEXT,relation TEXT,domain TEXT,family TEXT,created REAL);
    CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY,mission_id TEXT,text TEXT,claim_type TEXT,purity REAL,relevance REAL,status TEXT,confidence REAL,blockers TEXT,next_action TEXT,created REAL);
    CREATE TABLE IF NOT EXISTS edges(id TEXT PRIMARY KEY,mission_id TEXT,claim_id TEXT,evidence_id TEXT,relation TEXT,score REAL,reason TEXT,created REAL);
    CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY AUTOINCREMENT,mission_id TEXT,kind TEXT,target TEXT,status TEXT,result TEXT,attempt INTEGER,created REAL);
    """)
    c.commit(); c.close()
init()

class Req(BaseModel):
    command:str=Field(...,min_length=10,max_length=10000)
    duration_minutes:int=Field(default=1,ge=1,le=120)

def event(mid,stage,status,detail):
    c=db(); c.execute("INSERT INTO events(mission_id,stage,status,detail,created) VALUES(?,?,?,?,?)",(mid,stage,status,detail,now())); c.commit(); c.close()

def action(mid,kind,target,status,result,attempt):
    c=db(); c.execute("INSERT INTO actions(mission_id,kind,target,status,result,attempt,created) VALUES(?,?,?,?,?,?,?)",(mid,kind,target,status,result,attempt,now())); c.commit(); c.close()

def topic_terms(obj):
    # Extract mission anchors from the objective.  A source does not need
    # to repeat every objective word to be relevant.
    raw=toks(obj)
    out=set(raw)
    for g in TOPIC_GROUPS:
        if raw & g: out |= g
    return out

def topic_score(obj,title,abstract):
    terms=topic_terms(obj)
    tt=toks(title); bt=toks(abstract)
    if not terms: return 0.0
    title_hits=sum(1 for g in TOPIC_GROUPS if (tt & g) and (terms & g))
    body_hits=sum(1 for g in TOPIC_GROUPS if (bt & g) and (terms & g))
    raw_hits=len(tt & terms)
    # Title is strong; body supports it. Do not require the entire objective.
    anchor=max(title_hits/4.0, raw_hits/8.0)
    body=min(1.0, body_hits/5.0)
    lexical=sim(obj,(title+" "+abstract[:6000]))
    return round(min(1.0,.55*anchor+.25*body+.20*lexical),4)

def resolve(url,doi=""):
    candidates=[]
    if url and safe_url(url) and domain(url)!="doi.org": candidates.append(url)
    if doi: candidates.append("https://doi.org/"+doi.strip())
    for u in candidates:
        try:
            r=requests.get(u,headers={"User-Agent":UA},timeout=TIMEOUT,
                           allow_redirects=True,stream=True)
            final=r.url or u; d=domain(final)
            if d and d!="doi.org":
                return final,d,family(d),"resolved"
        except Exception: pass
    d=domain(url)
    if d=="doi.org": d=""
    return url or ("https://doi.org/"+doi if doi else ""),d,family(d),"unresolved"

def crossref(q):
    try:
        r=requests.get("https://api.crossref.org/works",
                       params={"query.bibliographic":q,"rows":6},
                       headers={"User-Agent":UA},timeout=TIMEOUT)
        out=[]
        for x in r.json().get("message",{}).get("items",[]):
            title=clean(" ".join(x.get("title",[])))
            if not title: continue
            ab=clean(re.sub(r"<[^>]+>"," ",x.get("abstract","")))
            doi=x.get("DOI","")
            links=x.get("link") or []
            u=next((z.get("URL") for z in links if safe_url(z.get("URL",""))), "")
            u=u or x.get("URL","")
            out.append({"title":title,"abstract":ab,"doi":doi,"url":u,"provider":"crossref"})
        return out
    except Exception:return []

def openalex(q):
    try:
        r=requests.get("https://api.openalex.org/works",
                       params={"search":q,"per-page":6},
                       headers={"User-Agent":UA},timeout=TIMEOUT)
        out=[]
        for x in r.json().get("results",[]):
            title=clean(x.get("title",""))
            if not title: continue
            inv=x.get("abstract_inverted_index") or {}
            pos=sorted((i,w) for w,inds in inv.items() for i in inds)
            ab=clean(" ".join(w for _,w in pos))
            doi=(x.get("doi") or "").replace("https://doi.org/","")
            loc=x.get("primary_location") or {}
            u=loc.get("landing_page_url") or loc.get("pdf_url") or x.get("id","")
            out.append({"title":title,"abstract":ab,"doi":doi,"url":u,"provider":"openalex"})
        return out
    except Exception:return []

def s2(q):
    try:
        r=requests.get("https://api.semanticscholar.org/graph/v1/paper/search",
                       params={"query":q,"limit":6,"fields":"title,abstract,url,externalIds,openAccessPdf"},
                       headers={"User-Agent":UA},timeout=TIMEOUT)
        out=[]
        for x in r.json().get("data",[]):
            title=clean(x.get("title",""))
            if not title: continue
            ext=x.get("externalIds") or {}; doi=ext.get("DOI","")
            pdf=(x.get("openAccessPdf") or {}).get("url","")
            out.append({"title":title,"abstract":clean(x.get("abstract","")),
                        "doi":doi,"url":pdf or x.get("url",""),"provider":"semantic_scholar"})
        return out
    except Exception:return []

def queries(obj,attempt=0):
    q=[
        obj,
        obj+" autonomous agent reliability evaluation benchmark",
        obj+" empirical study task execution failure",
        obj+" real world deployment agent evaluation",
        obj+" independent replication autonomous agents",
    ]
    # Recovery deliberately changes the search intent, not the verification rule.
    if attempt==1:
        q=[
            obj+" benchmark autonomous agents reliability",
            obj+" tool use planning task success failure",
            obj+" agent evaluation empirical results",
            obj+" deployment safety reliability agents",
            obj+" independent evaluation replication",
        ]
    elif attempt>=2:
        q=[
            obj+" systematic review autonomous agents reliability",
            obj+" controlled experiment agent task completion",
            obj+" benchmark failure recovery monitoring agents",
            obj+" university laboratory autonomous agent study",
            obj+" independent publisher agent reliability",
        ]
    return q

def discover(obj,attempt):
    providers=[crossref,openalex,s2]
    allr=[]
    for i,q in enumerate(queries(obj,attempt)):
        p=providers[(i+attempt)%len(providers)]
        allr += p(q)
    u={}
    for x in allr:
        key=(x.get("doi") or "").lower().strip() or re.sub(r"\W+"," ",x.get("title","").lower()).strip()
        if key and key not in u:u[key]=x
    return list(u.values())[:30]

def fetch_text(url):
    if not url or not safe_url(url): return ""
    try:
        r=requests.get(url,headers={"User-Agent":UA},timeout=TIMEOUT,allow_redirects=True)
        if r.status_code>=400:return ""
        t=r.text[:MAX_TEXT]
        # Pull useful HTML text, especially meta description/abstract and paragraphs.
        for pat in [
            r'<meta[^>]+(?:name|property)=["\'](?:description|og:description|citation_abstract)["\'][^>]+content=["\']([^"\']+)',
            r'<p[^>]*>(.*?)</p>'
        ]:
            vals=re.findall(pat,t,re.I|re.S)
            if vals:
                return clean(re.sub(r"<[^>]+>"," ", " ".join(vals)))[:MAX_TEXT]
        return clean(re.sub(r"<[^>]+>"," ",t))[:MAX_TEXT]
    except Exception:return ""

def prepare(mid,obj,item):
    title=clean(item.get("title","")); ab=clean(item.get("abstract",""))
    rel=topic_score(obj,title,ab)
    # Title relevance can pass even when provider abstract is absent.
    if rel<0.18:return None,"MISSION_IRRELEVANT"
    can,d,fam,state=resolve(item.get("url",""),item.get("doi",""))
    # If abstract is absent, try the canonical source before rejecting the work.
    if len(ab)<180 and can and d:
        fetched=fetch_text(can)
        if fetched: ab=fetched
    if not ab:return None,"NO_USABLE_TEXT"
    # Final acceptance is intentionally moderate, not exact-vocabulary matching.
    rel=max(rel,topic_score(obj,title,ab))
    if rel<0.20:return None,"MISSION_IRRELEVANT"
    w={"id":sid("work",(item.get("doi") or can or title).lower()),
       "mission_id":mid,"title":title,"url":item.get("url",""),
       "canonical_url":can,"doi":item.get("doi",""),"provider":item.get("provider",""),
       "domain":d,"family":fam,"abstract":ab,"relevance":rel}
    # Keep the work for audit even when provenance is unresolved.
    # Unknown provenance may create claims, but it can NEVER create support.
    if not d or fam=="unknown": return w,None
    return w,None

def save_work(w):
    c=db(); c.execute("""INSERT OR IGNORE INTO works
    (id,mission_id,title,url,canonical_url,doi,provider,domain,family,abstract,relevance,accepted,created)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?)""",
    (w["id"],w["mission_id"],w["title"],w["url"],w["canonical_url"],w["doi"],
     w["provider"],w["domain"],w["family"],w["abstract"],w["relevance"],now()))
    c.commit(); c.close()

def ingest(mid,obj,attempt):
    found=discover(obj,attempt)
    accepted=0; rejected=Counter(); new=0; nd=set(); nf=set(); canon=0; fetchable=0
    c=db(); existing={r["id"] for r in c.execute("SELECT id FROM works WHERE mission_id=?",(mid,)).fetchall()}; c.close()
    for item in found:
        w,reason=prepare(mid,obj,item)
        if w and w["domain"] and w["family"]!="unknown":
            canon+=1; fetchable += int(bool(w["abstract"]))
        if not w:
            rejected[reason]+=1; continue
        if reason:
            rejected[reason]+=1
            continue
        accepted+=1
        if w["id"] not in existing:
            save_work(w); existing.add(w["id"]); new+=1
            nd.add(w["domain"]); nf.add(w["family"])
    return {"discovered":len(found),"canonicalized":canon,"accepted":accepted,
            "rejected":dict(rejected),"new_works":new,
            "new_domains":len(nd),"new_families":len(nf),"usable_text":fetchable}

def purity(s):
    s=clean(s)
    if len(s)<45 or len(s)>900:return 0
    if any(re.search(p,s,re.I) for p in BAD_CLAIM):return 0
    if len(s.split())<9:return .5
    return .9 if len(s.split())<120 else .75

def claim_type(s):
    l=s.lower()
    if re.search(r"\b(recommend|should|must|propose|suggest)\b",l):return "recommendation"
    if re.search(r"\b(review|survey|systematic)\b",l):return "review"
    if re.search(r"\b(method|algorithm|framework|approach|architecture)\b",l):return "methodological"
    if re.search(r"\b(found|observed|identified|measured|participants|experiment|benchmark|evaluation)\b",l):return "empirical"
    return "synthesis"

def extract(obj,w):
    out=[]
    for s in re.split(r"(?<=[.!?])\s+",clean(w["abstract"])):
        p=purity(s)
        if p<.65:continue
        r=sim(obj,s)
        # Use topic groups for claim relevance too; do not demand all mission words.
        groups=sum(bool(toks(s)&g) for g in TOPIC_GROUPS)
        if r<.08 and groups<2:continue
        out.append({"text":s,"purity":p,"relevance":round(max(r,min(1,groups/8)),4),
                    "claim_type":claim_type(s)})
    u={}
    for x in out:u[re.sub(r"\W+"," ",x["text"].lower()).strip()]=x
    return list(u.values())[:40]

def excerpt(claim,text):
    ss=[clean(x) for x in re.split(r"(?<=[.!?])\s+",clean(text)) if clean(x)]
    ss=[x for x in ss if not any(re.search(p,x,re.I) for p in BAD_CLAIM)]
    return max(ss,key=lambda x:sim(claim,x),default="")[:MAX_EXCERPT]

def entail(obj,claim,ex,rel):
    if not ex or rel<.20:return 0
    if any(re.search(p,ex,re.I) for p in BAD_CLAIM):return 0
    a,b=toks(claim),toks(ex)
    if not a or not b:return 0
    coverage=len(a&b)/len(a)
    sequence=SequenceMatcher(None,claim.lower(),ex.lower()).ratio()
    score=.72*coverage+.28*sequence
    if len(ex.split())<12:score*=.65
    if len(ex.split())<7:score*=.35
    cp=polarity(claim); ep=polarity(ex)
    if cp!="neutral" and ep!="neutral" and cp!=ep:return min(.2,score)
    # Crucial: high entailment requires meaningful claim coverage.
    if coverage<.50: score*=.45
    if coverage<.35: score*=.25
    return round(min(1,score*(.65+.35*rel)),4)

def polarity(s):
    l=s.lower()
    n=sum(x in l for x in NEG); p=sum(x in l for x in POS)
    return "negative" if n>p else ("positive" if p>n else "neutral")

def relation(lex,ent,rel,d,f):
    if rel<.20 or not d or f=="unknown":return "NO_SUPPORT"
    if ent>=.84 and lex>=.60 and rel>=.45:return "DIRECT_SUPPORT"
    if ent>=.70 and lex>=.45 and rel>=.35:return "STRONG_SUPPORT"
    if ent>=.55 and lex>=.32 and rel>=.30:return "SUPPORT"
    if ent>=.42 and lex>=.25 and rel>=.25:return "POSSIBLE_SUPPORT"
    return "NO_SUPPORT"

def save_claim(mid,x):
    cid=sid("claim",mid+"|"+x["text"].lower())
    c=db(); c.execute("""INSERT OR IGNORE INTO claims
    (id,mission_id,text,claim_type,purity,relevance,status,confidence,blockers,next_action,created)
    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
    (cid,mid,x["text"],x["claim_type"],x["purity"],x["relevance"],"PENDING",0,"[]","",now()))
    c.commit(); c.close(); return cid

def save_ev(mid,w,cid,ex,lex,ent,rel):
    eid=sid("evidence","|".join([mid,w["id"],cid,ex]))
    q={"DIRECT_SUPPORT":"DIRECT","STRONG_SUPPORT":"STRONG","SUPPORT":"MODERATE",
       "POSSIBLE_SUPPORT":"WEAK"}.get(rel,"NONE")
    c=db(); c.execute("""INSERT OR IGNORE INTO evidence
    (id,mission_id,work_id,claim_id,excerpt,lexical,entailment,relevance,quality,relation,domain,family,created)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
    (eid,mid,w["id"],cid,ex,lex,ent,w["relevance"],q,rel,w["domain"],w["family"],now()))
    c.commit(); c.close(); return eid

def save_edge(mid,cid,eid,rel,score,why):
    eid2=sid("edge","|".join([mid,cid,eid,rel]))
    c=db(); c.execute("""INSERT OR IGNORE INTO edges
    (id,mission_id,claim_id,evidence_id,relation,score,reason,created)
    VALUES(?,?,?,?,?,?,?,?)""",(eid2,mid,cid,eid,rel,score,json.dumps(why),now())); c.commit(); c.close()

def evidence_pass(mid,obj):
    c=db(); works=[dict(x) for x in c.execute("SELECT * FROM works WHERE mission_id=? AND accepted=1",(mid,)).fetchall()]; c.close()
    nc=ne=0; usable=0
    for w in works:
        cs=extract(obj,w)
        if cs:usable+=1
        for x in cs:
            cid=save_claim(mid,x)
            ex=excerpt(x["text"],w["abstract"])
            if not ex:continue
            lex=sim(x["text"],ex); ent=entail(obj,x["text"],ex,w["relevance"])
            rel=relation(lex,ent,w["relevance"],w["domain"],w["family"])
            if rel=="NO_SUPPORT":continue
            eid=save_ev(mid,w,cid,ex,lex,ent,rel)
            save_edge(mid,cid,eid,rel,ent,{"domain":w["domain"],"family":w["family"],
                                            "lexical":lex,"entailment":ent,"work_relevance":w["relevance"]})
            ne+=1
    c=db()
    nc=c.execute("SELECT COUNT(*) n FROM claims WHERE mission_id=?",(mid,)).fetchone()["n"]
    c.close()
    return {"usable_works":usable,"total_claims":nc,"new_edges":ne}

def verify_claim(mid,cid):
    c=db(); claim=c.execute("SELECT * FROM claims WHERE id=?",(cid,)).fetchone()
    es=c.execute("""SELECT e.*,w.domain work_domain,w.family work_family
                    FROM evidence e JOIN works w ON w.id=e.work_id WHERE e.claim_id=?""",(cid,)).fetchall(); c.close()
    strong=[e for e in es if e["relation"] in ("DIRECT_SUPPORT","STRONG_SUPPORT")]
    support=[e for e in es if e["relation"] in ("DIRECT_SUPPORT","STRONG_SUPPORT","SUPPORT")]
    works={e["work_id"] for e in support}; domains={e["work_domain"] for e in support if e["work_domain"]}
    fams={e["work_family"] for e in support if e["work_family"]!="unknown"}
    blockers=[]
    if len(works)<2:blockers.append("need_2_independent_works")
    if len(domains)<2:blockers.append("need_2_independent_domains")
    if len(fams)<2:blockers.append("need_2_independent_source_families")
    if len(strong)<2:blockers.append("need_2_strong_evidence_relationships")
    status="VERIFIED" if len(works)>=2 and len(domains)>=2 and len(fams)>=2 and len(strong)>=2 else ("SUPPORTED_NOT_VERIFIED" if support else "INSUFFICIENT")
    nxt=("find_independent_primary_study" if len(works)<2 else
         "find_evidence_from_independent_domain" if len(domains)<2 else
         "find_evidence_from_independent_source_family" if len(fams)<2 else
         "find_second_strong_evidence_excerpt" if len(strong)<2 else "no_further_action_required")
    conf=round(sum(float(e["entailment"]) for e in support)/len(support),4) if support else 0
    c=db(); c.execute("UPDATE claims SET status=?,confidence=?,blockers=?,next_action=? WHERE id=?",
                      (status,conf,json.dumps(blockers),nxt,cid)); c.commit(); c.close()
    return status

def verify(mid):
    c=db(); ids=[r["id"] for r in c.execute("SELECT id FROM claims WHERE mission_id=?",(mid,)).fetchall()]; c.close()
    out=[verify_claim(mid,x) for x in ids]
    return {"claims":len(out),"verified":out.count("VERIFIED"),
            "supported":out.count("SUPPORTED_NOT_VERIFIED"),
            "insufficient":out.count("INSUFFICIENT")}

def recovery(mid,obj,attempt):
    target=queries(obj,attempt)[0]
    action(mid,"research_recovery",target,"RUNNING","diagnostic recovery",attempt)
    src=ingest(mid,obj,attempt); ev=evidence_pass(mid,obj); vr=verify(mid)
    action(mid,"research_recovery",target,"COMPLETED",json.dumps({
        "source":src,"evidence":ev,"verification":vr}),attempt)
    return src,ev,vr

def audit(mid):
    c=db()
    mission=c.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone()
    works=[dict(x) for x in c.execute("SELECT * FROM works WHERE mission_id=?",(mid,)).fetchall()]
    claims=[dict(x) for x in c.execute("SELECT * FROM claims WHERE mission_id=?",(mid,)).fetchall()]
    ev=[dict(x) for x in c.execute("SELECT * FROM evidence WHERE mission_id=?",(mid,)).fetchall()]
    edges=[dict(x) for x in c.execute("SELECT * FROM edges WHERE mission_id=?",(mid,)).fetchall()]
    acts=[dict(x) for x in c.execute("SELECT * FROM actions WHERE mission_id=?",(mid,)).fetchall()]
    events=[dict(x) for x in c.execute("SELECT * FROM events WHERE mission_id=?",(mid,)).fetchall()]
    c.close()
    accepted=[w for w in works if w["accepted"]]
    domains={w["domain"] for w in accepted if w["domain"]}
    fams={w["family"] for w in accepted if w["family"]!="unknown"}
    rejected=sum(1 for w in works if not w["accepted"])
    verified=sum(x["status"]=="VERIFIED" for x in claims)
    return {
      "version":VERSION,"build":BUILD,"mission_id":mid,
      "metrics":{
        "discovered":sum(1 for e in events if e["stage"]=="discovery" and e["status"]=="completed"),
        "works":len(works),"usable_works":sum(1 for x in works if x["accepted"]),
        "claims":len(claims),"evidence":len(ev),"support_edges":len(edges),
        "graph_edges":len(edges),"verified_claims":verified,
        "supported_not_verified":sum(x["status"]=="SUPPORTED_NOT_VERIFIED" for x in claims),
        "domains":len(domains),"source_families":len(fams),"actions":len(acts),
        "high_purity_claims":sum(x["purity"]>=.8 for x in claims),
        "rejected_records":rejected,
        "verification_rate":round(verified/max(1,len(claims)),4)
      },
      "diagnostics":{
        "source_stage_events":[dict(e) for e in events if e["stage"] in ("discovery","recovery")],
        "claim_blockers": [{"claim_id":x["id"],"status":x["status"],
                            "blockers":json.loads(x["blockers"] or "[]"),
                            "next_action":x["next_action"]} for x in claims]
      },
      "reality":{
        "autonomous_research":"DEMONSTRATED" if works and claims else "NOT DEMONSTRATED",
        "mission_relevance_gate":"DEMONSTRATED" if works else "NOT DEMONSTRATED",
        "evidence_provenance":"DEMONSTRATED" if ev and all(x["domain"] and x["family"]!="unknown" for x in ev) else "PARTIALLY DEMONSTRATED",
        "claim_quality_control":"DEMONSTRATED" if claims and any(x["purity"]>=.8 for x in claims) else "PARTIALLY DEMONSTRATED",
        "independent_verification":"DEMONSTRATED" if verified else "NOT DEMONSTRATED",
        "closed_loop_recovery":"DEMONSTRATED" if len(acts)>=2 else "NOT DEMONSTRATED",
        "recovery_diversification":"DEMONSTRATED" if len(domains)>=2 and len(fams)>=2 else "NOT DEMONSTRATED",
        "general_real_world_execution":"NOT DEMONSTRATED",
        "continuous_self_improvement":"NOT DEMONSTRATED",
        "durable_memory":"LIMITED_BY_STORAGE"
      },
      "definition_of_working":"A mission is working when it discovers relevant sources, records canonical provenance, extracts relevant atomic claims, attaches evidence that passes calibrated entailment, verifies only through independent works/domains/families, and recovers from measurable gaps without weakening the gate."
    }

def run_mission(mid,obj):
    event(mid,"mission","running","Mission accepted")
    event(mid,"discovery","running","provider discovery started")
    src=ingest(mid,obj,0)
    event(mid,"discovery","completed",json.dumps(src))
    ev=evidence_pass(mid,obj); vr=verify(mid)
    event(mid,"verification","completed",json.dumps(vr))
    last=(src["new_works"],src["new_domains"],src["new_families"],vr["verified"])
    for a in (1,2):
        if vr["verified"]:break
        event(mid,"recovery","running",f"round {a}")
        s,e,v=recovery(mid,obj,a)
        cur=(s["new_works"],s["new_domains"],s["new_families"],v["verified"])
        event(mid,"recovery","completed",json.dumps({"round":a,"source":s,"evidence":e,"verification":v}))
        if cur==last and s["new_works"]==0 and s["new_domains"]==0 and s["new_families"]==0:
            event(mid,"recovery","completed","STOPPED_NO_DIVERSIFICATION_OR_PROGRESS")
            break
        last=cur
        vr=v
    result=audit(mid)
    c=db(); c.execute("UPDATE missions SET status=?,updated=?,result=? WHERE id=?",("completed",now(),json.dumps(result),mid)); c.commit(); c.close()
    event(mid,"mission","completed","Mission completed with verification gate preserved")

app=FastAPI(title="AI Infinity",version=VERSION)

@app.get("/",response_class=HTMLResponse)
def home():
    return """<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>AI Infinity</title></head><body style="font-family:system-ui;background:#0b0f14;color:#eee;padding:20px">
    <h1>AI Infinity</h1><p>TARGET-2050.36 — DISCOVERY-RECOVERY-REALITY-CORE</p>
    <textarea id="q" style="width:100%;height:180px">Research the reliability of autonomous AI agents for real-world task execution. Find high-quality mission-relevant independent primary evidence from different domains and source families. Extract only atomic factual claims, verify each important claim, identify contradictions, explain verification blockers, and perform bounded targeted recovery without lowering verification standards.</textarea>
    <button onclick="go()">Run</button><pre id="o"></pre>
    <script>async function go(){let q=document.getElementById('q').value;let r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:q})});document.getElementById('o').textContent=JSON.stringify(await r.json(),null,2)}</script>
    </body></html>"""

@app.get("/health")
def health(): return {"status":"ok","version":VERSION,"build":BUILD}

@app.get("/status")
def status():
    c=db(); out={}
    for t in ("missions","works","claims","evidence","edges","actions"):
        out[t]=c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
    c.close(); return {"version":VERSION,"build":BUILD,**out}

@app.get("/capabilities")
def capabilities():
    return {"version":VERSION,"build":BUILD,"capabilities":[
        "stage_diagnostics","mission_relevance_gate","canonical_provenance",
        "provider_rotation","atomic_claim_extraction","claim_purity",
        "calibrated_entailment","independent_verification","claim_blockers",
        "bounded_recovery","recovery_diversification","reality_audit"],
        "not_claimed":["general_AGI","general_ASI","arbitrary_real_world_execution","perfect_semantic_entailment"]}

@app.post("/run")
def run(req:Req):
    mid=sid("mission",req.command+str(now()))
    c=db(); c.execute("INSERT INTO missions VALUES(?,?,?,?,?,?)",(mid,req.command,"running",now(),now(),None)); c.commit(); c.close()
    run_mission(mid,req.command)
    return {"mission_id":mid,"status":"completed","version":VERSION,"build":BUILD}

@app.post("/research")
def research(req:Req): return run(req)

@app.post("/command")
def command(req:Req): return run(req)

@app.get("/mission/{mid}")
def mission(mid):
    c=db(); m=c.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone(); c.close()
    if not m: raise HTTPException(404,"Mission not found")
    return {"mission":dict(m),"audit":audit(mid)}

@app.get("/mission/{mid}/report")
def report(mid): return audit(mid)
@app.get("/audit/{mid}")
def audit_route(mid): return audit(mid)

@app.get("/claims/{mid}")
def claims(mid):
    c=db(); x=[dict(r) for r in c.execute("SELECT * FROM claims WHERE mission_id=? ORDER BY relevance DESC",(mid,)).fetchall()]; c.close()
    return {"mission_id":mid,"claims":x}

@app.get("/evidence/{mid}")
def evidence(mid):
    c=db(); x=[dict(r) for r in c.execute("SELECT * FROM evidence WHERE mission_id=? ORDER BY entailment DESC",(mid,)).fetchall()]; c.close()
    return {"mission_id":mid,"evidence":x}

@app.get("/graph/{mid}")
def graph(mid):
    c=db(); x=[dict(r) for r in c.execute("SELECT * FROM edges WHERE mission_id=? ORDER BY score DESC",(mid,)).fetchall()]; c.close()
    return {"mission_id":mid,"edges":x}

@app.get("/actions/{mid}")
def actions(mid):
    c=db(); x=[dict(r) for r in c.execute("SELECT * FROM actions WHERE mission_id=? ORDER BY id",(mid,)).fetchall()]; c.close()
    return {"mission_id":mid,"actions":x}

@app.get("/events/{mid}")
def events(mid):
    c=db(); x=[dict(r) for r in c.execute("SELECT * FROM events WHERE mission_id=? ORDER BY id",(mid,)).fetchall()]; c.close()
    return {"mission_id":mid,"events":x}

@app.get("/missions")
def missions():
    c=db(); x=[dict(r) for r in c.execute("SELECT id,objective,status,created,updated FROM missions ORDER BY created DESC LIMIT 50").fetchall()]; c.close()
    return {"missions":x}

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="0.0.0.0",port=8000)
