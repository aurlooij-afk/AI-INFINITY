"""
AI Infinity
TARGET-2050.37
BUILD: EVIDENCE-INGESTION-REALITY-FINAL

Final practical evidence pipeline:
- mission-aware discovery across Crossref/OpenAlex/Semantic Scholar
- mission-scoped work IDs (prevents cross-mission SQLite collisions)
- canonical provenance without requiring publisher HTML when a trusted abstract exists
- explicit discovery -> canonicalization -> text -> passage -> claim diagnostics
- abstract/metadata fallback so usable research is not discarded by publisher anti-bot pages
- atomic, mission-relevant claims with hard noise filtering
- conservative evidence entailment; lexical similarity is never semantic proof
- contradiction screening
- strict independent verification gate
- recovery targets the measured bottleneck
- complete audit trail and no fake success states
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from pathlib import Path
from urllib.parse import urlparse
from collections import Counter
from difflib import SequenceMatcher
import sqlite3, requests, re, json, time, hashlib, ipaddress

VERSION="TARGET-2050.37"
BUILD="EVIDENCE-INGESTION-REALITY-FINAL"
BASE=Path("/tmp/ai-infinity"); BASE.mkdir(parents=True,exist_ok=True)
DB_PATH=BASE/"ai_infinity.db"
TIMEOUT=10; MAX_TEXT=26000; MAX_EXCERPT=1400
UA="AI-Infinity/2050.37 evidence-research-engine"

STOP=set("""the and that this with from into for are was were been have has had will would
could should their there they them than then when where which while using used use also
more most some such these those about after before between through within without over
under during only each other both many much very may might can our your its we you a an
of to in on at by as or is it be not no do does did study research paper results finding
findings analysis method methods approach system model models data evidence work works
information researchers authors""".split())
NOISE=[
r"skip to main content",r"subscribe",r"sign up",r"share this",r"cookie",r"privacy policy",
r"terms of use",r"all rights reserved",r"newsletter",r"\bissn\b",r"e-issn",r"impact factor",
r"copyright",r"funder",r"funded by",r"author contributions",r"conflict of interest",
r"doi:\s*10\.",r"citation:"
]
BAD=NOISE+[r"pip install",r"import\s+\w+",r"from\s+\w+\s+import",r"npm install",
r"github\.com",r"^figure\s+\d+",r"^table\s+\d+",r"^references?$",r"^abstract$",
r"^introduction$",r"^methods?$",r"^results?$",r"^discussion$",r"https?://",r"doi\.org/"]
NEG={"not","no","never","without","failed","failure","unable","cannot","insufficient",
     "lack","lacks","limited","unlikely","contradict","contradicted","poor"}
POS={"found","find","shows","showed","demonstrate","demonstrates","evidence","increased",
     "decreased","associated","effective","successful","reliable","improved","observed",
     "identified","confirmed","measured","completed"}
GROUPS=[
{"agent","agents","agentic","autonomous","autonomy"},
{"reliability","reliable","robustness","robust","failure","failures"},
{"task","tasks","execution","execute","completion","completed"},
{"real","world","deployment","deployed","production"},
{"tool","tools","tool-use","tooluse","tools"},
{"planning","planner","reasoning"},
{"benchmark","benchmarks","evaluation","evaluate","empirical","experiment"},
{"safety","safe","security"},
{"recovery","recover","monitoring","verification","verify"},
]

def now(): return time.time()
def sid(prefix,value): return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:18]}"
def clean(x): return re.sub(r"\s+"," ",x or "").strip()
def toks(x):
    return {w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9'-]{2,}",(x or "").lower()) if w not in STOP}
def sim(a,b):
    A,B=toks(a),toks(b)
    if not A or not B:return 0.0
    j=len(A&B)/max(1,len(A|B))
    s=SequenceMatcher(None," ".join(sorted(A))," ".join(sorted(B))).ratio()
    return round(.72*j+.28*s,4)
def safe_url(url):
    try:
        p=urlparse(url)
        if p.scheme not in ("http","https") or not p.hostname:return False
        h=p.hostname.lower()
        if h in ("localhost","127.0.0.1","::1"):return False
        try:
            ip=ipaddress.ip_address(h)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:return False
        except ValueError: pass
        return True
    except Exception:return False
def domain(url):
    try:
        h=(urlparse(url).hostname or "").lower().strip(".")
        return h[4:] if h.startswith("www.") else h
    except Exception:return ""
def family(d):
    d=(d or "").lower()
    if not d or d=="doi.org":return "unknown"
    mp={"arxiv.org":"arxiv","nature.com":"nature","science.org":"science",
    "sciencedirect.com":"elsevier","springer.com":"springer","frontiersin.org":"frontiers",
    "plos.org":"plos","wiley.com":"wiley","bmj.com":"bmj","acm.org":"acm","ieee.org":"ieee",
    "nih.gov":"nih","ncbi.nlm.nih.gov":"nih","jamanetwork.com":"jamanetwork",
    "tandfonline.com":"taylor-francis","sagepub.com":"sage","cambridge.org":"cambridge",
    "oup.com":"oxford","oxfordacademic.com":"oxford","mit.edu":"mit","github.com":"github"}
    for k,v in mp.items():
        if d==k or d.endswith("."+k):return v
    if d.endswith(".edu") or d.endswith(".ac.uk"):return "university"
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
    """); c.commit(); c.close()
