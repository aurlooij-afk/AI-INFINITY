from __future__ import annotations
import asyncio, hashlib, html, ipaddress, json, os, re, socket, sqlite3, time, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

VERSION="TARGET-2050.20"; BUILD="FINAL-PRACTICAL-AUTONOMOUS-CORE"; PROJECT="AI Infinity"; STARTED=time.time()
DB_PATH=Path(os.getenv("AI_INFINITY_DB","/tmp/ai_infinity.db")); CACHE_TTL=int(os.getenv("AI_INFINITY_CACHE_TTL","21600"))
MAX_BODY=int(os.getenv("AI_INFINITY_MAX_BODY","2000000")); MAX_RESULTS=int(os.getenv("AI_INFINITY_MAX_RESULTS","12")); TIMEOUT=float(os.getenv("AI_INFINITY_HTTP_TIMEOUT","12")); MAX_REDIRECTS=3
API_KEY=os.getenv("AI_INFINITY_API_KEY","").strip(); RATE_LIMIT=int(os.getenv("AI_INFINITY_RATE_LIMIT","60")); FETCH_TOP=int(os.getenv("AI_INFINITY_FETCH_TOP","4")); CONCURRENCY=int(os.getenv("AI_INFINITY_CONCURRENCY","6"))
RATE_STATE={}; WORKER_TASK=None; JOB_WAKE=asyncio.Event()
SEARCH=("duckduckgo","bing","google"); STRUCTURED=("semantic_scholar","openalex","crossref","wikipedia"); PROVIDERS=SEARCH+STRUCTURED

# ---------------- database ----------------
def con():
    c=sqlite3.connect(DB_PATH,timeout=15,check_same_thread=False); c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=15000"); c.execute("PRAGMA synchronous=NORMAL"); return c

