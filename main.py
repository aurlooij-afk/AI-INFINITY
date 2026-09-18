import ast
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

# ============================================================
# AI INFINITY — TARGET-2.3.1
# Evidence-First Intelligence Fabric
# ============================================================
# This is a safe, single-file upgrade. It does NOT claim AGI/ASI.
# It uses public sources, evidence provenance, deterministic gates,
# optional Hugging Face synthesis, safe calculator execution, memory,
# audit trails, and an Intelligence Genome.
# ============================================================

VERSION = "TARGET-2.3.2"
BASE = Path(os.getenv("AI_INFINITY_HOME", "/tmp/ai-infinity"))
ARTIFACTS = BASE / "artifacts"
WORK = BASE / "work"
LOGS = BASE / "logs"
DB_PATH = BASE / "ai_infinity.db"
for d in (BASE, ARTIFACTS, WORK, LOGS):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AI Infinity", version=VERSION, description="Evidence-first autonomous intelligence fabric")
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_MODEL = os.getenv("HF_MODEL", "openai/gpt-oss-120b:cheapest")
HTTP_TIMEOUT = int(os.getenv("AI_INFINITY_HTTP_TIMEOUT", "15"))
MAX_SOURCE_BYTES = int(os.getenv("AI_INFINITY_MAX_SOURCE_BYTES", "120000"))
MAX_DISCOVERY_PER_QUERY = int(os.getenv("AI_INFINITY_DISCOVERY_PER_QUERY", "4"))
MAX_COLLECTED_SOURCES = int(os.getenv("AI_INFINITY_MAX_SOURCES", "12"))
USER_AGENT = "AI-Infinity/2.3.2"


class RunRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=12000)
    research: bool = True
    verify: bool = True
    remember: bool = True
    allow_paid: bool = False


class ExecuteRequest(BaseModel):
    action: str
    args: Dict[str, Any] = Field(default_factory=dict)


class MemoryRequest(BaseModel):
    content: str
    kind: str = "general"
    verified: bool = False