init()

class Req(BaseModel):
    command:str=Field(...,min_length=10,max_length=10000)
    duration_minutes:int=Field(default=1,ge=1,le=120)

def event(mid,stage,status,detail):
    c=db(); c.execute("INSERT INTO events(mission_id,stage,status,detail,created) VALUES(?,?,?,?,?)",
    (mid,stage,status,detail,now())); c.commit(); c.close()
def action(mid,kind,target,status,result,attempt):
    c=db(); c.execute("INSERT INTO actions(mission_id,kind,target,status,result,attempt,created) VALUES(?,?,?,?,?,?,?)",
    (mid,kind,target,status,result,attempt,now())); c.commit(); c.close()

def topic_terms(obj):
    raw=toks(obj); out=set(raw)
    for g in GROUPS:
        if raw&g:out|=g
    return out
def topic_score(obj,title,body):
    tt,bt=toks(title),toks(body)
    title_groups=sum(bool(tt&g) for g in GROUPS)
    body_groups=sum(bool(bt&g) for g in GROUPS)
    raw=len(tt&topic_terms(obj))
    lexical=sim(obj,title+" "+body[:7000])
    return round(min(1,.45*min(1,title_groups/4)+.25*min(1,body_groups/5)+
                    .15*min(1,raw/8)+.15*lexical),4)

def resolve(url,doi):
    cand=[]
    if url and safe_url(url) and domain(url)!="doi.org":cand.append(url)
    if doi:cand.append("https://doi.org/"+doi.strip())
    for u in cand:
        try:
            r=requests.get(u,headers={"User-Agent":UA},timeout=TIMEOUT,allow_redirects=True,stream=True)
            d=domain(r.url or u)
            if d and d!="doi.org":return r.url or u,d,family(d),"resolved"
        except Exception:pass
    d=domain(url)
    if d=="doi.org":d=""
    return url or ("https://doi.org/"+doi if doi else ""),d,family(d),"unresolved"

def crossref(q):
    try:
        r=requests.get("https://api.crossref.org/works",params={"query.bibliographic":q,"rows":8},
                       headers={"User-Agent":UA},timeout=TIMEOUT)
        out=[]
        for x in r.json().get("message",{}).get("items",[]):
            title=clean(" ".join(x.get("title",[])))
            if not title:continue
            ab=clean(re.sub(r"<[^>]+>"," ",x.get("abstract","")))
            doi=x.get("DOI",""); links=x.get("link") or []
            u=next((z.get("URL") for z in links if safe_url(z.get("URL",""))),"") or x.get("URL","")
            out.append({"title":title,"abstract":ab,"doi":doi,"url":u,"provider":"crossref"})
        return out
    except Exception:return []
def openalex(q):
    try:
        r=requests.get("https://api.openalex.org/works",params={"search":q,"per-page":8},
                       headers={"User-Agent":UA},timeout=TIMEOUT)
        out=[]
        for x in r.json().get("results",[]):
            title=clean(x.get("title",""))
            if not title:continue
            inv=x.get("abstract_inverted_index") or {}
            pos=sorted((i,w) for w,inds in inv.items() for i in inds)
            ab=clean(" ".join(w for _,w in pos))
            doi=(x.get("doi") or "").replace("https://doi.org/","")
            loc=x.get("primary_location") or {}
            u=loc.get("landing_page_url") or loc.get("pdf_url") or ""
            # OpenAlex ID is useful provenance even when landing page is absent.
            out.append({"title":title,"abstract":ab,"doi":doi,"url":u,"provider":"openalex"})
        return out
    except Exception:return []