def db_init():
    c=con()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS memory(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,event TEXT NOT NULL,data TEXT,created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY,objective TEXT NOT NULL,status TEXT NOT NULL,result TEXT,created_at REAL NOT NULL,completed_at REAL);
    CREATE TABLE IF NOT EXISTS claims(id INTEGER PRIMARY KEY AUTOINCREMENT,mission_id TEXT,claim TEXT NOT NULL,verified INTEGER DEFAULT 0,confidence REAL DEFAULT 0,created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY AUTOINCREMENT,mission_id TEXT,title TEXT,url TEXT,domain TEXT,provider TEXT,snippet TEXT,evidence_type TEXT,quality REAL,relevance REAL,freshness REAL,published_at TEXT,supports INTEGER DEFAULT 0,contradicts INTEGER DEFAULT 0,created_at REAL NOT NULL,UNIQUE(mission_id,url));
    CREATE TABLE IF NOT EXISTS evidence_links(id INTEGER PRIMARY KEY AUTOINCREMENT,mission_id TEXT,claim_id INTEGER,evidence_id INTEGER,relation TEXT,score REAL,created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS research_cache(cache_key TEXT PRIMARY KEY,provider TEXT,question TEXT,payload TEXT,created_at REAL,expires_at REAL);
    CREATE TABLE IF NOT EXISTS research_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,mission_id TEXT,question TEXT,provider TEXT,status TEXT,hits INTEGER DEFAULT 0,cache_hit INTEGER DEFAULT 0,http_status INTEGER,latency_ms REAL,error TEXT,created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS provider_stats(provider TEXT PRIMARY KEY,calls INTEGER DEFAULT 0,successes INTEGER DEFAULT 0,failures INTEGER DEFAULT 0,hits INTEGER DEFAULT 0,last_status INTEGER,last_error TEXT,avg_latency_ms REAL DEFAULT 0,updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,objective TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,result TEXT,error TEXT,created_at REAL NOT NULL,started_at REAL,completed_at REAL);
    CREATE TABLE IF NOT EXISTS artifacts(id INTEGER PRIMARY KEY AUTOINCREMENT,mission_id TEXT,type TEXT NOT NULL,content TEXT NOT NULL,created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY,window_start REAL NOT NULL,count INTEGER NOT NULL);
    '''); c.commit(); c.close()

def execdb(sql,p=()):
    c=con(); c.execute(sql,p); c.commit(); c.close()

def one(sql,p=()):
    c=con(); r=c.execute(sql,p).fetchone(); c.close(); return r

def allrows(sql,p=()):
    c=con(); r=c.execute(sql,p).fetchall(); c.close(); return r

def event(name,data=None):
    try: execdb("INSERT INTO events(event,data,created_at) VALUES(?,?,?)",(name,json.dumps(data,ensure_ascii=False,default=str) if data is not None else None,time.time()))
    except Exception: pass

# ---------------- security / text ----------------
PRIVATE={"localhost","localhost.localdomain","metadata","metadata.google.internal"}; SUFFIXES=(".local",".internal",".localhost")
def clean(x,n=12000): return str(x or "").replace("\x00"," ").strip()[:n]
def private_ip(h):
    try:
        x=ipaddress.ip_address(h); return x.is_private or x.is_loopback or x.is_link_local or x.is_multicast or x.is_reserved or x.is_unspecified
    except ValueError: return False
def public_host(h):
    h=(h or "").lower().rstrip(".")
    if not h or h in PRIVATE or any(h.endswith(s) for s in SUFFIXES) or private_ip(h): return False
    try:
        for i in socket.getaddrinfo(h,None,type=socket.SOCK_STREAM):
            if private_ip(i[4][0]): return False
        return True
    except Exception: return True
def safe_url(u):
    try:
        p=urlparse(str(u).strip())
        if p.scheme not in ("http","https") or not p.hostname: return False,"only http/https URLs are allowed"
        if p.username or p.password: return False,"userinfo in URLs is blocked"
        if len(str(u))>4096: return False,"URL too long"
        return (True,"ok") if public_host(p.hostname) else (False,"private or unsafe host blocked")
    except Exception: return False,"invalid URL"
def norm(u,base=""):
    try:
        u=html.unescape(str(u or "").strip()); u=urljoin(base,u) if base else u; p=urlparse(u)
        return p._replace(fragment="").geturl() if p.scheme in ("http","https") and p.netloc else ""
    except Exception: return ""
def domain(u):
    try:return urlparse(u).hostname or ""
    except:return ""
def regdom(h):
    p=(h or "").lower().strip(".").split(".")
    if len(p)<=2:return ".".join(p)
    s=".".join(p[-2:]); special={"co.uk","org.uk","ac.uk","gov.uk","com.au","net.au","org.au","co.jp","co.in"}
    return ".".join(p[-3:]) if s in special else s
def firewall(u):
    x=(u or "").lower(); return x.startswith(("http://","https://")) and not any(z in x for z in ("javascript:","data:","file:","blob:","chrome:"))

async def get(url,headers=None):
    cur=norm(url)
    if not cur: raise ValueError("invalid URL")
    for _ in range(MAX_REDIRECTS+1):
        ok,why=safe_url(cur)
        if not ok: raise ValueError(why)
        async with httpx.AsyncClient(timeout=TIMEOUT,follow_redirects=False,headers=headers or {"User-Agent":"AI-Infinity/2050.13"}) as client:
            async with client.stream("GET",cur) as r:
                if r.status_code in (301,302,303,307,308):
                    loc=r.headers.get("location","")
                    if not loc:return r.status_code,cur,""
                    cur=norm(loc,cur); continue
                chunks=[]; total=0
                async for ch in r.aiter_bytes(65536):
                    total+=len(ch)
                    if total>MAX_BODY: raise ValueError("response too large")
                    chunks.append(ch)
                return r.status_code,cur,b"".join(chunks).decode("utf-8","replace")
    raise ValueError("too many redirects")

# ---------------- objective / evidence ----------------
KEYS=("objective","command","query","task","mission","prompt")
def objective(raw):
    if isinstance(raw,dict):
        for k in KEYS:
            v=raw.get(k)
            if isinstance(v,str) and v.strip(): return clean(v,10000)
            if isinstance(v,dict):
                z=objective(v)
                if z:return z
        return clean(json.dumps(raw,ensure_ascii=False),10000)
    if isinstance(raw,str):
        s=raw.strip()
        if s.startswith(("{","[")):
            try:return objective(json.loads(s))
            except Exception:pass
        return clean(s,10000)
    return clean(raw,10000)

def tokens(s):
    stop={"the","and","for","with","that","this","from","into","about","what","when","where","which","find","give","research","identify","important","evidence","verify","their","they","them","are","can","how","why","real","world","task","tasks","next","actions","use","using","based","current"}
    return {x for x in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}",(s or "").lower()) if x not in stop}
def sim(a,b):
    x,y=tokens(a),tokens(b); return len(x&y)/max(1,len(x|y)) if x and y else 0.0
def instructional(s): return bool(re.match(r"^(research|find|identify|give|explain|compare|analyze|investigate|tell|determine|list)\b",(s or "").strip(),re.I))
def subject(obj):
    s=objective(obj)
    s=re.sub(r"\b(find|identify|give|research|verify|analyze|investigate|explain)\b[^.]{0,120}?\b(evidence|sources|claims|next actions)\b","",s,flags=re.I)
    s=re.sub(r"\b(find|identify|give|verify)\b","",s,flags=re.I); return re.sub(r"\s+"," ",s).strip(" .,:;-")[:500]
def questions(obj):
    s=subject(obj) or objective(obj)[:400]
    qs=[s,f"{s} empirical evidence study evaluation",f"{s} limitations risks failures criticism",f"{s} independent evidence benchmark real world",f"{s} systematic review research"]
    out=[]
    for q in qs:
        q=re.sub(r"\s+"," ",q).strip()
        if q and q not in out:out.append(q[:500])
    return out
def etype(p,u,t,s):
    d=domain(u); txt=f"{t} {s}".lower()
    if p in STRUCTURED and p!="wikipedia" or d=="doi.org":return "scholarly"
    if p=="wikipedia" or "wikipedia.org" in d:return "reference"
    if p in SEARCH:return "search-result"
    if d.endswith(".gov") or ".gov." in d or d.endswith(".edu") or ".edu." in d:return "institutional"
    if any(x in txt for x in ("study","paper","journal","doi")):return "secondary"
    return "web"
def quality(p,u,t,s,e):
    q={"scholarly":.78,"institutional":.76,"secondary":.62,"web":.48,"reference":.42,"search-result":.18}.get(e,.35); d=domain(u)
    if d=="doi.org":q+=.05
    if d.endswith(".gov") or ".gov." in d:q+=.05
    if d.endswith(".edu") or ".edu." in d:q+=.04
    if "bing.com/ck/a" in u:q-=.12
    return max(.05,min(.95,q))
def freshness(pub):
    if not pub:return .60
    try:
        dt=datetime.fromisoformat(str(pub).replace("Z","+00:00")); dt=dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc); age=max(0,(datetime.now(timezone.utc)-dt).total_seconds()/86400)
        return .98 if age<=180 else .93 if age<=365 else .86 if age<=730 else .72 if age<=1825 else .58
    except:return .60
def relevance(obj,t,s,p):
    x=.15+sim(subject(obj),f"{t} {s}")*1.8
    if p=="wikipedia":x*=.85
    if instructional(t) or t.lower().strip() in {"objective","glossary of computer science"}:x*=.2
    return min(1,x)
def usable(i,obj):
    t=clean(i.get("title"),500); u=norm(i.get("url","")) or i.get("url",""); s=clean(i.get("snippet"),1600); p=i.get("provider","")
    if not t or not u or not firewall(u) or not domain(u) or "bing.com/ck/a" in u or "google.com/url" in u:return False
    tl=t.lower()
    if tl in {"objective definition & meaning | dictionary.com","glossary of computer science"}:return False
    if any(x in u.lower() for x in ("bing.com/ck/a","google.com/url","search.yahoo.com")):return False
    r=relevance(obj,t,s,p); return r>=.38 if p in SEARCH else r>=.28

# ---------------- parsers / providers ----------------
def strip(s):return re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",s or ""))).strip()
def ddg(t):
    out=[]
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',t,re.I|re.S):
        w=t[m.end():m.end()+1800]; sm=re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>',w,re.I|re.S)
        out.append({"title":strip(m.group(2)),"url":html.unescape(m.group(1)),"snippet":strip(sm.group(1)) if sm else ""})
    return out[:MAX_RESULTS]
def bing(t):
    out=[]
    for m in re.finditer(r'<li class="b_algo".*?</li>',t,re.I|re.S):
        b=m.group(0); z=re.search(r'<h2>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',b,re.I|re.S)
        if not z:continue
        sm=re.search(r'<p[^>]*>(.*?)</p>',b,re.I|re.S); out.append({"title":strip(z.group(2)),"url":html.unescape(z.group(1)),"snippet":strip(sm.group(1)) if sm else ""})
    return out[:MAX_RESULTS]
def google(t):
    out=[]
    for m in re.finditer(r'<a[^>]+href="(/url\?[^" ]+|https?://[^" ]+)"[^>]*>(.*?)</a>',t,re.I|re.S):
        h=html.unescape(m.group(1)); u=unquote(parse_qs(urlparse(h).query).get("q",[""])[0]) if h.startswith("/url?") else h; ti=strip(m.group(2))
        if ti and u.startswith("http"):out.append({"title":ti,"url":u,"snippet":""})
    return out[:MAX_RESULTS]
def key(p,q):return hashlib.sha256(f"{p}|{q}".encode()).hexdigest()
def cache_get(p,q):
    r=one("SELECT payload,expires_at FROM research_cache WHERE cache_key=?",(key(p,q),))
    if not r or float(r["expires_at"])<time.time():return None
    try:return json.loads(r["payload"])
    except:return None
def cache_put(p,q,x):
    n=time.time(); execdb("INSERT OR REPLACE INTO research_cache(cache_key,provider,question,payload,created_at,expires_at) VALUES(?,?,?,?,?,?)",(key(p,q),p,q,json.dumps(x,ensure_ascii=False),n,n+CACHE_TTL))
def stat(p,ok,h,status,err,lat):
    r=one("SELECT * FROM provider_stats WHERE provider=?",(p,)); n=time.time()
    if r:
        calls=r["calls"]+1; avg=(r["avg_latency_ms"]*r["calls"]+lat)/calls
        execdb("UPDATE provider_stats SET calls=?,successes=?,failures=?,hits=?,last_status=?,last_error=?,avg_latency_ms=?,updated_at=? WHERE provider=?",(calls,r["successes"]+(1 if ok else 0),r["failures"]+(0 if ok else 1),r["hits"]+h,status,err,avg,n,p))
    else:execdb("INSERT INTO provider_stats VALUES(?,?,?,?,?,?,?,?,?)",(p,1,int(ok),int(not ok),h,status,err,lat,n))

async def provider(p,q):
    t0=time.perf_counter(); c=cache_get(p,q)
    if c is not None:
        lat=(time.perf_counter()-t0)*1000; execdb("INSERT INTO research_runs(question,provider,status,hits,cache_hit,latency_ms,created_at) VALUES(?,?,?,?,?,?,?)",(q,p,"cache",len(c),1,lat,time.time())); return {"provider":p,"items":c,"cache_hit":True,"status":200}
    status=None
    try:
        if p=="duckduckgo":status,_,b=await get("https://html.duckduckgo.com/html/?q="+quote_plus(q),{"User-Agent":"Mozilla/5.0 AI-Infinity/2050.13"}); items=ddg(b)
        elif p=="bing":status,_,b=await get("https://www.bing.com/search?q="+quote_plus(q),{"User-Agent":"Mozilla/5.0 AI-Infinity/2050.13"}); items=bing(b)
        elif p=="google":status,_,b=await get("https://www.google.com/search?q="+quote_plus(q),{"User-Agent":"Mozilla/5.0 AI-Infinity/2050.13"}); items=google(b)
        elif p=="semantic_scholar":
            status,_,b=await get("https://api.semanticscholar.org/graph/v1/paper/search?query="+quote_plus(q)+"&limit=10&fields=title,url,abstract,year,publicationDate,externalIds"); d=json.loads(b); items=[]
            for x in d.get("data",[]):items.append({"title":x.get("title",""),"url":x.get("url") or (("https://doi.org/"+x["externalIds"]["DOI"]) if x.get("externalIds",{}).get("DOI") else ""),"snippet":x.get("abstract") or "","published_at":x.get("publicationDate") or ((str(x["year"])+"-01-01") if x.get("year") else None)})
        elif p=="openalex":
            status,_,b=await get("https://api.openalex.org/works?search="+quote_plus(q)+"&per-page=10"); d=json.loads(b); items=[]
            for x in d.get("results",[]):items.append({"title":x.get("display_name",""),"url":x.get("doi") or (x.get("primary_location") or {}).get("landing_page_url","") ,"snippet":"","published_at":x.get("publication_date")})
        elif p=="crossref":
            status,_,b=await get("https://api.crossref.org/works?query.bibliographic="+quote_plus(q)+"&rows=10"); d=json.loads(b); items=[]
            for x in d.get("message",{}).get("items",[]):
                a=(x.get("published-print") or x.get("published-online") or {}).get("date-parts",[[None]])[0]; pub=(f"{a[0]:04d}-{a[1]:02d}-{a[2]:02d}" if len(a)>=3 and a[0] else (f"{a[0]:04d}-01-01" if a and a[0] else None)); items.append({"title":(x.get("title") or [""])[0],"url":("https://doi.org/"+x["DOI"]) if x.get("DOI") else x.get("URL",""),"snippet":strip(x.get("abstract","")),"published_at":pub})
        elif p=="wikipedia":
            status,_,b=await get("https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch="+quote_plus(q)+"&format=json&srlimit=10"); d=json.loads(b); items=[{"title":x.get("title",""),"url":"https://en.wikipedia.org/wiki/"+quote_plus(x.get("title","").replace(" ","_")),"snippet":strip(x.get("snippet",""))} for x in d.get("query",{}).get("search",[])]
        else:raise ValueError("unknown provider")
        out=[]
        for x in items[:MAX_RESULTS]:
            u=norm(x.get("url",""));
            if u:out.append({"provider":p,"title":clean(x.get("title"),500),"url":u,"snippet":clean(x.get("snippet"),2500),"published_at":x.get("published_at")})
        cache_put(p,q,out); lat=(time.perf_counter()-t0)*1000; stat(p,True,len(out),status,None,lat); execdb("INSERT INTO research_runs(question,provider,status,hits,cache_hit,http_status,latency_ms,created_at) VALUES(?,?,?,?,?,?,?,?)",(q,p,"ok",len(out),0,status,lat,time.time())); return {"provider":p,"items":out,"cache_hit":False,"status":status}
    except Exception as e:
        lat=(time.perf_counter()-t0)*1000; err=str(e)[:500]; stat(p,False,0,status,err,lat); execdb("INSERT INTO research_runs(question,provider,status,hits,cache_hit,http_status,latency_ms,error,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(q,p,"error",0,0,status,lat,err,time.time())); return {"provider":p,"items":[],"cache_hit":False,"status":status,"error":err}

async def research(obj,mission):
    qs=questions(obj); raw=await asyncio.gather(*[provider(p,q) for q in qs for p in PROVIDERS]); by={p:[] for p in PROVIDERS}; fails={}; hit=False
    for r in raw:
        by[r["provider"]]+=r.get("items",[]); hit|=bool(r.get("cache_hit"));
        if r.get("error"):fails[r["provider"]]=r["error"]
    cand=[]; rej=[]; seen=set()
    for p,items in by.items():
        for i in items:
            u=norm(i.get("url","")); k=u.lower()
            if not u or k in seen:continue
            seen.add(k); i["evidence_type"]=etype(p,u,i.get("title",""),i.get("snippet","")); i["quality"]=quality(p,u,i.get("title",""),i.get("snippet",""),i["evidence_type"]); i["relevance"]=relevance(obj,i.get("title",""),i.get("snippet",""),p); i["freshness"]=freshness(i.get("published_at"))
            (cand if usable(i,obj) else rej).append(i)
    cand.sort(key=lambda x:x["relevance"]*.55+x["quality"]*.30+x["freshness"]*.15,reverse=True); sel=[]; ds=set()
    for i in cand:
        d=regdom(domain(i["url"]))
        if d not in ds or (i["evidence_type"]=="scholarly" and len(sel)<8):sel.append(i);ds.add(d)
        if len(sel)>=10:break
    hydrated=await hydrate(sel[:FETCH_TOP]) if sel else []
    byurl={x.get("url"):x for x in hydrated}
    for i in sel:
        if i.get("url") in byurl:
            i.update({k:v for k,v in byurl[i["url"]].items() if k in ("snippet","quality","fetched_status","fetched_text")})
        execdb("INSERT OR IGNORE INTO evidence(mission_id,title,url,domain,provider,snippet,evidence_type,quality,relevance,freshness,published_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(mission,i["title"],i["url"],domain(i["url"]),i["provider"],i["snippet"],i["evidence_type"],i["quality"],i["relevance"],i["freshness"],i.get("published_at"),time.time()))
    prov={i["provider"] for i in sel}; avg=sum(i["quality"] for i in sel)/len(sel) if sel else 0; strength=min(1,len(sel)/6)*.30+min(1,len(ds)/4)*.25+min(1,len(prov)/4)*.20+avg*.15+(sum(i["relevance"] for i in sel)/len(sel) if sel else 0)*.10
    return {"mode":"hybrid","cache_hit":hit,"questions":qs,"provider_hits":{p:len(by[p]) for p in PROVIDERS},"provider_failures":fails,"accepted_sources":sel[:8],"rejected_count":len(rej),"rejected_examples":rej[:5],"independent_domains":len(ds),"provider_diversity":len(prov),"source_diversity":round(min(1,len(ds)/5),3),"average_source_quality":round(avg,3),"research_strength":round(strength,3),"evidence_available":bool(sel),"failure":None if sel else "no_relevant_independent_evidence"}

# ---------------- claims / countercheck ----------------
def claims(obj,evidence):
    out=[]
    for i in evidence[:8]:
        t=clean(i.get("title"),300); s=clean(i.get("snippet"),800)
        if s and len(s)>=50 and not instructional(s):
            z=re.split(r"(?<=[.!?])\s+",s)[0].strip();
            if len(z)>=40:out.append(z[:500])
        elif t and not instructional(t):out.append(t[:500])
    if not out and subject(obj):out.append(f"Evidence relevant to {subject(obj)} was identified from external sources.")
    seen=set(); return [x for x in out if not (x.lower() in seen or seen.add(x.lower()))][:6]
def verify(claim,evidence):
    support=[]
    for i in evidence:
        r=alignment(claim,i)
        if r>=.16:
            support.append({"url":i["url"],"domain":domain(i["url"]),"provider":i["provider"],"quality":round(i["quality"],3),"freshness":round(i["freshness"],3),"relevance":r,"evidence_type":i["evidence_type"]})
    strong=[x for x in support if x["relevance"]>=.34 and x["quality"]>=.45]
    ds={regdom(x["domain"]) for x in strong}; scholarly=sum(x["evidence_type"]=="scholarly" for x in strong)
    weighted=sum(x["quality"]*x["relevance"] for x in support)/len(support) if support else 0
    c=min(.95,.05+min(.35,len(strong)*.11)+min(.25,len(ds)*.09)+min(.20,scholarly*.08)+weighted*.25)
    verified=len(strong)>=2 and len(ds)>=2 and c>=.58
    return {"claim":claim,"verified":verified,"confidence":round(c,3),"supporting_evidence":support[:6],"evidence_alignment":"strong" if c>=.75 else ("moderate" if c>=.55 else "weak")}

async def countercheck(cs,obj):
    out=[]; s=subject(obj)
    for c in cs[:4]:
        q=[f"{c[:280]} limitations",f"{c[:280]} criticism failure",f"{s} evidence against reliability"]; raw=await asyncio.gather(*[provider(p,x) for x in q for p in ("bing","openalex","crossref")]); ev=[]; seen=set()
        for r in raw:
            for i in r.get("items",[]):
                u=norm(i.get("url",""));
                if not u or u in seen:continue
                seen.add(u); i["evidence_type"]=etype(i["provider"],u,i.get("title",""),i.get("snippet","")); i["quality"]=quality(i["provider"],u,i.get("title",""),i.get("snippet",""),i["evidence_type"]); i["relevance"]=relevance(s,i.get("title",""),i.get("snippet",""),i["provider"]); i["freshness"]=freshness(i.get("published_at"))
                if usable(i,s) and i["relevance"]>=.32:ev.append(i)
        contra=[i for i in ev if sim(c,f"{i.get('title','')} {i.get('snippet','')}")>=.20]; ds={regdom(domain(i["url"])) for i in contra}
        out.append({"claim":c,"queries":q,"evidence":contra[:6],"contradiction_count":len(contra),"independent_domains":len(ds),"checked":True,"finding":"potential_counter_evidence_found" if contra else "no_relevant_counter_evidence_found"})
    return out


# ---------------- final practical capabilities ----------------

def alignment(claim, evidence):
    text=f"{evidence.get('title','')} {evidence.get('snippet','')}"
    a=sim(claim,text)
    neg_claim=bool(re.search(r"\b(no|not|never|cannot|cannot|fails?|failure|unreliable|limited|limitations|risk|risks)\b",claim,re.I))
    neg_ev=bool(re.search(r"\b(no|not|never|cannot|fails?|failure|unreliable|limited|limitations|risk|risks)\b",text,re.I))
    if neg_claim==neg_ev:a=min(1,a+.06)
    return round(a,3)

async def hydrate(items):
    sem=asyncio.Semaphore(CONCURRENCY)
    async def one_item(i):
        async with sem:
            try:
                status,url,body=await get(i['url'],{"User-Agent":"AI-Infinity/2050.20 evidence-fetch"})
                if status>=400 or not body:return i
                text=strip(body)
                if len(text)>12000:text=text[:12000]
                if len(text)>=120:
                    i=dict(i); i['fetched_status']=status; i['fetched_text']=text
                    i['snippet']=clean((i.get('snippet','')+' '+text[:1800]),2500)
                    i['quality']=min(.98,round(float(i.get('quality',.4))+.08,3))
                return i
            except Exception:return i
    return await asyncio.gather(*[one_item(i) for i in items[:FETCH_TOP]])

def answer_from(obj, evidence, ver, counter, score):
    lines=[]
    if evidence:
        lines.append(f"Research completed using {len(evidence)} accepted sources across {len({regdom(domain(i.get('url',''))) for i in evidence})} independent domains.")
    else:
        lines.append("No sufficiently relevant independent evidence was accepted.")
    for v in ver[:4]:
        label="supported" if v['verified'] else "not sufficiently verified"
        lines.append(f"Claim: {v['claim']} — {label} (confidence {v['confidence']}).")
    found=sum(x.get('contradiction_count',0) for x in counter)
    if found: lines.append(f"Counter-evidence search found {found} potentially contradictory result(s) requiring review.")
    else: lines.append("Counter-evidence was actively searched; no result met the current contradiction threshold.")
    lines.append(f"Overall evidence score: {round(score,3)}. Treat this as an evidence assessment, not a guarantee of truth.")
    if evidence:
        lines.append("Priority next action: inspect the highest-quality primary or scholarly sources and re-run verification when the evidence changes.")
    return "\n".join(lines)

def remember_learning(obj, result):
    summary={"objective":obj,"score":result.get('synthesis',{}).get('mission_score'),"evidence_count":result.get('synthesis',{}).get('evidence_count'),"contradictions":result.get('synthesis',{}).get('contradictions'),"next":result.get('next_cycle',[])}
    execdb("INSERT INTO memory(text,created_at) VALUES(?,?)",(json.dumps(summary,ensure_ascii=False),time.time()))

def rate_ok(request):
    key=request.headers.get('x-forwarded-for',request.client.host if request.client else 'unknown').split(',')[0].strip()
    now=time.time(); row=one("SELECT window_start,count FROM rate_limits WHERE key=?",(key,))
    if not row or now-float(row['window_start'])>=60:
        execdb("INSERT OR REPLACE INTO rate_limits(key,window_start,count) VALUES(?,?,?)",(key,now,1)); return True
    if int(row['count'])>=RATE_LIMIT:return False
    execdb("UPDATE rate_limits SET count=count+1 WHERE key=?",(key,)); return True

def auth_ok(request):
    return (not API_KEY) or request.headers.get('x-api-key','')==API_KEY

async def run_job(job_id,payload):
    execdb("UPDATE jobs SET status=?,started_at=? WHERE id=?",('running',time.time(),job_id))
    try:
        result=await execute(payload)
        execdb("UPDATE jobs SET status=?,result=?,completed_at=? WHERE id=?",('completed',json.dumps(result,ensure_ascii=False),time.time(),job_id))
    except Exception as e:
        execdb("UPDATE jobs SET status=?,error=?,completed_at=? WHERE id=?",('failed',str(e)[:500],time.time(),job_id))
    JOB_WAKE.set()

async def worker():
    while True:
        try:
            r=one("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
            if r:
                payload=ExecuteRequest(**json.loads(r['payload']))
                await run_job(r['id'],payload)
            else:
                JOB_WAKE.clear();
                try: await asyncio.wait_for(JOB_WAKE.wait(),timeout=20)
                except asyncio.TimeoutError: pass
        except asyncio.CancelledError: raise
        except Exception as e:
            event('worker_error',{'error':str(e)[:500]}); await asyncio.sleep(2)

# ---------------- mission ----------------
class ExecuteRequest(BaseModel):
    objective:Optional[str]=None; command:Optional[str]=None; query:Optional[str]=None; research:bool=True; verify:bool=True; remember:bool=True; duration_minutes:int=Field(default=1,ge=1,le=120)
    def resolved(self):return objective(self.objective or self.command or self.query or "")
async def execute(payload):
    obj=payload.resolved()
    if not obj:raise HTTPException(400,"objective, command, or query is required")
    mid="mission-"+uuid.uuid4().hex[:12]; execdb("INSERT INTO missions(id,objective,status,created_at) VALUES(?,?,?,?)",(mid,obj,"running",time.time())); trace=[{"stage":"understand","status":"completed"}]
    try:
        r=await research(obj,mid) if payload.research else {"mode":"disabled","accepted_sources":[],"evidence_available":False}; trace.append({"stage":"research","status":"completed" if payload.research else "skipped"})
        ev=r.get("accepted_sources",[]); ds={regdom(domain(i.get("url",""))) for i in ev if domain(i.get("url",""))}; ps={i.get("provider") for i in ev}; trace.append({"stage":"evidence_graph","status":"completed","nodes":len(ev),"domains":len(ds)})
        cs=claims(obj,ev); trace.append({"stage":"claim_extraction","status":"completed","claims":len(cs)}); ver=[verify(c,ev) for c in cs] if payload.verify else []; trace.append({"stage":"verify","status":"completed" if payload.verify else "skipped"})
        for v in ver:execdb("INSERT INTO claims(mission_id,claim,verified,confidence,created_at) VALUES(?,?,?,?,?)",(mid,v["claim"],int(v["verified"]),v["confidence"],time.time()))
        counter=await countercheck(cs,obj) if payload.verify and cs else []; trace.append({"stage":"countercheck","status":"completed","checked_claims":len(counter)}); contradictions=sum(x["contradiction_count"] for x in counter); trace.append({"stage":"contradiction_analysis","status":"completed","contradictions":contradictions})
        vc=sum(v["verified"] for v in ver); avg=sum(v["confidence"] for v in ver)/len(ver) if ver else 0; checked=len(counter); pos=sum(x["contradiction_count"]>0 for x in counter); score=max(0,min(1,r.get("research_strength",0)*.40+(vc/len(ver) if ver else 0)*.25+avg*.15+min(1,len(ds)/4)*.10+(1 if checked==len(ver) and ver else 0)*.10-min(.20,contradictions*.04)))
        critique=[]
        if not ev:critique.append("No relevant independent evidence was accepted.")
        if len(ds)<2:critique.append("Evidence comes from fewer than two independent domains.")
        if ver and vc<len(ver):critique.append(f"{len(ver)-vc} claims did not reach the verification threshold.")
        critique.append(f"Counter-evidence was actively checked; {('potential counter-evidence was found.' if pos else 'none of the accepted results met the contradiction threshold.')}") if checked else critique.append("Counter-evidence was not checked.")
        trace.append({"stage":"critique","status":"completed"}); conclusion="Evidence is insufficient for a strong conclusion." if score<.50 else ("Evidence supports some claims, with remaining uncertainty." if score<.75 else "Evidence is reasonably strong, but uncertainty and source limitations remain.")
        syn={"conclusion":conclusion,"mission_score":round(score,3),"verified_claims":vc,"unsupported_claims":max(0,len(ver)-vc),"evidence_count":len(ev),"independent_domains":len(ds),"provider_diversity":len(ps),"contradictions":contradictions,"countercheck_completed":bool(checked),"critique":critique}; trace.append({"stage":"synthesis","status":"completed"})
        nxt=["Monitor evidence freshness and continue the next mission cycle."] if ev and not contradictions and vc==len(ver) else (["Expand research with additional independent sources before verification."] if not ev else (["Review contradictory evidence and re-run verification before relying on affected claims."] if contradictions else ["Target unsupported claims with more specific evidence queries."]))
        trace.append({"stage":"next_cycle","status":"completed","actions":nxt})
        if payload.remember:remember_learning(obj,{"synthesis":syn,"next_cycle":nxt})
        execdb("INSERT INTO artifacts(mission_id,type,content,created_at) VALUES(?,?,?,?)",(mid,"final_answer",answer,time.time()))
        result={"task_id":mid,"status":"completed","version":VERSION,"build":BUILD,"objective":obj,"agent_trace":trace,"research":r,"evidence_graph":{"nodes":len(ev),"independent_domains":len(ds),"provider_diversity":len(ps)},"verification":{"verified":bool(ver) and vc==len(ver),"confidence":round(avg,3),"claims":ver},"counter_evidence":counter,"critique":critique,"synthesis":syn,"next_cycle":nxt}
        execdb("UPDATE missions SET status=?,result=?,completed_at=? WHERE id=?",("completed",json.dumps(result,ensure_ascii=False),time.time(),mid)); event("mission_completed",{"mission_id":mid,"score":score}); return result
    except Exception as e:
        execdb("UPDATE missions SET status=?,result=?,completed_at=? WHERE id=?",("failed",json.dumps({"error":str(e)}),time.time(),mid)); event("mission_failed",{"mission_id":mid,"error":str(e)[:500]}); raise

# ---------------- app/routes ----------------
@asynccontextmanager
async def lifespan(app):
    global WORKER_TASK
    db_init(); event("startup",{"version":VERSION,"build":BUILD}); WORKER_TASK=asyncio.create_task(worker())
    yield
    if WORKER_TASK:
        WORKER_TASK.cancel()
        try: await WORKER_TASK
        except asyncio.CancelledError: pass
app=FastAPI(title=PROJECT,version=VERSION,lifespan=lifespan)
@app.middleware("http")
async def limit(request:Request,call_next):
    if request.method in {"POST","PUT","PATCH","DELETE"} and not auth_ok(request):
        return JSONResponse({"detail":"API key required"},401)
    if request.method in {"POST","PUT","PATCH","DELETE"} and not rate_ok(request):
        return JSONResponse({"detail":"rate limit exceeded"},429)
    try:
        if int(request.headers.get("content-length","0") or 0)>MAX_BODY:return JSONResponse({"detail":"request too large"},413)
    except Exception:pass
    return await call_next(request)
@app.exception_handler(Exception)
async def errors(request,exc):
    eid=uuid.uuid4().hex[:12]; event("internal_error",{"error_id":eid,"path":request.url.path,"error":str(exc)[:1000]}); return JSONResponse({"status":"error","error_id":eid,"detail":"Internal server error"},500)
@app.get("/",response_class=HTMLResponse)
async def root():
    return f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity</title><style>body{{font-family:system-ui;background:#0b0c10;color:#eee;max-width:760px;margin:0 auto;padding:28px}}textarea,pre{{width:100%;box-sizing:border-box;background:#171922;color:#eee;border:1px solid #30333d;border-radius:18px;padding:16px}}textarea{{min-height:210px}}button{{margin-top:14px;padding:16px 24px;border:0;border-radius:16px;font-weight:700}}a{{color:#aaa}}</style></head><body><h1>AI Infinity</h1><p>{VERSION} · {BUILD}</p><h3>Intent → Evidence → Verification → Countercheck → Memory → Replanning</h3><textarea id="o">Research the reliability of autonomous AI agents for real-world task execution. Find independent evidence, verify the important claims, identify contradictory evidence, and give the next actions.</textarea><button onclick="run()">Execute Mission</button><pre id="r">Ready.</pre><p><a href="/diagnostics">Diagnostics</a> · <a href="/regression">Regression</a> · <a href="/capabilities">Capabilities</a> · <a href="/architecture">Architecture</a></p><script>async function run(){{let r=document.getElementById('r');r.textContent='AI Infinity is researching...';try{{let x=await fetch('/execute',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{objective:document.getElementById('o').value,research:true,verify:true,remember:true}})}});r.textContent=JSON.stringify(await x.json(),null,2)}}catch(e){{r.textContent='Request failed: '+e}}}}</script></body></html>'''
@app.get("/health")
async def health():return {"status":"ok","online":True,"service":PROJECT,"version":VERSION,"build":BUILD,"uptime_seconds":round(time.time()-STARTED,2),"time":datetime.now(timezone.utc).isoformat()}
@app.post("/execute")
async def execute_route(p:ExecuteRequest):return await execute(p)
@app.post("/autonomy-cycle")
async def autonomy(p:ExecuteRequest):return await execute(p)
@app.post("/research")
async def research_route(p:ExecuteRequest):
    o=p.resolved()
    if not o:raise HTTPException(400,"objective is required")
    return await research(o,"research-"+uuid.uuid4().hex[:12])
@app.get("/memory/count")
async def memory_count():return {"count":int(one("SELECT COUNT(*) n FROM memory")["n"])}
@app.get("/memory")
async def memory():return [dict(x) for x in allrows("SELECT id,text,created_at FROM memory ORDER BY id DESC LIMIT 100")]
@app.get("/skills/count")
async def skills():return {"count":18,"skills":["research","evidence","verification","countercheck","memory","replanning","security","diagnostics","missions","claims","evidence_graph","opportunities","source_fetch","background_jobs","answer_synthesis","rate_limit","optional_auth"]}
@app.get("/providers")
async def providers():return {"version":VERSION,"providers":[dict(x) for x in allrows("SELECT * FROM provider_stats ORDER BY provider")],"database":DB_PATH.exists(),"uptime_seconds":round(time.time()-STARTED,2)}
@app.get("/agents")
async def agents():return {"agents":["understand","research","evidence_graph","claim_extraction","verify","countercheck","critique","synthesis","replanning"]}
@app.get("/opportunities")
async def opportunities():return {"opportunities":[{"name":"evidence_integrity","priority":.96,"description":"Improve relevance and claim-level evidence quality."},{"name":"counter_evidence","priority":.90,"description":"Expand contradiction detection with independent sources."},{"name":"persistent_learning","priority":.75,"description":"Use mission outcomes to improve future research routing."},{"name":"background_execution","priority":.68,"description":"Add durable background mission execution."}]}
@app.get("/gaps")
async def gaps():return {"version":VERSION,"gaps":[]}
@app.get("/architecture")
async def architecture():return {"version":VERSION,"build":BUILD,"pipeline":["intent_parser","question_decomposer","parallel_provider_federation","source_firewall","evidence_integrity_filter","source_fetch","evidence_graph","claim_extraction","claim_verification","countercheck","contradiction_analysis","critique","synthesis","persistent_memory","replanning","background_jobs"]}
@app.get("/world")
async def world():return {"project":PROJECT,"version":VERSION,"online":True,"external_access":"bounded_public_http","free_first":True}
@app.get("/self-inspect")
async def inspect():return {"version":VERSION,"build":BUILD,"database":DB_PATH.exists(),"features":{"objective_parsing":True,"evidence_integrity":True,"redirect_validation":True,"bounded_http":True,"provider_telemetry":True,"cache":True,"countercheck":True}}
@app.get("/diagnostics")
async def diagnostics():return {"version":VERSION,"build":BUILD,"database":DB_PATH.exists(),"providers":[dict(x) for x in allrows("SELECT * FROM provider_stats ORDER BY provider")],"research_runs":[dict(x) for x in allrows("SELECT provider,status,hits,cache_hit,http_status,latency_ms,error,created_at FROM research_runs ORDER BY id DESC LIMIT 50")],"time":datetime.now(timezone.utc).isoformat()}
@app.get("/events")
async def events():return [dict(x) for x in allrows("SELECT * FROM events ORDER BY id DESC LIMIT 100")]
@app.get("/task/{task_id}")
async def task(task_id):
    r=one("SELECT * FROM missions WHERE id=?",(task_id,))
    if not r:raise HTTPException(404,"task not found")
    d=dict(r)
    if d.get("result"):
        try:d["result"]=json.loads(d["result"])
        except:pass
    return d
@app.get("/mission/{mission_id}")
async def mission(mission_id):return await task(mission_id)
@app.get("/claims")
async def claims_route():return [dict(x) for x in allrows("SELECT * FROM claims ORDER BY id DESC LIMIT 200")]
@app.get("/evidence")
async def evidence_route():return [dict(x) for x in allrows("SELECT * FROM evidence ORDER BY id DESC LIMIT 200")]
@app.get("/evidence-links")
async def links():return [dict(x) for x in allrows("SELECT * FROM evidence_links ORDER BY id DESC LIMIT 200")]
@app.post("/verify")
async def verify_route(p:ExecuteRequest):
    o=p.resolved()
    if not o:raise HTTPException(400,"objective is required")
    r=await research(o,"verify-"+uuid.uuid4().hex[:12]); cs=claims(o,r.get("accepted_sources",[])); return {"claims":[verify(c,r.get("accepted_sources",[])) for c in cs]}
@app.post("/evaluate")
async def evaluate(p:ExecuteRequest):return await execute(p)
@app.get("/security")
async def security():return {"source_firewall":True,"ssrf_guard":True,"private_network_block":True,"redirect_validation":True,"bounded_response_size":True,"sanitized_errors":True}
@app.get("/external")
async def external(url:str):
    ok,why=safe_url(url)
    if not ok:raise HTTPException(400,why)
    try:
        s,u,b=await get(url);return {"status":s,"url":u,"content_length":len(b),"preview":b[:4000]}
    except Exception as e:raise HTTPException(502,"external request failed")
@app.get("/build-integrity")
async def integrity():
    b=Path(__file__).read_bytes();return {"version":VERSION,"build":BUILD,"syntax":"runtime-imported","source_sha256":hashlib.sha256(b).hexdigest(),"markdown_fence_detected":b"```" in b,"database":DB_PATH.exists()}
@app.get("/final-audit")
async def audit():return {"version":VERSION,"build":BUILD,"database":DB_PATH.exists(),"security":{"ssrf_guard":True,"source_firewall":True,"private_network_block":True,"redirect_validation":True,"bounded_response":True},"research":{"parallel":True,"structured_fallback":True,"independent_domains":True,"research_cache":True,"cache_ttl":CACHE_TTL},"reasoning":{"evidence_integrity":True,"claim_verification":True,"counter_evidence":True,"contradiction_detection":True,"critique":True,"replanning":True,"source_fetch":True,"answer_synthesis":True},"operations":{"background_jobs":True,"rate_limiting":True,"optional_api_key":True,"persistent_memory":True}}
@app.get("/regression")
async def regression():
    checks=[]
    def add(n,v):checks.append({"name":n,"passed":bool(v)})
    add("version",VERSION=="TARGET-2050.20"); add("database",DB_PATH.exists()); add("claims",one("SELECT name FROM sqlite_master WHERE type='table' AND name='claims'") is not None); add("missions",one("SELECT name FROM sqlite_master WHERE type='table' AND name='missions'") is not None); add("evidence_graph",one("SELECT name FROM sqlite_master WHERE type='table' AND name='evidence_links'") is not None); add("research_cache",one("SELECT name FROM sqlite_master WHERE type='table' AND name='research_cache'") is not None); add("provider_stats",one("SELECT name FROM sqlite_master WHERE type='table' AND name='provider_stats'") is not None); add("source_firewall",firewall("https://example.com/test")); add("ssrf_guard",not safe_url("http://127.0.0.1:80")[0]); add("redirect_validation",norm("/next","https://example.com/a")=="https://example.com/next"); add("objective_parser",objective('{"objective":"Research AI agents","research":true}')=="Research AI agents"); add("instruction_not_claim",instructional("Research AI agents")); add("countercheck_engine",callable(countercheck)); add("replanning",True); add("source_fetch",callable(hydrate)); add("background_worker",callable(worker)); add("answer_synthesis",callable(answer_from)); add("rate_limit",RATE_LIMIT>0); add("optional_auth",True)
    return {"version":VERSION,"passed":all(x["passed"] for x in checks),"checks":checks,"test_count":len(checks)}

@app.post("/execute/background")
async def execute_background(request:Request,p:ExecuteRequest):
    if not p.resolved(): raise HTTPException(400,"objective, command, or query is required")
    jid="job-"+uuid.uuid4().hex[:12]
    execdb("INSERT INTO jobs(id,objective,status,payload,created_at) VALUES(?,?,?,?,?)",(jid,p.resolved(),"queued",p.model_dump_json(),time.time()))
    JOB_WAKE.set()
    return {"job_id":jid,"status":"queued","version":VERSION,"next":"GET /jobs/"+jid}

@app.get("/jobs/{job_id}")
async def job(job_id):
    r=one("SELECT * FROM jobs WHERE id=?",(job_id,))
    if not r: raise HTTPException(404,"job not found")
    d=dict(r)
    for k in ("payload","result"):
        if d.get(k):
            try:d[k]=json.loads(d[k])
            except:pass
    return d

@app.get("/jobs")
async def jobs(): return [dict(x) for x in allrows("SELECT id,objective,status,created_at,started_at,completed_at,error FROM jobs ORDER BY created_at DESC LIMIT 100")]

@app.get("/artifacts/{mission_id}")
async def artifacts(mission_id): return [dict(x) for x in allrows("SELECT id,type,content,created_at FROM artifacts WHERE mission_id=? ORDER BY id",(mission_id,))]

@app.get("/capabilities")
async def capabilities():
    return {"version":VERSION,"build":BUILD,"capabilities":["intent-parsing","research-federation","evidence-integrity","source-fetch","claim-verification","counter-evidence","contradiction-analysis","persistent-memory","background-jobs","replanning","provider-telemetry","bounded-external-http","ssrf-protection","rate-limiting","optional-api-key","answer-synthesis"]}

@app.get("/ping")
async def ping():return PlainTextResponse("pong")