class GenomeRequest(BaseModel):
    objective: str
    strategy: Dict[str, Any] = Field(default_factory=dict)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def dump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, objective TEXT, status TEXT, result_json TEXT, created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, kind TEXT, content TEXT, verified INTEGER, created_at TEXT);
    CREATE TABLE IF NOT EXISTS genomes(id TEXT PRIMARY KEY, task_id TEXT, genome_json TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, task_id TEXT, source_url TEXT, source_title TEXT, domain TEXT, claim TEXT, excerpt TEXT, content_hash TEXT, verification_status TEXT, relevance REAL, metadata_json TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS research(id TEXT PRIMARY KEY, task_id TEXT, question TEXT, status TEXT, report_json TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, task_id TEXT, event_type TEXT, data_json TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS failures(id TEXT PRIMARY KEY, task_id TEXT, stage TEXT, error TEXT, recovery_json TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY, task_id TEXT, action TEXT, status TEXT, result_json TEXT, created_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_evidence_task ON evidence(task_id);
    CREATE INDEX IF NOT EXISTS idx_memory_kind ON memories(kind);
    """)
    c.commit(); c.close()


init_db()


def event(task_id: Optional[str], kind: str, data: Any) -> None:
    c = db(); c.execute("INSERT INTO events VALUES(?,?,?,?,?)", (uid("event"), task_id, kind, dump(data), now_iso())); c.commit(); c.close()


def save_memory(content: str, kind: str = "general", verified: bool = False) -> str:
    mid = uid("memory"); c = db()
    c.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (mid, kind, content, int(verified), now_iso())); c.commit(); c.close()
    return mid


def save_task(task_id: str, objective: str, status: str, result: Any = None) -> None:
    t = now_iso(); c = db()
    c.execute("""INSERT INTO tasks(id,objective,status,result_json,created_at,updated_at) VALUES(?,?,?,?,?,?)
                 ON CONFLICT(id) DO UPDATE SET status=excluded.status,result_json=excluded.result_json,updated_at=excluded.updated_at""",
              (task_id, objective, status, dump(result) if result is not None else None, t, t))
    c.commit(); c.close()


def save_genome(task_id: str, genome: Dict[str, Any]) -> str:
    gid = uid("genome"); c = db(); c.execute("INSERT INTO genomes VALUES(?,?,?,?)", (gid, task_id, dump(genome), now_iso())); c.commit(); c.close(); return gid


def save_research(task_id: str, question: str, status: str, report: Dict[str, Any]) -> str:
    rid = uid("research"); c = db(); c.execute("INSERT INTO research VALUES(?,?,?,?,?,?)", (rid, task_id, question, status, dump(report), now_iso())); c.commit(); c.close(); return rid


STOP = set("a an and are as at be been being by for from had has have how i if in into is it its me more most of on or our that the their them there these they this to was were what when where which who why will with you your about become need needed".split())
RESEARCH_TERMS = {"agent","agents","autonomous","autonomy","task","tasks","execution","reliable","reliability","planning","planner","tool","tools","verification","verify","safety","security","memory","evaluation","benchmark","failure","monitoring","control","reasoning","workflow","multi-agent","agentic","alignment","evidence","provenance","grounding"}
POSITIVE = {"improve","improved","improves","effective","reliable","reliability","success","successful","safe","safety","verified","verification","robust","accurate","reduces","reduce","better","benefit","supports","supported","works","working","validated","valid"}
NEGATIVE = {"fail","fails","failed","failure","unreliable","unsafe","risk","risks","limitation","limitations","cannot","unable","error","errors","incorrect","inaccurate","harm","harms","degrades","weak","uncertain"}


def tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", text.lower()) if w not in STOP}


def keyword_hits(text: str) -> set:
    low = text.lower(); return {k for k in RESEARCH_TERMS if k in low}


def canonical_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.netloc.lower().replace("www.", "")
        path = re.sub(r"/{2,}", "/", p.path or "/").rstrip("/") or "/"
        return f"{p.scheme.lower()}://{host}{path}"
    except Exception:
        return url


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def relevance(question: str, title: str, text: str, url: str) -> float:
    q_text = research_anchor(question)
    q = tokens(q_text)
    if not q:
        return 0.0
    tt, tx = tokens(title), tokens(text[:50000])
    title_overlap = len(q & tt) / max(1, len(q))
    text_overlap = len(q & tx) / max(1, min(len(q), 12))
    qk = keyword_hits(q_text)
    sk = keyword_hits(title + " " + text[:30000])
    kh = len(qk & sk) / max(1, len(qk))
    d = domain_of(url)
    bonus = 0.06 if any(x in d for x in ("arxiv.org","doi.org","crossref.org","openalex.org")) else 0.0
    if "wikipedia.org" in d:
        bonus -= 0.05
    score = 0.48*title_overlap + 0.37*text_overlap + 0.15*kh + bonus
    return round(max(0.0, min(1.0, score)), 4)

def fetch_url(url: str) -> Dict[str, Any]:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc: raise ValueError("unsupported_url")
    r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent":USER_AGENT,"Accept":"text/html,text/plain,application/json,*/*;q=0.2"}, allow_redirects=True, stream=True)
    data = bytearray()
    for chunk in r.iter_content(8192):
        data.extend(chunk)
        if len(data) >= MAX_SOURCE_BYTES: break
    raw = bytes(data[:MAX_SOURCE_BYTES]); ct = r.headers.get("content-type","")
    text = raw.decode("utf-8", errors="replace")
    if "html" in ct.lower() or "<html" in text[:1000].lower():
        text = re.sub(r"(?is)<script.*?</script>"," ",text); text = re.sub(r"(?is)<style.*?</style>"," ",text); text = re.sub(r"(?is)<noscript.*?</noscript>"," ",text)
        text = re.sub(r"<[^>]+>"," ",text); text = re.sub(r"&nbsp;"," ",text,flags=re.I); text = re.sub(r"&amp;","&",text,flags=re.I); text = re.sub(r"&quot;",'"',text,flags=re.I); text = re.sub(r"&#39;","'",text,flags=re.I)
    text = re.sub(r"\s+"," ",text).strip()[:MAX_SOURCE_BYTES]
    m = re.search(r"<title[^>]*>(.*?)</title>", raw.decode("utf-8",errors="replace"), re.I|re.S)
    title = re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",m.group(1))).strip()[:500] if m else r.url[:500]
    return {"url":r.url,"title":title or r.url,"text":text,"hash":hashlib.sha256(raw).hexdigest(),"http_status":r.status_code,"content_type":ct,"content_length":len(raw),"retrieved_at":now_iso()}


def arxiv_search(query: str, limit: int) -> List[Dict[str,Any]]:
    url = "https://export.arxiv.org/api/query?search_query=all:"+quote(query)+f"&start=0&max_results={limit}"
    r=requests.get(url,timeout=HTTP_TIMEOUT,headers={"User-Agent":USER_AGENT}); r.raise_for_status(); out=[]
    for e in re.findall(r"<entry>(.*?)</entry>",r.text,re.S):
        t=re.search(r"<title>(.*?)</title>",e,re.S); s=re.search(r"<summary>(.*?)</summary>",e,re.S); l=re.search(r'<link[^>]+href="([^"]+)"',e)
        if t and l: out.append({"title":re.sub(r"\s+"," ",t.group(1)).strip(),"url":l.group(1),"snippet":re.sub(r"\s+"," ",s.group(1)).strip() if s else "","provider":"arxiv"})
    return out


def crossref_search(query: str, limit: int) -> List[Dict[str,Any]]:
    r=requests.get("https://api.crossref.org/works?query.bibliographic="+quote(query)+f"&rows={limit}",timeout=HTTP_TIMEOUT,headers={"User-Agent":USER_AGENT}); r.raise_for_status(); out=[]
    for x in r.json().get("message",{}).get("items",[]):
        title=(x.get("title") or [""])[0]; doi=x.get("DOI")
        if title and doi: out.append({"title":title,"url":"https://doi.org/"+doi,"snippet":x.get("abstract","")[:3000],"provider":"crossref"})
    return out


def openalex_search(query: str, limit: int) -> List[Dict[str,Any]]:
    r=requests.get("https://api.openalex.org/works?search="+quote(query)+f"&per-page={limit}",timeout=HTTP_TIMEOUT,headers={"User-Agent":USER_AGENT}); r.raise_for_status(); out=[]
    for x in r.json().get("results",[]):
        title=x.get("display_name",""); loc=x.get("primary_location") or {}; u=loc.get("landing_page_url") or x.get("doi")
        if title and u: out.append({"title":title,"url":u,"snippet":" ".join((x.get("abstract_inverted_index") or {}).keys())[:3000],"provider":"openalex"})
    return out


def wikipedia_search(query: str, limit: int) -> List[Dict[str,Any]]:
    r=requests.get("https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch="+quote(query)+f"&srlimit={limit}&format=json",timeout=HTTP_TIMEOUT,headers={"User-Agent":USER_AGENT}); r.raise_for_status(); out=[]
    for x in r.json().get("query",{}).get("search",[]):
        title=x.get("title","")
        if title: out.append({"title":title,"url":"https://en.wikipedia.org/wiki/"+quote(title.replace(" ","_")),"snippet":re.sub(r"<[^>]+>"," ",x.get("snippet","")),"provider":"wikipedia"})
    return out


def research_anchor(question: str) -> str:
    """Compress a long objective into search terms that public indexes can match."""
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", question.lower()) if w not in STOP]
    preferred = list(keyword_hits(question))
    ordered = []
    for w in preferred + words:
        if w not in ordered:
            ordered.append(w)
    return " ".join(ordered[:18])


def expand_queries(question: str) -> List[str]:
    base = re.sub(r"\\s+", " ", question).strip()
    anchor = research_anchor(question)
    queries = [
        anchor,
        anchor + " evidence studies benchmarks",
        anchor + " reliability evaluation failure recovery",
        anchor + " safety tool use planning monitoring",
        anchor + " verification autonomous agents",
        "systematic review " + anchor,
        "independent evidence " + anchor,
    ]
    return list(dict.fromkeys(q for q in queries if q.strip()))


def discover_sources(question: str) -> Dict[str,Any]:
    providers=[
        ("arxiv",arxiv_search),
        ("crossref",crossref_search),
        ("openalex",openalex_search),
        ("wikipedia",wikipedia_search)
    ]
    items=[]; diagnostics=[]; queries=expand_queries(question)
    for q in queries:
        for name,fn in providers:
            try:
                batch=fn(q,MAX_DISCOVERY_PER_QUERY)
                for x in batch:
                    x["query"]=q
                    x["provider"]=name
                items.extend(batch)
                diagnostics.append({"query":q,"provider":name,"count":len(batch),"status":"ok"})
            except Exception as exc:
                diagnostics.append({"query":q,"provider":name,"count":0,"status":"error","error":str(exc)[:300]})
    seen=set(); unique=[]
    for x in items:
        key=canonical_url(x.get("url",""))
        if key and key not in seen:
            seen.add(key)
            unique.append(x)
    return {"queries":queries,"search_anchor":research_anchor(question),"items":unique,"diagnostics":diagnostics}

def source_family(url: str) -> str:
    d = domain_of(url)
    if "arxiv.org" in d:
        return "research-index"
    if "openalex.org" in d:
        return "bibliographic-index"
    if "crossref.org" in d or "doi.org" in d:
        return "doi-index"
    if "wikipedia.org" in d:
        return "encyclopedia"
    return d


def collect_sources(task_id: str, question: str, discovered: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    candidates=[]
    for item in discovered:
        try:
            got=fetch_url(item["url"])
            score=relevance(question,got["title"],got["text"],got["url"])
            if got["http_status"] >= 400 or len(got["text"]) < 120 or score < 0.12:
                continue
            got.update({
                "provider":item.get("provider"),
                "query":item.get("query"),
                "relevance":score,
                "source_family":source_family(got["url"])
            })
            candidates.append(got)
        except Exception:
            continue

    # Prefer strong, relevant evidence while deliberately diversifying domains and source families.
    candidates.sort(key=lambda x:(x["relevance"], len(x["text"])), reverse=True)
    selected=[]; hashes=set(); domains=set(); families=set()

    for x in candidates:
        if x["hash"] in hashes:
            continue
        d=domain_of(x["url"]); fam=x["source_family"]
        if d not in domains or fam not in families or len(selected) < 3:
            selected.append(x); hashes.add(x["hash"]); domains.add(d); families.add(fam)
        if len(selected)>=MAX_COLLECTED_SOURCES:
            break

    for x in candidates:
        if len(selected)>=MAX_COLLECTED_SOURCES:
            break
        if x["hash"] not in hashes:
            selected.append(x); hashes.add(x["hash"])

    c=db()
    for x in selected:
        eid=uid("evidence"); x["evidence_id"]=eid
        excerpt=x["text"][:1400]
        c.execute(
            "INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid,task_id,x["url"],x["title"],domain_of(x["url"]),"",excerpt,x["hash"],
             "collected",x["relevance"],
             dump({"provider":x.get("provider"),"query":x.get("query"),
                   "source_family":x.get("source_family"),
                   "retrieved_at":x.get("retrieved_at"),
                   "http_status":x.get("http_status")}),now_iso())
        )
    c.commit(); c.close()
    return selected

def extract_claims(question: str, sources: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    claims=[]; seen=set()
    patterns=[
      r"[^.!?]{0,180}(?:reliable|reliability|verification|verified|safety|failure|planning|monitoring|evaluation|benchmark|tool use|recovery|autonomous)[^.!?]{0,260}[.!?]",
      r"[^.!?]{0,180}(?:shows|found|demonstrates|reports|concludes|suggests|indicates)[^.!?]{0,260}[.!?]"
    ]
    for source in sources:
        text=source["text"][:40000]
        found=[]
        for p in patterns:
            found += re.findall(p,text,re.I)
        for raw in found[:12]:
            claim=re.sub(r"\s+"," ",raw).strip()
            if len(claim)<45:
                continue
            key=" ".join(sorted(tokens(claim)))
            if key in seen:
                continue
            seen.add(key)
            ov=len(tokens(research_anchor(question))&tokens(claim))/max(1,len(tokens(research_anchor(question))))
            if ov < 0.05 and not (keyword_hits(question)&keyword_hits(claim)):
                continue
            claims.append({
                "claim_id":uid("claim"),
                "text":claim,
                "evidence_ids":[source["evidence_id"]],
                "domains":[domain_of(source["url"])],
                "source_urls":[source["url"]]
            })
    return claims[:30]

def similarity(a: str,b: str)->float:
    A,B=tokens(a),tokens(b)
    return len(A&B)/max(1,len(A|B))


def verify_claims(claims: List[Dict[str,Any]], sources: List[Dict[str,Any]]) -> Dict[str,Any]:
    for c in claims:
        support=[]
        for src in sources:
            sim=similarity(c["text"],src["text"][:50000])
            if sim >= 0.07:
                support.append((src,sim))
                if src["evidence_id"] not in c["evidence_ids"]:
                    c["evidence_ids"].append(src["evidence_id"])
                    c["source_urls"].append(src["url"])
                    c["domains"].append(domain_of(src["url"]))

        c["domains"]=list(dict.fromkeys(c["domains"]))
        c["source_urls"]=list(dict.fromkeys(c["source_urls"]))
        c["mapped_evidence"]=len(c["evidence_ids"])
        c["independent_domains"]=len(c["domains"])
        c["corroborated"]=len(c["domains"])>=2

        contradictions=[]
        base_tokens=tokens(c["text"])
        base_pos=len(base_tokens & POSITIVE)
        base_neg=len(base_tokens & NEGATIVE)

        # Conservative contradiction heuristic: only flag a close semantic match
        # when the opposing polarity is materially stronger in another source.
        for src,sim in support:
            if sim < 0.18 or src["url"] in c["source_urls"][:1]:
                continue
            st_tokens=tokens(src["text"][:30000])
            sp=len(st_tokens & POSITIVE); sn=len(st_tokens & NEGATIVE)
            if base_pos>=2 and sn>=4 and sn>sp+2:
                contradictions.append({"claim_id":c["claim_id"],"source":src["url"],"type":"polarity_conflict"})
            elif base_neg>=2 and sp>=4 and sp>sn+2:
                contradictions.append({"claim_id":c["claim_id"],"source":src["url"],"type":"polarity_conflict"})

        c["contradictions"]=contradictions
        c["verification_status"]="corroborated" if c["corroborated"] else "source_supported"
        c["confidence"]=0.90 if c["corroborated"] else 0.72

    unsupported=[c for c in claims if c["mapped_evidence"]<1]
    contradictions=[x for c in claims for x in c.get("contradictions",[])]
    return {
        "claims":claims,
        "unsupported_claims":unsupported,
        "contradictions":contradictions,
        "corroborated_claims":sum(1 for c in claims if c["corroborated"])
    }

def evidence_gate(sources: List[Dict[str,Any]], verification: Dict[str,Any]) -> Dict[str,Any]:
    domains={domain_of(s["url"]) for s in sources}
    families={s.get("source_family",source_family(s["url"])) for s in sources}
    claims=verification.get("claims",[])
    mapped=sum(1 for c in claims if c.get("mapped_evidence",0)>=1)
    unsupported=len(verification.get("unsupported_claims",[]))
    contradictions=len(verification.get("contradictions",[]))
    checks={
        "minimum_sources":len(sources)>=3,
        "independent_domains":len(domains)>=2,
        "source_family_diversity":len(families)>=2,
        "mapped_claims":mapped>=3,
        "no_unsupported_claims":unsupported==0,
        "no_unresolved_contradictions":contradictions==0
    }
    passed=all(checks.values())
    reasons=[]
    if not checks["minimum_sources"]: reasons.append("fewer_than_3_relevant_sources")
    if not checks["independent_domains"]: reasons.append("fewer_than_2_independent_domains")
    if not checks["source_family_diversity"]: reasons.append("insufficient_source_family_diversity")
    if not checks["mapped_claims"]: reasons.append("fewer_than_3_mapped_claims")
    if not checks["no_unsupported_claims"]: reasons.append("unsupported_claims_present")
    if not checks["no_unresolved_contradictions"]: reasons.append("contradictions_present")
    return {
        "passed":passed,
        "checks":checks,
        "source_count":len(sources),
        "domain_count":len(domains),
        "source_family_count":len(families),
        "mapped_claim_count":mapped,
        "unsupported_claim_count":unsupported,
        "contradiction_count":contradictions,
        "reasons":reasons
    }

def call_ai(prompt: str) -> Optional[str]:
    if not HF_TOKEN: return None
    try:
        r=requests.post("https://router.huggingface.co/v1/chat/completions",headers={"Authorization":"Bearer "+HF_TOKEN,"Content-Type":"application/json"},json={"model":HF_MODEL,"messages":[{"role":"system","content":"You are the synthesis layer of AI Infinity. Use only supplied evidence. Never invent citations or facts. If evidence is insufficient, say so."},{"role":"user","content":prompt}],"temperature":0.2,"max_tokens":1800}, timeout=45)
        r.raise_for_status(); data=r.json(); return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def grounded_analysis(question: str, sources: List[Dict[str,Any]], verification: Dict[str,Any], gate: Dict[str,Any]) -> str:
    if not gate["passed"]:
        return "Evidence Gate did not pass. AI Infinity will not label the research evidence-verified. Reasons: " + ", ".join(gate["reasons"] or ["insufficient_evidence"]) + "."
    evidence=[{"id":s["evidence_id"],"domain":domain_of(s["url"]),"title":s["title"],"url":s["url"],"excerpt":s["text"][:1200]} for s in sources]
    prompt="Question:\n"+question+"\n\nVerified evidence records:\n"+dump(evidence)+"\n\nMapped claims:\n"+dump(verification["claims"])+"\n\nWrite a concise synthesis. Every factual statement must be supported by one or more supplied evidence records. Do not add outside facts."
    ai=call_ai(prompt)
    if ai: return ai
    lines=["Evidence-supported synthesis:"]
    for c in verification["claims"][:8]:
        if c["corroborated"]: lines.append("- "+c["text"]+" ["+", ".join(c["domains"][:3])+"]")
    return "\n".join(lines) if len(lines)>1 else "Evidence Gate passed, but no safe synthesis sentence could be constructed."


def research_pipeline(task_id: str, question: str) -> Dict[str,Any]:
    discovery=discover_sources(question)
    sources=collect_sources(task_id,question,discovery["items"])
    claims=extract_claims(question,sources)
    verification=verify_claims(claims,sources)
    gate=evidence_gate(sources,verification)
    analysis=grounded_analysis(question,sources,verification,gate)
    report={"version":VERSION,"question":question,"queries":discovery["queries"],"discovery":discovery["diagnostics"],"sources": [{k:s[k] for k in ("evidence_id","url","title","provider","query","relevance","hash","retrieved_at","source_family") } for s in sources],"evidence": [{"evidence_id":s["evidence_id"],"url":s["url"],"domain":domain_of(s["url"]),"title":s["title"],"excerpt":s["text"][:1400],"hash":s["hash"],"relevance":s["relevance"],"source_family":s.get("source_family",source_family(s["url"]))} for s in sources],"claims":verification["claims"],"verification":verification,"contradictions":verification["contradictions"],"evidence_gate":gate,"analysis":analysis,"status":"completed" if gate["passed"] else "insufficient_evidence","provenance":{"query_count":len(discovery["queries"]),"source_count":len(sources),"domains":sorted({domain_of(s["url"]) for s in sources}),"source_families":sorted({s.get("source_family",source_family(s["url"])) for s in sources}),"source_hashes":sorted({s["hash"] for s in sources})}}
    rid=save_research(task_id,question,report["status"],report); report["research_id"]=rid; event(task_id,"research_completed",{"status":report["status"],"gate":gate}); return report


def safe_calculator(expression: str) -> Any:
    tree=ast.parse(expression,mode="eval")
    allowed=(ast.Expression,ast.BinOp,ast.UnaryOp,ast.Constant,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Mod,ast.Pow,ast.USub,ast.UAdd,ast.FloorDiv,ast.Load)
    for node in ast.walk(tree):
        if not isinstance(node,allowed): raise ValueError("calculator_expression_not_allowed")
        if isinstance(node,ast.Constant) and not isinstance(node.value,(int,float)): raise ValueError("calculator_values_must_be_numeric")
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Pow):
            if isinstance(node.right,ast.Constant) and abs(float(node.right.value))>20: raise ValueError("exponent_too_large")
    return eval(compile(tree,"<calculator>","eval"),{"__builtins__":{}},{})


def registered_execute(task_id: str, action: str, args: Dict[str,Any]) -> Any:
    if action=="calculator_test": return {"expression":args.get("expression","2+3*4"),"result":safe_calculator(str(args.get("expression","2+3*4")))}
    if action=="hash_text": return {"sha256":hashlib.sha256(str(args.get("text","")).encode()).hexdigest()}
    if action=="validate_python":
        source=str(args.get("source","")); ast.parse(source); return {"syntax_valid":True,"bytes":len(source.encode())}
    raise ValueError("action_not_registered")


def make_genome(task_id: str, objective: str, research: Optional[Dict[str,Any]]) -> Dict[str,Any]:
    gate=(research or {}).get("evidence_gate",{})
    return {"version":"2.3","objective":objective,"task_id":task_id,"strategy":{"intent":"understand","decompose":"research","evidence":"collect_filter_map_corroborate","decision":"evidence_gate","execution":"registered_actions_only","learning":"preserve_reusable_provenance"},"research_status":(research or {}).get("status","not_requested"),"fitness":1.0 if gate.get("passed") else 0.5,"reusable":True,"provenance_fields":["queries","source_urls","domains","hashes","claim_ids","evidence_ids","verification_status","gate_decision"],"evolution":{"status":"proposal_only","next_mutations":["improve source-family independence","benchmark relevance threshold","add stronger semantic contradiction checking","add persistent durable storage","add more registered safe tools"]}}


def run_infinity(req: RunRequest) -> Dict[str,Any]:
    task_id=uid("task"); save_task(task_id,req.objective,"running"); event(task_id,"started",{"version":VERSION})
    research=None
    try:
        if req.research: research=research_pipeline(task_id,req.objective)
        memory_id=None
        if req.remember:
            if research and research["status"]=="completed" and research["evidence_gate"]["passed"]:
                memory_id=save_memory(dump({"objective":req.objective,"analysis":research["analysis"],"evidence":research["evidence"],"provenance":research["provenance"]}),"verified_research",True)
            else:
                memory_id=save_memory(dump({"objective":req.objective,"research_status":research["status"] if research else "not_requested","note":"Not stored as verified intelligence because the evidence gate did not pass."}),"research_uncertainty",False)
        genome=make_genome(task_id,req.objective,research); genome["genome_id"]=save_genome(task_id,genome)
        result={"task_id":task_id,"status":"completed","version":VERSION,"objective":req.objective,"intent":{"type":"research" if req.research else "general","objective":req.objective,"priority":"normal"},"world_model":{"objective":req.objective,"resources":["local_python","local_filesystem","public_web","evidence_database","optional_huggingface_model"],"constraints":{"free_first":True,"allow_paid":bool(req.allow_paid),"automatic_spending":False,"arbitrary_shell_execution":False,"automatic_self_modification":False,"authorized_actions_only":True,"evidence_gate":True}},"research":research,"memory_id":memory_id,"intelligence_genome":genome,"temporary_minds":[{"name":"researcher","status":"ready"},{"name":"analyst","status":"ready"},{"name":"critic","status":"ready"},{"name":"verifier","status":"ready"}],"evolution":{"status":"proposal_only","automatic_deployment":False,"improvements":["source-family diversity","claim-evidence graph","contradiction-aware verification","relevance benchmarking","durable memory","sandboxed tool expansion"]},"safety":{"arbitrary_shell_execution":False,"automatic_spending":False,"automatic_self_modification":False,"authorized_actions_only":True,"evidence_gate":True}}
        save_task(task_id,req.objective,"completed",result); event(task_id,"completed",{"status":"completed"}); return result
    except Exception as exc:
        recovery=["retry failed providers","preserve collected evidence","fail closed on missing evidence","do not turn unverified text into facts"]
        c=db(); c.execute("INSERT INTO failures VALUES(?,?,?,?,?,?)",(uid("failure"),task_id,"run_infinity",str(exc),dump(recovery),now_iso())); c.commit(); c.close(); save_task(task_id,req.objective,"failed",{"error":str(exc),"recovery":recovery}); raise HTTPException(status_code=500,detail="Task failed: "+str(exc))


@app.get("/health")
def health(): return {"status":"ok","service":"AI Infinity","version":VERSION}

@app.get("/v1/status")
def status():
    c=db(); counts={k:c.execute(f"SELECT COUNT(*) AS n FROM {k}").fetchone()["n"] for k in ("tasks","memories","evidence","genomes")}; c.close()
    return {"service":"AI Infinity","version":VERSION,"counts":counts,"governor":{"free_first":True,"allow_paid_default":False,"automatic_spending":False}}

@app.post("/v1/run")
def run(request: RunRequest): return run_infinity(request)

@app.post("/v1/execute")
def execute(request: ExecuteRequest):
    task_id=uid("task")
    try:
        result=registered_execute(task_id,request.action,request.args); c=db(); c.execute("INSERT INTO executions VALUES(?,?,?,?,?,?)",(uid("execution"),task_id,request.action,"completed",dump(result),now_iso())); c.commit(); c.close(); return {"task_id":task_id,"status":"completed","action":request.action,"result":result}
    except Exception as exc: raise HTTPException(status_code=400,detail=str(exc))

@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str):
    c=db(); row=c.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone(); c.close()
    if not row: raise HTTPException(status_code=404,detail="Task not found")
    result=dict(row)
    if result.get("result_json"):
        try: result["result"]=json.loads(result["result_json"])
        except Exception: pass
    return result

@app.get("/v1/memory")
def list_memory(limit:int=20):
    c=db(); rows=c.execute("SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",(min(max(limit,1),100),)).fetchall(); c.close(); return [dict(x) for x in rows]

@app.post("/v1/memory")
def add_memory(request:MemoryRequest): return {"memory_id":save_memory(request.content,request.kind,request.verified)}

@app.get("/v1/genomes")
def list_genomes(limit:int=20):
    c=db(); rows=c.execute("SELECT * FROM genomes ORDER BY created_at DESC LIMIT ?",(min(max(limit,1),100),)).fetchall(); c.close(); return [dict(x) for x in rows]

@app.post("/v1/genomes")
def add_genome(request:GenomeRequest): return {"genome_id":save_genome("manual",{"objective":request.objective,"strategy":request.strategy,"created_at":now_iso()})}

@app.get("/v1/evidence")
def list_evidence(task_id:Optional[str]=None,limit:int=50):
    c=db()
    if task_id: rows=c.execute("SELECT * FROM evidence WHERE task_id=? ORDER BY created_at DESC LIMIT ?",(task_id,min(max(limit,1),200))).fetchall()
    else: rows=c.execute("SELECT * FROM evidence ORDER BY created_at DESC LIMIT ?",(min(max(limit,1),200),)).fetchall()
    c.close(); return [dict(x) for x in rows]

@app.get("/v1/research/{research_id}")
def get_research(research_id:str):
    c=db(); row=c.execute("SELECT * FROM research WHERE id=?",(research_id,)).fetchone(); c.close()
    if not row: raise HTTPException(status_code=404,detail="Research not found")
    result=dict(row)
    try: result["report"]=json.loads(result.pop("report_json"))
    except Exception: pass
    return result

@app.get("/v1/failures")
def list_failures(limit:int=50):
    c=db(); rows=c.execute("SELECT * FROM failures ORDER BY created_at DESC LIMIT ?",(min(max(limit,1),100),)).fetchall(); c.close(); return [dict(x) for x in rows]

@app.get("/v1/audit")
def audit(limit:int=100):
    c=db(); rows=c.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT ?",(min(max(limit,1),200),)).fetchall(); c.close(); return [dict(x) for x in rows]

@app.get("/",response_class=HTMLResponse)
def home():
    return f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity {VERSION}</title><style>body{{font-family:system-ui;max-width:900px;margin:auto;padding:20px;background:#0b1020;color:#fff}}textarea{{width:100%;min-height:180px;box-sizing:border-box;background:#151b30;color:#fff;border:1px solid #303957;border-radius:12px;padding:14px}}button{{padding:12px 18px;margin:8px 5px 8px 0;border:0;border-radius:10px}}pre{{white-space:pre-wrap;background:#151b30;padding:14px;border-radius:12px;overflow:auto}}</style></head><body><h1>∞ AI Infinity</h1><p>{VERSION} — Evidence-First Intelligence Fabric</p><textarea id="objective" placeholder="Enter your objective..."></textarea><br><button onclick="runTask()">Run Objective</button><button onclick="calc()">Test Calculator</button><pre id="out">Ready.</pre><script>async function runTask(){{const objective=document.getElementById('objective').value;if(!objective.trim()){{document.getElementById('out').textContent='Enter an objective first.';return;}}document.getElementById('out').textContent='Running AI Infinity...';try{{const r=await fetch('/v1/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{objective,research:true,verify:true,remember:true,allow_paid:false}})}});document.getElementById('out').textContent=await r.text();}}catch(e){{document.getElementById('out').textContent='Request error: '+e;}}}}async function calc(){{const r=await fetch('/v1/execute',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:'calculator_test',args:{{expression:'2+3*4'}}}})}});document.getElementById('out').textContent=await r.text();}}</script></body></html>'''

@app.get("/video/{task_id}")
def video(task_id:str):
    p=WORK/task_id/"genius.mp4"
    if not p.exists(): raise HTTPException(status_code=404,detail="Video not found")
    return FileResponse(p,media_type="video/mp4")

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","8000")))