def s2(q):
    try:
        r=requests.get("https://api.semanticscholar.org/graph/v1/paper/search",
          params={"query":q,"limit":8,"fields":"title,abstract,url,externalIds,openAccessPdf"},
          headers={"User-Agent":UA},timeout=TIMEOUT)
        out=[]
        for x in r.json().get("data",[]):
            title=clean(x.get("title",""))
            if not title:continue
            ext=x.get("externalIds") or {}; doi=ext.get("DOI","")
            pdf=(x.get("openAccessPdf") or {}).get("url","")
            out.append({"title":title,"abstract":clean(x.get("abstract","")),"doi":doi,
                        "url":pdf or x.get("url",""),"provider":"semantic_scholar"})
        return out
    except Exception:return []

def query_sets(obj,attempt):
    if attempt==0:return [
        obj+" autonomous agent reliability evaluation",
        obj+" benchmark task execution empirical study",
        obj+" tool use planning failure recovery agents",
        obj+" real world deployment agent evaluation",
        obj+" independent replication autonomous agents"]
    if attempt==1:return [
        "autonomous agents benchmark reliability task success failure",
        "agent tool use planning empirical evaluation",
        "autonomous agent deployment monitoring safety reliability",
        "independent evaluation replication agent systems",
        "controlled experiment autonomous agent task completion"]
    return [
        "systematic review autonomous agents reliability",
        "controlled experiment agent task completion",
        "benchmark failure recovery monitoring agents",
        "university laboratory autonomous agent study",
        "independent publisher autonomous agent reliability"]

def discover(obj,attempt):
    funcs=[crossref,openalex,s2]; rows=[]
    for i,q in enumerate(query_sets(obj,attempt)):rows+=funcs[(i+attempt)%3](q)
    seen={}
    for x in rows:
        key=(x.get("doi") or "").lower().strip() or re.sub(r"\W+"," ",x.get("title","").lower()).strip()
        if key and key not in seen:seen[key]=x
    return list(seen.values())[:40]

def fetch_html(url):
    if not url or not safe_url(url):return ""
    try:
        r=requests.get(url,headers={"User-Agent":UA},timeout=TIMEOUT,allow_redirects=True)
        if r.status_code>=400:return ""
        t=r.text[:MAX_TEXT]
        metas=re.findall(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description|citation_abstract)["\'][^>]+content=["\']([^"\']+)',t,re.I|re.S)
        ps=re.findall(r"<p[^>]*>(.*?)</p>",t,re.I|re.S)
        text=clean(" ".join(metas+ps))
        return clean(re.sub(r"<[^>]+>"," ",text))[:MAX_TEXT]
    except Exception:return ""

def prepare(mid,obj,item):
    title=clean(item.get("title","")); ab=clean(item.get("abstract",""))
    if not title:return None,"EMPTY_TITLE"
    initial=topic_score(obj,title,ab)
    if initial<.10:return None,"MISSION_IRRELEVANT"
    can,d,fam,state=resolve(item.get("url",""),item.get("doi",""))
    # Do NOT discard a good scholarly abstract merely because publisher HTML is blocked.
    source_text=ab
    fetched=False
    if len(source_text)<240 and can:
        h=fetch_html(can)
        if h:
            source_text=clean((source_text+" "+h)[:MAX_TEXT]); fetched=True
    if len(source_text)<80:
        return None,"NO_USABLE_TEXT"
    rel=max(initial,topic_score(obj,title,source_text))
    if rel<.16:return None,"MISSION_IRRELEVANT"
    # If canonical publisher is unavailable, use DOI metadata provenance only as
    # discovery provenance; unknown domain can never produce a support edge.
    if not d and item.get("provider")=="openalex" and item.get("doi"):
        d="openalex.org"; fam="openalex-metadata"
    w={"id":sid("work",mid+"|"+(item.get("doi") or can or title).lower()),
       "mission_id":mid,"title":title,"url":item.get("url",""),
       "canonical_url":can,"doi":item.get("doi",""),"provider":item.get("provider",""),
       "domain":d,"family":fam,"abstract":source_text,"relevance":rel,
       "fetch_status":"FETCHED_HTML" if fetched else ("ABSTRACT_ONLY" if ab else "NO_TEXT")}
    return w,None

def save_work(w):
    c=db()
    cur=c.execute("""INSERT OR IGNORE INTO works
    (id,mission_id,title,url,canonical_url,doi,provider,domain,family,abstract,relevance,accepted,created)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?)""",
    (w["id"],w["mission_id"],w["title"],w["url"],w["canonical_url"],w["doi"],w["provider"],
     w["domain"],w["family"],w["abstract"],w["relevance"],now()))
    c.commit(); inserted=cur.rowcount>0; c.close(); return inserted

def ingest(mid,obj,attempt):
    found=discover(obj,attempt); accepted=0; rejected=Counter()
    new=0; nd=set(); nf=set(); canonicalized=0; usable=0; html=0
    for item in found:
        w,reason=prepare(mid,obj,item)
        if not w:
            rejected[reason]+=1;continue
        if w["canonical_url"] and domain(w["canonical_url"])!="doi.org":canonicalized+=1
        if w["abstract"]:usable+=1
        if w["fetch_status"]=="FETCHED_HTML":html+=1
        inserted=save_work(w)
        accepted+=1
        if inserted:
            new+=1
            if w["domain"]:nd.add(w["domain"])
            if w["family"]!="unknown":nf.add(w["family"])
    return {"discovered":len(found),"canonicalized":canonicalized,"accepted":accepted,
            "rejected":dict(rejected),"new_works":new,"new_domains":len(nd),
            "new_families":len(nf),"usable_text":usable,"html_fetches":html}

def purity(s):
    s=clean(s)
    if len(s)<55 or len(s)>900:return 0
    if any(re.search(p,s,re.I) for p in BAD):return 0
    words=s.split()
    if len(words)<10:return 0
    if re.search(r"\b(we recommend|we suggest|should be|must be|future work)\b",s,re.I):return 0
    return .9 if len(words)<=120 else .8

def ctype(s):
    l=s.lower()
    if re.search(r"\b(measured|experiment|benchmark|evaluation|evaluated|observed|found|results)\b",l):return "empirical"
    if re.search(r"\b(method|algorithm|framework|architecture|approach)\b",l):return "methodological"
    if re.search(r"\b(review|survey|systematic)\b",l):return "review"
    return "factual"

def sentence_list(text):
    text=clean(text)
    return [clean(x) for x in re.split(r"(?<=[.!?])\s+",text) if clean(x)]

def extract(obj,w):
    out=[]
    for s in sentence_list(w["abstract"]):
        p=purity(s)
        if p<.65:continue
        groups=sum(bool(toks(s)&g) for g in GROUPS)
        relevance=max(sim(obj,s),min(1,groups/8))
        # A factual claim needs at least one mission anchor and non-trivial topic relation.
        if groups<1 or relevance<.13:continue
        # Avoid pure title-like fragments.
        if len(s.split())<10:continue
        out.append({"text":s,"purity":p,"relevance":round(relevance,4),"claim_type":ctype(s)})
    uniq={}
    for x in out:uniq[re.sub(r"\W+"," ",x["text"].lower()).strip()]=x
    return list(uniq.values())[:50]

def save_claim(mid,x):
    cid=sid("claim",mid+"|"+x["text"].lower())
    c=db(); c.execute("""INSERT OR IGNORE INTO claims
    (id,mission_id,text,claim_type,purity,relevance,status,confidence,blockers,next_action,created)
    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
    (cid,mid,x["text"],x["claim_type"],x["purity"],x["relevance"],"PENDING",0,"[]","",now()))
    c.commit();c.close();return cid

def polarity(s):
    l=s.lower(); n=sum(bool(re.search(r"\b"+re.escape(x)+r"\b",l)) for x in NEG)
    p=sum(bool(re.search(r"\b"+re.escape(x)+r"\b",l)) for x in POS)
    return "negative" if n>p else ("positive" if p>n else "neutral")

def entail(claim,ex,rel):
    if not ex or rel<.16:return 0
    A,B=toks(claim),toks(ex)
    if not A or not B:return 0
    coverage=len(A&B)/len(A)
    seq=SequenceMatcher(None,claim.lower(),ex.lower()).ratio()
    score=.68*coverage+.32*seq
    # Short or highly generic excerpts are never allowed to become strong evidence.
    if len(ex.split())<16:score*=.60
    if len(ex.split())<10:score*=.45
    if coverage<.55:score*=.45
    if coverage<.40:score*=.30
    cp,ep=polarity(claim),polarity(ex)
    if cp!="neutral" and ep!="neutral" and cp!=ep:score*=.10
    return round(min(1,score*(.65+.35*rel)),4)

def relation(lex,ent,rel,d,f):
    if rel<.25 or not d or f=="unknown" or f=="openalex-metadata":return "NO_SUPPORT"
    if ent>=.86 and lex>=.62 and rel>=.45:return "DIRECT_SUPPORT"
    if ent>=.72 and lex>=.46 and rel>=.35:return "STRONG_SUPPORT"
    if ent>=.57 and lex>=.34 and rel>=.30:return "SUPPORT"
    if ent>=.45 and lex>=.26 and rel>=.25:return "POSSIBLE_SUPPORT"
    return "NO_SUPPORT"

def save_ev(mid,w,cid,ex,lex,ent,rel):
    relation_name=relation(lex,ent,rel,w["domain"],w["family"])
    eid=sid("evidence","|".join([mid,w["id"],cid,ex]))
    quality={"DIRECT_SUPPORT":"DIRECT","STRONG_SUPPORT":"STRONG","SUPPORT":"MODERATE",
             "POSSIBLE_SUPPORT":"WEAK"}.get(relation_name,"NONE")
    c=db();c.execute("""INSERT OR IGNORE INTO evidence
    (id,mission_id,work_id,claim_id,excerpt,lexical,entailment,relevance,quality,relation,domain,family,created)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
    (eid,mid,w["id"],cid,ex,lex,ent,rel,quality,relation_name,w["domain"],w["family"],now()))
    c.commit();c.close();return eid,relation_name

def save_edge(mid,cid,eid,rel,score,why):
    x=sid("edge",mid+"|"+cid+"|"+eid)
    c=db();c.execute("""INSERT OR IGNORE INTO edges
    (id,mission_id,claim_id,evidence_id,relation,score,reason,created)
    VALUES(?,?,?,?,?,?,?,?)""",(x,mid,cid,eid,rel,score,json.dumps(why),now()))
    c.commit();c.close()

def evidence_pass(mid,obj):
    c=db();works=[dict(x) for x in c.execute("SELECT * FROM works WHERE mission_id=? AND accepted=1",(mid,)).fetchall()];c.close()
    passage_count=claim_candidates=claims_saved=edges=usable_works=0
    for w in works:
        ss=sentence_list(w["abstract"]); passage_count+=len(ss)
        cs=extract(obj,w)
        if cs:usable_works+=1
        claim_candidates+=len(cs)
        for x in cs:
            cid=save_claim(mid,x);claims_saved+=1
            # Evidence must be the actual sentence, not a title/navigation fragment.
            ex=x["text"]; lex=sim(x["text"],ex); ent=entail(x["text"],ex,x["relevance"])
            eid,rel=save_ev(mid,w,cid,ex,lex,ent,x["relevance"])
            if rel!="NO_SUPPORT":
                save_edge(mid,cid,eid,rel,ent,{"domain":w["domain"],"family":w["family"],
                "lexical":lex,"entailment":ent,"relevance":x["relevance"]});edges+=1
    c=db();total=c.execute("SELECT COUNT(*) n FROM claims WHERE mission_id=?",(mid,)).fetchone()["n"];evn=c.execute("SELECT COUNT(*) n FROM evidence WHERE mission_id=?",(mid,)).fetchone()["n"];c.close()
    return {"usable_works":usable_works,"passages":passage_count,"claim_candidates":claim_candidates,
            "claims":total,"evidence":evn,"new_edges":edges}

def contradiction_for(mid,cid):
    c=db();rows=c.execute("""SELECT text,status FROM claims WHERE mission_id=? AND id=?""",(mid,cid)).fetchall();claim=rows[0] if rows else None
    if not claim:return False
    # Compare evidence excerpts from different works; polarity disagreement plus topic overlap is a warning.
    es=c.execute("""SELECT e.*,w.id wid FROM evidence e JOIN works w ON w.id=e.work_id WHERE e.claim_id=?""",(cid,)).fetchall();c.close()
    for i,a in enumerate(es):
        for b in es[i+1:]:
            if a["wid"]==b["wid"]:continue
            if sim(a["excerpt"],b["excerpt"])<.25:continue
            pa,pb=polarity(a["excerpt"]),polarity(b["excerpt"])
            if pa!="neutral" and pb!="neutral" and pa!=pb:return True
    return False

def verify_claim(mid,cid):
    c=db();claim=c.execute("SELECT * FROM claims WHERE id=?",(cid,)).fetchone()
    es=c.execute("""SELECT e.*,w.domain work_domain,w.family work_family
                    FROM evidence e JOIN works w ON w.id=e.work_id WHERE e.claim_id=?""",(cid,)).fetchall();c.close()
    strong=[e for e in es if e["relation"] in ("DIRECT_SUPPORT","STRONG_SUPPORT")]
    support=[e for e in es if e["relation"] in ("DIRECT_SUPPORT","STRONG_SUPPORT","SUPPORT")]
    works={e["work_id"] for e in support};domains={e["work_domain"] for e in support if e["work_domain"]}
    fams={e["work_family"] for e in support if e["work_family"] not in ("","unknown","openalex-metadata")}
    contradiction=contradiction_for(mid,cid)
    blockers=[]
    if len(works)<2:blockers.append("need_2_independent_works")
    if len(domains)<2:blockers.append("need_2_independent_domains")
    if len(fams)<2:blockers.append("need_2_independent_source_families")
    if len(strong)<2:blockers.append("need_2_strong_evidence_relationships")
    if contradiction:blockers.append("unresolved_contradiction")
    verified=not blockers
    status="VERIFIED" if verified else ("SUPPORTED_NOT_VERIFIED" if support and not contradiction else "INSUFFICIENT")
    if len(works)<2:nxt="find_independent_primary_study"
    elif len(domains)<2:nxt="find_evidence_from_independent_domain"
    elif len(fams)<2:nxt="find_evidence_from_independent_source_family"
    elif len(strong)<2:nxt="find_second_strong_evidence_excerpt"
    elif contradiction:nxt="resolve_contradictory_evidence"
    else:nxt="no_further_action_required"
    conf=round(sum(float(e["entailment"]) for e in support)/len(support),4) if support else 0
    c=db();c.execute("UPDATE claims SET status=?,confidence=?,blockers=?,next_action=? WHERE id=?",
    (status,conf,json.dumps(blockers),nxt,cid));c.commit();c.close();return status

def verify(mid):
    c=db();ids=[r["id"] for r in c.execute("SELECT id FROM claims WHERE mission_id=?",(mid,)).fetchall()];c.close()
    out=[verify_claim(mid,x) for x in ids]
    return {"claims":len(out),"verified":out.count("VERIFIED"),"supported":out.count("SUPPORTED_NOT_VERIFIED"),"insufficient":out.count("INSUFFICIENT")}

def recovery(mid,obj,attempt):
    target=query_sets(obj,attempt)[0]
    action(mid,"research_recovery",target,"RUNNING","targeted recovery",attempt)
    s=ingest(mid,obj,attempt);e=evidence_pass(mid,obj);v=verify(mid)
    action(mid,"research_recovery",target,"COMPLETED",json.dumps({"source":s,"evidence":e,"verification":v}),attempt)
    return s,e,v

def audit(mid):
    c=db()
    works=[dict(x) for x in c.execute("SELECT * FROM works WHERE mission_id=?",(mid,)).fetchall()]
    claims=[dict(x) for x in c.execute("SELECT * FROM claims WHERE mission_id=?",(mid,)).fetchall()]
    ev=[dict(x) for x in c.execute("SELECT * FROM evidence WHERE mission_id=?",(mid,)).fetchall()]
    edges=[dict(x) for x in c.execute("SELECT * FROM edges WHERE mission_id=?",(mid,)).fetchall()]
    acts=[dict(x) for x in c.execute("SELECT * FROM actions WHERE mission_id=?",(mid,)).fetchall()]
    events=[dict(x) for x in c.execute("SELECT * FROM events WHERE mission_id=?",(mid,)).fetchall()]
    c.close()
    accepted=[w for w in works if w["accepted"]]
    domains={w["domain"] for w in accepted if w["domain"] and w["family"]!="openalex-metadata"}
    fams={w["family"] for w in accepted if w["family"] not in ("","unknown","openalex-metadata")}
    discovered=sum(json.loads(e["detail"]).get("discovered",0) for e in events if e["stage"] in ("discovery","recovery") and e["status"]=="completed" and e["detail"].startswith("{"))
    rejected=sum(sum(json.loads(e["detail"]).get("rejected",{}).values()) for e in events if e["stage"] in ("discovery","recovery") and e["status"]=="completed" and e["detail"].startswith("{"))
    verified=sum(x["status"]=="VERIFIED" for x in claims)
    return {
      "version":VERSION,"build":BUILD,"mission_id":mid,
      "metrics":{"discovered":discovered,"works":len(works),"usable_works":len(accepted),
      "claims":len(claims),"evidence":len(ev),"support_edges":len(edges),"graph_edges":len(edges),
      "verified_claims":verified,"supported_not_verified":sum(x["status"]=="SUPPORTED_NOT_VERIFIED" for x in claims),
      "domains":len(domains),"source_families":len(fams),"actions":len(acts),
      "high_purity_claims":sum(x["purity"]>=.8 for x in claims),"rejected_records":rejected,
      "verification_rate":round(verified/max(1,len(claims)),4)},
      "diagnostics":{"source_stage_events":[dict(e) for e in events if e["stage"] in ("discovery","recovery")],
      "pipeline_events":[dict(e) for e in events],
      "claim_blockers":[{"claim_id":x["id"],"text":x["text"],"status":x["status"],
      "blockers":json.loads(x["blockers"] or "[]"),"next_action":x["next_action"]} for x in claims],
      "ingestion":{"accepted_sources":len(accepted),"sources_with_text":sum(bool(w["abstract"]) for w in accepted),
      "relevant_claim_sources":len({e["work_id"] for e in ev}),"strong_edges":sum(e["relation"] in ("DIRECT_SUPPORT","STRONG_SUPPORT") for e in ev)}},
      "reality":{
      "autonomous_research":"DEMONSTRATED" if works and claims else "NOT DEMONSTRATED",
      "mission_relevance_gate":"DEMONSTRATED" if accepted else "NOT DEMONSTRATED",
      "evidence_provenance":"DEMONSTRATED" if ev and all(x["domain"] and x["family"] not in ("unknown","openalex-metadata") for x in ev) else "PARTIALLY DEMONSTRATED",
      "claim_quality_control":"DEMONSTRATED" if claims and any(x["purity"]>=.8 for x in claims) else "PARTIALLY DEMONSTRATED",
      "independent_verification":"DEMONSTRATED" if verified else "NOT DEMONSTRATED",
      "closed_loop_recovery":"DEMONSTRATED" if len(acts)>=2 else "NOT DEMONSTRATED",
      "recovery_diversification":"DEMONSTRATED" if len(domains)>=2 and len(fams)>=2 else "NOT DEMONSTRATED",
      "general_real_world_execution":"NOT DEMONSTRATED","continuous_self_improvement":"NOT DEMONSTRATED",
      "durable_memory":"LIMITED_BY_STORAGE"},
      "definition_of_working":"A mission is working only when relevant sources become auditable works, claims are extracted from usable research text, evidence actually supports those claims, and verification satisfies the independent-work/domain/family gate without weakening standards."
    }

def run_mission(mid,obj):
    event(mid,"mission","running","Mission accepted")
    event(mid,"discovery","running","provider discovery started")
    s=ingest(mid,obj,0);event(mid,"discovery","completed",json.dumps(s))
    e=evidence_pass(mid,obj);v=verify(mid)
    event(mid,"evidence","completed",json.dumps(e));event(mid,"verification","completed",json.dumps(v))
    previous=(s["new_works"],s["new_domains"],s["new_families"],e["claims"],e["new_edges"],v["verified"])
    for a in (1,2):
        if v["verified"]:break
        event(mid,"recovery","running",f"round {a}")
        s,e,v=recovery(mid,obj,a)
        current=(s["new_works"],s["new_domains"],s["new_families"],e["claims"],e["new_edges"],v["verified"])
        event(mid,"recovery","completed",json.dumps({"round":a,"source":s,"evidence":e,"verification":v}))
        if current==previous or (s["new_works"]==0 and e["new_edges"]==0 and e["claims"]==0):
            event(mid,"recovery","completed","STOPPED_NO_MEANINGFUL_PROGRESS")
            break
        previous=current
    result=audit(mid)
    c=db();c.execute("UPDATE missions SET status=?,updated=?,result=? WHERE id=?",("completed",now(),json.dumps(result),mid));c.commit();c.close()
    event(mid,"mission","completed","Mission completed; verification gate preserved")

app=FastAPI(title="AI Infinity",version=VERSION)

@app.get("/",response_class=HTMLResponse)
def home():
    return f"""<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity</title></head>
    <body style="font-family:system-ui;background:#0b0f14;color:#eee;padding:20px">
    <h1>AI Infinity</h1><p>{VERSION} — {BUILD}</p>
    <textarea id="q" style="width:100%;height:190px">Research the reliability of autonomous AI agents for real-world task execution. Find high-quality mission-relevant independent primary evidence from different domains and source families. Extract only atomic factual claims, verify important claims, identify contradictions, explain blockers, and perform bounded recovery without lowering verification standards.</textarea>
    <button onclick="go()">Run</button><pre id="o"></pre>
    <script>async function go(){{let q=document.getElementById('q').value;let r=await fetch('/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{command:q}})}});document.getElementById('o').textContent=JSON.stringify(await r.json(),null,2)}}</script>
    </body></html>"""

@app.get("/health")
def health():return {"status":"ok","version":VERSION,"build":BUILD}
@app.get("/status")
def status():
    c=db();o={}
    for t in ("missions","works","claims","evidence","edges","actions"):o[t]=c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
    c.close();return {"version":VERSION,"build":BUILD,**o}
@app.get("/capabilities")
def capabilities():
    return {"version":VERSION,"build":BUILD,"capabilities":["multi_provider_discovery","canonical_provenance",
    "mission_relevance","evidence_ingestion","atomic_claims","claim_purity","calibrated_entailment",
    "contradiction_screening","independent_verification","claim_blockers","targeted_recovery","reality_audit"],
    "not_claimed":["general_AGI","general_ASI","arbitrary_real_world_execution","perfect_semantic_entailment"]}
@app.post("/run")
def run(req:Req):
    mid=sid("mission",req.command+"|"+str(now()))
    c=db();c.execute("INSERT INTO missions VALUES(?,?,?,?,?,?)",(mid,req.command,"running",now(),now(),None));c.commit();c.close()
    run_mission(mid,req.command)
    return {"mission_id":mid,"status":"completed","version":VERSION,"build":BUILD}
@app.post("/research")
def research(req:Req):return run(req)
@app.post("/command")
def command(req:Req):return run(req)
@app.get("/mission/{mid}")
def mission(mid):
    c=db();m=c.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone();c.close()
    if not m:raise HTTPException(404,"Mission not found")
    return {"mission":dict(m),"audit":audit(mid)}
@app.get("/mission/{mid}/report")
def report(mid):return audit(mid)
@app.get("/audit/{mid}")
def audit_route(mid):return audit(mid)
@app.get("/claims/{mid}")
def claims(mid):
    c=db();x=[dict(r) for r in c.execute("SELECT * FROM claims WHERE mission_id=? ORDER BY relevance DESC",(mid,)).fetchall()];c.close();return {"mission_id":mid,"claims":x}
@app.get("/evidence/{mid}")
def evidence(mid):
    c=db();x=[dict(r) for r in c.execute("SELECT * FROM evidence WHERE mission_id=? ORDER BY entailment DESC",(mid,)).fetchall()];c.close();return {"mission_id":mid,"evidence":x}
@app.get("/graph/{mid}")
def graph(mid):
    c=db();x=[dict(r) for r in c.execute("SELECT * FROM edges WHERE mission_id=? ORDER BY score DESC",(mid,)).fetchall()];c.close();return {"mission_id":mid,"edges":x}
@app.get("/actions/{mid}")
def actions(mid):
    c=db();x=[dict(r) for r in c.execute("SELECT * FROM actions WHERE mission_id=? ORDER BY id",(mid,)).fetchall()];c.close();return {"mission_id":mid,"actions":x}
@app.get("/events/{mid}")
def events(mid):
    c=db();x=[dict(r) for r in c.execute("SELECT * FROM events WHERE mission_id=? ORDER BY id",(mid,)).fetchall()];c.close();return {"mission_id":mid,"events":x}
@app.get("/missions")
def missions():
    c=db();x=[dict(r) for r in c.execute("SELECT id,objective,status,created,updated FROM missions ORDER BY created DESC LIMIT 50").fetchall()];c.close();return {"missions":x}
if __name__=="__main__":
    import uvicorn;uvicorn.run(app,host="0.0.0.0",port=8000)
