import ast
import hashlib
import json
import os
import re
import sqlite3
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

VERSION = "TARGET-2.4.0"
EVIDENCE_VERSION = "EVIDENCE-GATE-3.0"
BASE = Path(os.getenv("AI_INFINITY_HOME", "/tmp/ai-infinity"))
ARTIFACTS = BASE / "artifacts"
WORK = BASE / "work"
LOGS = BASE / "logs"
DB_PATH = BASE / "ai_infinity.db"
for d in (BASE, ARTIFACTS, WORK, LOGS):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AI Infinity", version=VERSION, description="Evidence-first intelligence fabric with safe execution graphs")
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_MODEL = os.getenv("HF_MODEL", "openai/gpt-oss-120b:cheapest")
HTTP_TIMEOUT = int(os.getenv("AI_INFINITY_HTTP_TIMEOUT", "15"))
MAX_SOURCE_BYTES = int(os.getenv("AI_INFINITY_MAX_SOURCE_BYTES", "120000"))
MAX_DISCOVERY_PER_QUERY = int(os.getenv("AI_INFINITY_DISCOVERY_PER_QUERY", "4"))
MAX_COLLECTED_SOURCES = int(os.getenv("AI_INFINITY_MAX_SOURCES", "12"))
USER_AGENT = "AI-Infinity/2.4.0"


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
    c = db()
    c.execute("INSERT INTO events VALUES(?,?,?,?,?)", (uid("event"), task_id, kind, dump(data), now_iso()))
    c.commit(); c.close()


def save_memory(content: str, kind: str = "general", verified: bool = False) -> str:
    mid = uid("memory")
    c = db(); c.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (mid, kind, content, int(verified), now_iso())); c.commit(); c.close()
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


STOP = set("a an and are as at be been being by for from had has have how i if in into is it its me more most of on or our that the their them there these they this to was were what when where which who why will with you your about become need needed use using used into than then can could should would may might do does did not only all any each other such through based per very real world make made".split())
RESEARCH_TERMS = {"agent","agents","autonomous","autonomy","task","tasks","execution","reliable","reliability","planning","planner","tool","tools","verification","verify","safety","security","memory","evaluation","benchmark","failure","monitoring","control","reasoning","workflow","multi-agent","agentic","alignment","evidence","provenance","grounding","recovery","robust","uncertainty"}
POSITIVE = {"improve","improved","improves","effective","reliable","reliability","success","successful","safe","safety","verified","verification","robust","accurate","reduces","reduce","better","benefit","supports","supported","works","working","validated","valid","outperform","outperforms","strengthen","strengthens"}
NEGATIVE = {"fail","fails","failed","failure","unreliable","unsafe","risk","risks","limitation","limitations","cannot","unable","error","errors","incorrect","inaccurate","harm","harms","degrades","weak","uncertain","poor","worse"}
NOISE_PHRASES = ("create account", "log in", "sign up", "donate", "view pdf", "submission history", "cite this", "html version", "export bibtex", "navigation", "menu", "cookie", "privacy policy", "terms of use", "skip to content", "table of contents")


def tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", text.lower()) if w not in STOP}


def keyword_hits(text: str) -> set:
    low = text.lower(); return {k for k in RESEARCH_TERMS if k in low}


def research_anchor(question: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", question.lower()) if w not in STOP]
    preferred = list(keyword_hits(question))
    ordered = []
    for w in preferred + words:
        if w not in ordered:
            ordered.append(w)
    return " ".join(ordered[:18])


def topic_queries(question: str) -> List[str]:
    a = research_anchor(question)
    topics = [
        f"{a} planning reasoning task decomposition",
        f"{a} tool use agents tool calling",
        f"{a} monitoring verification evaluation benchmarks",
        f"{a} failure recovery reliability robustness",
        f"{a} safety security control risk",
        f"{a} real world execution autonomous systems",
    ]
    return list(dict.fromkeys(x.strip() for x in topics if x.strip()))


def expand_queries(question: str) -> List[str]:
    a = research_anchor(question)
    q = [a, *topic_queries(question), f"systematic review {a}", f"independent evidence {a}", f"benchmark study {a}"]
    return list(dict.fromkeys(x.strip() for x in q if x.strip()))


def canonical_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.netloc.lower().replace("www.", "")
        path = re.sub(r"/{2,}", "/", p.path or "/").rstrip("/") or "/"
        query = "" if "arxiv.org" in host else ""
        return f"{p.scheme.lower()}://{host}{path}{query}"
    except Exception:
        return url


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def source_family(url: str) -> str:
    d = domain_of(url)
    if "arxiv.org" in d: return "research-index"
    if "openalex.org" in d: return "bibliographic-index"
    if "crossref.org" in d or "doi.org" in d: return "doi-index"
    if "wikipedia.org" in d: return "encyclopedia"
    return d or "unknown"


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # Remove common navigation/boilerplate fragments and very short UI runs.
    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = []
    for p in parts:
        s = p.strip()
        low = s.lower()
        if len(s) < 35:
            continue
        if any(n in low for n in NOISE_PHRASES) and len(s) < 180:
            continue
        if re.fullmatch(r"[\w\s|:/.-]{1,180}", s) and s.count(" ") < 8 and not any(k in low for k in RESEARCH_TERMS):
            continue
        kept.append(s)
    if len(kept) >= 2:
        return " ".join(kept)[:MAX_SOURCE_BYTES]
    return text[:MAX_SOURCE_BYTES]


def fetch_url(url: str) -> Dict[str, Any]:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("unsupported_url")
    r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/json,*/*;q=0.2"}, allow_redirects=True, stream=True)
    data = bytearray()
    for chunk in r.iter_content(8192):
        data.extend(chunk)
        if len(data) >= MAX_SOURCE_BYTES:
            break
    raw = bytes(data[:MAX_SOURCE_BYTES])
    ct = r.headers.get("content-type", "")
    raw_text = raw.decode("utf-8", errors="replace")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_text, re.I | re.S)
    title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", title_match.group(1))).strip()[:500] if title_match else r.url[:500]
    text = raw_text
    if "html" in ct.lower() or "<html" in raw_text[:1000].lower():
        text = re.sub(r"(?is)<script.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?</style>", " ", text)
        text = re.sub(r"(?is)<noscript.*?</noscript>", " ", text)
        text = re.sub(r"(?is)<nav.*?</nav>", " ", text)
        text = re.sub(r"(?is)<header.*?</header>", " ", text)
        text = re.sub(r"(?is)<footer.*?</footer>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text, flags=re.I)
        text = re.sub(r"&amp;", "&", text, flags=re.I)
        text = re.sub(r"&quot;", '"', text, flags=re.I)
        text = re.sub(r"&#39;", "'", text, flags=re.I)
    text = clean_text(text)
    return {"url": r.url, "title": title or r.url, "text": text, "hash": hashlib.sha256(raw).hexdigest(), "http_status": r.status_code, "content_type": ct, "content_length": len(raw), "retrieved_at": now_iso()}


def arxiv_search(query: str, limit: int) -> List[Dict[str, Any]]:
    url = "https://export.arxiv.org/api/query?search_query=all:" + quote(query) + f"&start=0&max_results={limit}"
    r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}); r.raise_for_status(); out = []
    for e in re.findall(r"<entry>(.*?)</entry>", r.text, re.S):
        t = re.search(r"<title>(.*?)</title>", e, re.S); s = re.search(r"<summary>(.*?)</summary>", e, re.S); l = re.search(r'<link[^>]+href="([^"]+)"', e)
        if t and l:
            title = re.sub(r"\s+", " ", t.group(1)).strip()
            out.append({"title": title, "url": l.group(1), "snippet": re.sub(r"\s+", " ", s.group(1)).strip() if s else "", "provider": "arxiv", "work_id": re.search(r"arxiv.org/abs/([^/?#]+)", l.group(1)).group(1) if re.search(r"arxiv.org/abs/([^/?#]+)", l.group(1)) else ""})
    return out


def crossref_search(query: str, limit: int) -> List[Dict[str, Any]]:
    r = requests.get("https://api.crossref.org/works?query.bibliographic=" + quote(query) + f"&rows={limit}", timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}); r.raise_for_status(); out = []
    for x in r.json().get("message", {}).get("items", []):
        title = (x.get("title") or [""])[0]; doi = x.get("DOI")
        if title and doi:
            out.append({"title": title, "url": "https://doi.org/" + doi, "snippet": re.sub(r"<[^>]+>", " ", x.get("abstract", ""))[:3000], "provider": "crossref", "work_id": "doi:" + doi.lower()})
    return out


def openalex_search(query: str, limit: int) -> List[Dict[str, Any]]:
    r = requests.get("https://api.openalex.org/works?search=" + quote(query) + f"&per-page={limit}", timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}); r.raise_for_status(); out = []
    for x in r.json().get("results", []):
        title = x.get("display_name", ""); loc = x.get("primary_location") or {}; u = loc.get("landing_page_url") or x.get("doi") or ""
        if title and u:
            doi = x.get("doi") or ""
            wid = ("doi:" + doi.lower().replace("https://doi.org/", "")) if doi else ("openalex:" + str(x.get("id", "")))
            out.append({"title": title, "url": u, "snippet": " ".join((x.get("abstract_inverted_index") or {}).keys())[:3000], "provider": "openalex", "work_id": wid})
    return out


def wikipedia_search(query: str, limit: int) -> List[Dict[str, Any]]:
    r = requests.get("https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=" + quote(query) + f"&srlimit={limit}&format=json", timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}); r.raise_for_status(); out = []
    for x in r.json().get("query", {}).get("search", []):
        title = x.get("title", "")
        if title:
            out.append({"title": title, "url": "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")), "snippet": re.sub(r"<[^>]+>", " ", x.get("snippet", "")), "provider": "wikipedia", "work_id": "wiki:" + title.lower()})
    return out


def discover_sources(question: str) -> Dict[str, Any]:
    providers = [("arxiv", arxiv_search), ("crossref", crossref_search), ("openalex", openalex_search), ("wikipedia", wikipedia_search)]
    items, diagnostics, queries = [], [], expand_queries(question)
    for q in queries:
        for name, fn in providers:
            try:
                batch = fn(q, MAX_DISCOVERY_PER_QUERY)
                for x in batch:
                    x["query"] = q; x["provider"] = name
                items.extend(batch)
                diagnostics.append({"query": q, "provider": name, "count": len(batch), "status": "ok"})
            except Exception as exc:
                diagnostics.append({"query": q, "provider": name, "count": 0, "status": "error", "error": str(exc)[:300]})
    seen = set(); unique = []
    for x in items:
        key = x.get("work_id") or canonical_url(x.get("url", ""))
        if key and key not in seen:
            seen.add(key); unique.append(x)
    return {"queries": queries, "search_anchor": research_anchor(question), "items": unique, "diagnostics": diagnostics}


def relevance(question: str, title: str, text: str, url: str) -> float:
    q_text = research_anchor(question); q = tokens(q_text)
    if not q: return 0.0
    tt, tx = tokens(title), tokens(text[:50000])
    title_overlap = len(q & tt) / max(1, len(q))
    text_overlap = len(q & tx) / max(1, min(len(q), 12))
    qk, sk = keyword_hits(q_text), keyword_hits(title + " " + text[:30000])
    kh = len(qk & sk) / max(1, len(qk))
    d = domain_of(url)
    bonus = 0.07 if any(x in d for x in ("arxiv.org", "doi.org")) else 0.02 if "openalex.org" in d else 0.0
    if "wikipedia.org" in d: bonus -= 0.10
    return round(max(0.0, min(1.0, 0.50 * title_overlap + 0.35 * text_overlap + 0.15 * kh + bonus)), 4)


def work_identity(item: Dict[str, Any]) -> str:
    wid = (item.get("work_id") or "").strip().lower()
    if wid: return wid
    title = re.sub(r"[^a-z0-9]+", " ", item.get("title", "").lower()).strip()
    return "title:" + hashlib.sha1(title.encode()).hexdigest()[:20]


def collect_sources(task_id: str, question: str, discovered: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates = []
    for item in discovered:
        # Wikipedia is useful for discovery/context but cannot become a substantive evidence unit.
        if item.get("provider") == "wikipedia":
            continue
        try:
            got = fetch_url(item["url"])
            score = relevance(question, got["title"], got["text"], got["url"])
            if got["http_status"] >= 400 or len(got["text"]) < 300 or score < 0.14:
                continue
            got.update({"provider": item.get("provider"), "query": item.get("query"), "relevance": score, "source_family": source_family(got["url"]), "work_id": work_identity(item), "substantive": True})
            if got["source_family"] in ("research-index", "bibliographic-index", "doi-index") and len(got["text"]) < 500:
                continue
            candidates.append(got)
        except Exception:
            continue
    candidates.sort(key=lambda x: (x["relevance"], len(x["text"])), reverse=True)
    selected, hashes, works, domains, families = [], set(), set(), set(), set()
    # Prefer independent underlying works and distinct evidence families.
    for x in candidates:
        if x["hash"] in hashes or x["work_id"] in works:
            continue
        d, fam = domain_of(x["url"]), x["source_family"]
        if d in domains and fam in families and len(selected) >= 3:
            continue
        selected.append(x); hashes.add(x["hash"]); works.add(x["work_id"]); domains.add(d); families.add(fam)
        if len(selected) >= MAX_COLLECTED_SOURCES: break
    for x in candidates:
        if len(selected) >= MAX_COLLECTED_SOURCES: break
        if x["hash"] in hashes or x["work_id"] in works: continue
        selected.append(x); hashes.add(x["hash"]); works.add(x["work_id"])
    c = db()
    for x in selected:
        eid = uid("evidence"); x["evidence_id"] = eid
        c.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (eid, task_id, x["url"], x["title"], domain_of(x["url"]), "", x["text"][:1600], x["hash"], "collected", x["relevance"], dump({"provider": x.get("provider"), "query": x.get("query"), "source_family": x.get("source_family"), "work_id": x.get("work_id"), "retrieved_at": x.get("retrieved_at"), "http_status": x.get("http_status")}), now_iso()))
    c.commit(); c.close()
    return selected


def candidate_sentences(text: str) -> List[str]:
    text = clean_text(text)
    raw = re.split(r"(?<=[.!?])\s+", text)
    out = []
    for s in raw:
        s = re.sub(r"\s+", " ", s).strip()
        if not 55 <= len(s) <= 520: continue
        low = s.lower()
        if any(n in low for n in NOISE_PHRASES): continue
        if s.count("|") >= 2: continue
        if len(re.findall(r"[A-Za-z]", s)) < 35: continue
        if not (keyword_hits(s) or any(w in low for w in ("study", "experiment", "results", "evaluation", "performance", "method", "benchmark"))): continue
        out.append(s)
    return out


def extract_claims(question: str, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    anchor = tokens(research_anchor(question)); claims = []; seen = set()
    for source in sources:
        for claim in candidate_sentences(source["text"])[:14]:
            overlap = len(anchor & tokens(claim)) / max(1, len(anchor))
            if overlap < 0.06 and not (keyword_hits(question) & keyword_hits(claim)): continue
            fingerprint = " ".join(sorted(tokens(claim)))
            if fingerprint in seen: continue
            seen.add(fingerprint)
            claims.append({"claim_id": uid("claim"), "text": claim, "evidence_ids": [source["evidence_id"]], "source_urls": [source["url"]], "domains": [domain_of(source["url"])], "works": [source["work_id"]], "source_families": [source["source_family"]]})
    return claims[:40]


def similarity(a: str, b: str) -> float:
    A, B = tokens(a), tokens(b)
    return len(A & B) / max(1, len(A | B))


def polarity(text: str) -> str:
    p, n = len(tokens(text) & POSITIVE), len(tokens(text) & NEGATIVE)
    if p >= n + 2: return "positive"
    if n >= p + 2: return "negative"
    return "mixed"


def verify_claims(claims: List[Dict[str, Any]], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    for c in claims:
        supports = []
        for s in sources:
            sim = similarity(c["text"], s["text"][:50000])
            if sim >= 0.10:
                supports.append((s, sim))
        supports.sort(key=lambda z: z[1], reverse=True)
        for s, sim in supports:
            if s["work_id"] not in c["works"]:
                c["evidence_ids"].append(s["evidence_id"]); c["source_urls"].append(s["url"]); c["domains"].append(domain_of(s["url"])); c["works"].append(s["work_id"]); c["source_families"].append(s["source_family"])
        c["domains"] = list(dict.fromkeys(c["domains"])); c["works"] = list(dict.fromkeys(c["works"])); c["source_families"] = list(dict.fromkeys(c["source_families"]))
        c["mapped_evidence"] = len(c["evidence_ids"]); c["independent_works"] = len(c["works"]); c["independent_domains"] = len(c["domains"])
        c["corroborated"] = len(c["works"]) >= 2
        c["polarity"] = polarity(c["text"])
        conflicts = []
        for s, sim in supports:
            if sim < 0.14 or s["work_id"] in c["works"][:1]: continue
            sp = polarity(s["text"][:30000])
            if c["polarity"] == "positive" and sp == "negative": conflicts.append({"source": s["url"], "work_id": s["work_id"], "type": "polarity_conflict"})
            if c["polarity"] == "negative" and sp == "positive": conflicts.append({"source": s["url"], "work_id": s["work_id"], "type": "polarity_conflict"})
        c["contradictions"] = conflicts
        c["verification_status"] = "corroborated" if c["corroborated"] and not conflicts else "source_supported" if c["mapped_evidence"] >= 1 and not conflicts else "conflicted"
        c["confidence"] = 0.90 if c["verification_status"] == "corroborated" else 0.70 if c["verification_status"] == "source_supported" else 0.30
    unsupported = [c for c in claims if c.get("mapped_evidence", 0) < 1]
    contradictions = [x for c in claims for x in c.get("contradictions", [])]
    return {"claims": claims, "unsupported_claims": unsupported, "contradictions": contradictions, "corroborated_claims": sum(1 for c in claims if c["corroborated"])}


def evidence_gate(sources: List[Dict[str, Any]], verification: Dict[str, Any]) -> Dict[str, Any]:
    substantive = [s for s in sources if s.get("substantive")]
    works = {s.get("work_id") for s in substantive if s.get("work_id")}
    families = {s.get("source_family", source_family(s["url"])) for s in substantive}
    domains = {domain_of(s["url"]) for s in substantive}
    claims = verification.get("claims", [])
    valid_claims = [c for c in claims if c.get("mapped_evidence", 0) >= 1 and len(c.get("text", "")) >= 55]
    contradictions = verification.get("contradictions", [])
    checks = {
        "minimum_substantive_sources": len(substantive) >= 3,
        "independent_underlying_works": len(works) >= 2,
        "independent_source_families": len(families) >= 2,
        "independent_domains": len(domains) >= 2,
        "minimum_valid_claims": len(valid_claims) >= 3,
        "no_unsupported_claims": len(verification.get("unsupported_claims", [])) == 0,
        "no_unresolved_contradictions": len(contradictions) == 0,
        "no_navigation_noise": all(not any(n in c.get("text", "").lower() for n in NOISE_PHRASES) for c in claims),
    }
    passed = all(checks.values())
    reasons = []
    mapping = {
        "minimum_substantive_sources": "fewer_than_3_substantive_sources",
        "independent_underlying_works": "fewer_than_2_independent_works",
        "independent_source_families": "fewer_than_2_independent_source_families",
        "independent_domains": "fewer_than_2_independent_domains",
        "minimum_valid_claims": "fewer_than_3_valid_claims",
        "no_unsupported_claims": "unsupported_claims_present",
        "no_unresolved_contradictions": "contradictions_present",
        "no_navigation_noise": "navigation_noise_present",
    }
    for k, reason in mapping.items():
        if not checks[k]: reasons.append(reason)
    return {"version": EVIDENCE_VERSION, "passed": passed, "checks": checks, "source_count": len(substantive), "work_count": len(works), "domain_count": len(domains), "source_family_count": len(families), "valid_claim_count": len(valid_claims), "unsupported_claim_count": len(verification.get("unsupported_claims", [])), "contradiction_count": len(contradictions), "reasons": reasons}


def call_ai(prompt: str) -> Optional[str]:
    if not HF_TOKEN: return None
    try:
        r = requests.post("https://router.huggingface.co/v1/chat/completions", headers={"Authorization": "Bearer " + HF_TOKEN, "Content-Type": "application/json"}, json={"model": HF_MODEL, "messages": [{"role": "system", "content": "You are AI Infinity's grounded synthesis and planning layer. Use only supplied evidence. Never invent facts, sources, tool results, permissions, or completed actions. Return concise structured text."}, {"role": "user", "content": prompt}], "temperature": 0.15, "max_tokens": 2200}, timeout=45)
        r.raise_for_status(); data = r.json(); return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def grounded_analysis(question: str, sources: List[Dict[str, Any]], verification: Dict[str, Any], gate: Dict[str, Any]) -> str:
    if not gate["passed"]:
        return "Evidence Gate 3.0 did not pass. AI Infinity will not label this research evidence-verified. Reasons: " + ", ".join(gate["reasons"] or ["insufficient_evidence"]) + "."
    evidence = [{"id": s["evidence_id"], "work_id": s["work_id"], "domain": domain_of(s["url"]), "title": s["title"], "url": s["url"], "excerpt": s["text"][:1100]} for s in sources]
    claims = [{k: c.get(k) for k in ("claim_id", "text", "evidence_ids", "works", "domains", "verification_status", "confidence")} for c in verification["claims"] if c.get("mapped_evidence", 0) >= 1]
    prompt = "Question:\n" + question + "\n\nEvidence:\n" + dump(evidence) + "\n\nClaims:\n" + dump(claims) + "\n\nWrite sections: Findings, Evidence, Uncertainty, Disagreements, Limitations. Every factual statement must be traceable to supplied evidence. Do not add outside facts."
    ai = call_ai(prompt)
    if ai: return ai
    lines = ["Findings:"]
    for c in verification["claims"][:8]:
        if c["verification_status"] == "corroborated":
            lines.append(f"- {c['text']} [works: {', '.join(c['works'][:2])}]")
    lines.append("\nEvidence: claims above are linked to stored evidence records.")
    lines.append("Uncertainty: synthesis fallback used because the optional AI synthesis service was unavailable." )
    lines.append("Disagreements: none detected by the deterministic contradiction check.")
    lines.append("Limitations: source discovery and semantic matching are heuristic and should not be treated as proof of truth.")
    return "\n".join(lines)


def build_execution_graph(objective: str, research: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    gate = (research or {}).get("evidence_gate", {})
    verified = bool(gate.get("passed"))
    nodes = [
        {"id": "understand", "type": "intent", "action": "parse_objective", "status": "ready"},
        {"id": "research", "type": "research", "action": "collect_and_verify_evidence", "status": "completed" if research else "ready"},
        {"id": "plan", "type": "planning", "action": "compile_strategy", "status": "ready" if verified else "blocked"},
        {"id": "simulate", "type": "sandbox", "action": "dry_run_plan", "status": "blocked"},
        {"id": "execute", "type": "execution", "action": "registered_safe_action", "status": "blocked"},
        {"id": "verify", "type": "verification", "action": "check_result_and_evidence", "status": "blocked"},
        {"id": "learn", "type": "learning", "action": "extract_reusable_genome", "status": "ready"},
    ]
    edges = [["understand", "research"], ["research", "plan"], ["plan", "simulate"], ["simulate", "execute"], ["execute", "verify"], ["verify", "learn"]]
    return {"version": "2.4", "mode": "safe_dry_run", "objective": objective, "verified_research": verified, "nodes": nodes, "edges": edges, "execution_policy": {"automatic_external_actions": False, "automatic_spending": False, "arbitrary_shell": False, "self_modification": False, "authorized_actions_only": True}}


def compile_plan(objective: str, research: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    gate = (research or {}).get("evidence_gate", {})
    if not gate.get("passed"):
        return {"status": "blocked", "reason": "evidence_gate_not_passed", "steps": [], "requires": ["verified_evidence"]}
    claims = (research or {}).get("claims", [])
    corroborated = [c for c in claims if c.get("verification_status") == "corroborated"]
    steps = [
        {"step": 1, "name": "Define success criteria", "action": "create_success_criteria", "depends_on": []},
        {"step": 2, "name": "Use verified findings", "action": "ground_plan_in_evidence", "depends_on": [1], "evidence_claims": [c["claim_id"] for c in corroborated[:6]]},
        {"step": 3, "name": "Run sandbox/dry-run", "action": "simulate_without_external_side_effects", "depends_on": [2]},
        {"step": 4, "name": "Execute only registered authorized actions", "action": "registered_execution", "depends_on": [3]},
        {"step": 5, "name": "Verify result", "action": "independent_result_check", "depends_on": [4]},
        {"step": 6, "name": "Store reusable intelligence", "action": "create_intelligence_genome", "depends_on": [5]},
    ]
    return {"status": "compiled", "objective": objective, "steps": steps, "evidence_basis": {"corroborated_claims": len(corroborated)}}


def research_pipeline(task_id: str, question: str) -> Dict[str, Any]:
    discovery = discover_sources(question)
    sources = collect_sources(task_id, question, discovery["items"])
    claims = extract_claims(question, sources)
    verification = verify_claims(claims, sources)
    gate = evidence_gate(sources, verification)
    analysis = grounded_analysis(question, sources, verification, gate)
    report = {
        "version": VERSION, "question": question, "queries": discovery["queries"], "search_anchor": discovery["search_anchor"], "discovery": discovery["diagnostics"],
        "sources": [{k: s.get(k) for k in ("evidence_id", "url", "title", "provider", "query", "relevance", "hash", "retrieved_at", "source_family", "work_id")} for s in sources],
        "evidence": [{"evidence_id": s["evidence_id"], "url": s["url"], "domain": domain_of(s["url"]), "title": s["title"], "excerpt": s["text"][:1400], "hash": s["hash"], "relevance": s["relevance"], "source_family": s["source_family"], "work_id": s["work_id"]} for s in sources],
        "claims": verification["claims"], "verification": verification, "contradictions": verification["contradictions"], "evidence_gate": gate, "analysis": analysis,
        "status": "completed" if gate["passed"] else "insufficient_evidence",
        "provenance": {"query_count": len(discovery["queries"]), "source_count": len(sources), "underlying_work_count": len({s["work_id"] for s in sources}), "domains": sorted({domain_of(s["url"]) for s in sources}), "source_families": sorted({s["source_family"] for s in sources}), "source_hashes": sorted({s["hash"] for s in sources}), "evidence_gate": gate["version"]},
    }
    report["execution_graph"] = build_execution_graph(question, report)
    report["compiled_plan"] = compile_plan(question, report)
    rid = save_research(task_id, question, report["status"], report); report["research_id"] = rid
    event(task_id, "research_completed", {"status": report["status"], "gate": gate})
    return report


def safe_calculator(expression: str) -> Any:
    tree = ast.parse(expression, mode="eval")
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.FloorDiv, ast.Load)
    for node in ast.walk(tree):
        if not isinstance(node, allowed): raise ValueError("calculator_expression_not_allowed")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)): raise ValueError("calculator_values_must_be_numeric")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) and isinstance(node.right, ast.Constant) and abs(float(node.right.value)) > 20: raise ValueError("exponent_too_large")
    return eval(compile(tree, "<calculator>", "eval"), {"__builtins__": {}}, {})


def registered_execute(task_id: str, action: str, args: Dict[str, Any]) -> Any:
    if action == "calculator_test": return {"expression": args.get("expression", "2+3*4"), "result": safe_calculator(str(args.get("expression", "2+3*4")))}
    if action == "hash_text": return {"sha256": hashlib.sha256(str(args.get("text", "")).encode()).hexdigest()}
    if action == "validate_python":
        source = str(args.get("source", "")); ast.parse(source); return {"syntax_valid": True, "bytes": len(source.encode())}
    if action == "create_plan":
        objective = str(args.get("objective", "")); return build_execution_graph(objective, None)
    raise ValueError("action_not_registered")


def make_genome(task_id: str, objective: str, research: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    gate = (research or {}).get("evidence_gate", {})
    passed = bool(gate.get("passed"))
    fitness = 1.0 if passed else 0.0
    return {
        "version": "2.4", "objective": objective, "task_id": task_id,
        "strategy": {"intent": "understand", "decompose": "topic_decomposition", "evidence": "canonicalize_clean_extract_map_corroborate", "decision": "evidence_gate_3", "compiler": "execution_graph", "execution": "registered_actions_only", "learning": "preserve_provenance_and_failures"},
        "research_status": (research or {}).get("status", "not_requested"), "fitness": fitness, "reusable": passed,
        "provenance_fields": ["queries", "source_urls", "underlying_work_ids", "domains", "source_families", "hashes", "claim_ids", "evidence_ids", "verification_status", "gate_decision"],
        "evolution": {"status": "proposal_only", "next_mutations": ["stronger semantic contradiction checking", "canonical publisher resolution", "durable memory backend", "sandboxed tool expansion", "benchmark execution reliability"]}
    }


def run_infinity(req: RunRequest) -> Dict[str, Any]:
    task_id = uid("task"); save_task(task_id, req.objective, "running"); event(task_id, "started", {"version": VERSION})
    research = None
    try:
        if req.research: research = research_pipeline(task_id, req.objective)
        memory_id = None
        if req.remember:
            if research and research["status"] == "completed" and research["evidence_gate"]["passed"]:
                memory_id = save_memory(dump({"objective": req.objective, "analysis": research["analysis"], "evidence": research["evidence"], "provenance": research["provenance"]}), "verified_research", True)
            else:
                memory_id = save_memory(dump({"objective": req.objective, "research_status": research["status"] if research else "not_requested", "note": "Not stored as verified intelligence because Evidence Gate 3.0 did not pass."}), "research_uncertainty", False)
        genome = make_genome(task_id, req.objective, research); genome["genome_id"] = save_genome(task_id, genome)
        graph = build_execution_graph(req.objective, research)
        plan = compile_plan(req.objective, research)
        result = {
            "task_id": task_id, "status": "completed", "version": VERSION, "objective": req.objective,
            "intelligence_compiler": {"status": "compiled", "execution_graph": graph, "plan": plan},
            "research": research, "memory_id": memory_id, "intelligence_genome": genome,
            "temporary_minds": [{"name": n, "status": "ready"} for n in ("researcher", "planner", "critic", "simulator", "verifier")],
            "world_model": {"objective": req.objective, "resources": ["local_python", "local_filesystem", "public_web", "evidence_database", "optional_huggingface_model"], "constraints": {"free_first": True, "allow_paid": bool(req.allow_paid), "automatic_spending": False, "arbitrary_shell_execution": False, "automatic_self_modification": False, "authorized_actions_only": True, "external_side_effects_default": False}},
            "evolution": {"status": "proposal_only", "automatic_deployment": False, "improvements": ["evidence integrity", "canonical work identity", "claim-evidence graph", "execution graph", "safe dry-run compiler"]},
            "safety": {"arbitrary_shell_execution": False, "automatic_spending": False, "automatic_self_modification": False, "authorized_actions_only": True, "evidence_gate": True}
        }
        save_task(task_id, req.objective, "completed", result); event(task_id, "completed", {"status": "completed"}); return result
    except Exception as exc:
        recovery = ["retry failed providers", "preserve collected evidence", "fail closed on missing evidence", "do not turn unverified text into facts", "keep external actions disabled"]
        c = db(); c.execute("INSERT INTO failures VALUES(?,?,?,?,?,?)", (uid("failure"), task_id, "run_infinity", str(exc), dump(recovery), now_iso())); c.commit(); c.close(); save_task(task_id, req.objective, "failed", {"error": str(exc), "recovery": recovery}); raise HTTPException(status_code=500, detail="Task failed: " + str(exc))


@app.get("/health")
def health(): return {"status": "ok", "service": "AI Infinity", "version": VERSION, "evidence_gate": EVIDENCE_VERSION}


@app.get("/v1/status")
def status():
    c = db(); counts = {k: c.execute(f"SELECT COUNT(*) AS n FROM {k}").fetchone()["n"] for k in ("tasks", "memories", "evidence", "genomes", "research")}; c.close()
    return {"service": "AI Infinity", "version": VERSION, "counts": counts, "governor": {"free_first": True, "allow_paid_default": False, "automatic_spending": False, "external_side_effects_default": False}, "compiler": "TARGET-2.4", "evidence_gate": EVIDENCE_VERSION}


@app.post("/v1/run")
def run(request: RunRequest): return run_infinity(request)


@app.post("/v1/execute")
def execute(request: ExecuteRequest):
    task_id = uid("task")
    try:
        result = registered_execute(task_id, request.action, request.args)
        c = db(); c.execute("INSERT INTO executions VALUES(?,?,?,?,?,?)", (uid("execution"), task_id, request.action, "completed", dump(result), now_iso())); c.commit(); c.close()
        return {"task_id": task_id, "status": "completed", "action": request.action, "result": result, "safety": {"registered_action_only": True, "external_side_effects": False}}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str):
    c = db(); row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone(); c.close()
    if not row: raise HTTPException(status_code=404, detail="Task not found")
    result = dict(row)
    if result.get("result_json"):
        try: result["result"] = json.loads(result["result_json"])
        except Exception: pass
    return result


@app.get("/v1/memory")
def list_memory(limit: int = 20):
    c = db(); rows = c.execute("SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 100),)).fetchall(); c.close(); return [dict(x) for x in rows]


@app.post("/v1/memory")
def add_memory(request: MemoryRequest): return {"memory_id": save_memory(request.content, request.kind, request.verified)}


@app.get("/v1/genomes")
def list_genomes(limit: int = 20):
    c = db(); rows = c.execute("SELECT * FROM genomes ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 100),)).fetchall(); c.close(); return [dict(x) for x in rows]


@app.post("/v1/genomes")
def add_genome(request: GenomeRequest): return {"genome_id": save_genome("manual", {"objective": request.objective, "strategy": request.strategy, "created_at": now_iso()})}


@app.get("/v1/evidence")
def list_evidence(task_id: Optional[str] = None, limit: int = 50):
    c = db()
    if task_id: rows = c.execute("SELECT * FROM evidence WHERE task_id=? ORDER BY created_at DESC LIMIT ?", (task_id, min(max(limit, 1), 200))).fetchall()
    else: rows = c.execute("SELECT * FROM evidence ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 200),)).fetchall()
    c.close(); return [dict(x) for x in rows]


@app.get("/v1/research/{research_id}")
def get_research(research_id: str):
    c = db(); row = c.execute("SELECT * FROM research WHERE id=?", (research_id,)).fetchone(); c.close()
    if not row: raise HTTPException(status_code=404, detail="Research not found")
    result = dict(row)
    try: result["report"] = json.loads(result.pop("report_json"))
    except Exception: pass
    return result


@app.get("/v1/failures")
def list_failures(limit: int = 50):
    c = db(); rows = c.execute("SELECT * FROM failures ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 100),)).fetchall(); c.close(); return [dict(x) for x in rows]


@app.get("/v1/audit")
def audit(limit: int = 100):
    c = db(); rows = c.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 200),)).fetchall(); c.close(); return [dict(x) for x in rows]


@app.get("/")
def home():
    return HTMLResponse(f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity {VERSION}</title><style>body{{font-family:system-ui;max-width:920px;margin:auto;padding:20px;background:#0b1020;color:#fff}}textarea{{width:100%;min-height:180px;box-sizing:border-box;background:#151b30;color:#fff;border:1px solid #303957;border-radius:12px;padding:14px}}button{{padding:12px 18px;margin:8px 5px 8px 0;border:0;border-radius:10px}}pre{{white-space:pre-wrap;background:#151b30;padding:14px;border-radius:12px;overflow:auto}}</style></head><body><h1>∞ AI Infinity</h1><p>{VERSION} · Evidence Gate 3.0 · Intelligence Compiler 2.4</p><textarea id="objective" placeholder="Enter your objective..."></textarea><br><button onclick="runTask()">Run AI Infinity</button><button onclick="calc()">Test Calculator</button><pre id="out">Ready.</pre><script>async function runTask(){{const objective=document.getElementById('objective').value;if(!objective.trim()){{out.textContent='Enter an objective first.';return;}}out.textContent='Running Evidence → Compiler...';try{{const r=await fetch('/v1/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{objective,research:true,verify:true,remember:true,allow_paid:false}})}});out.textContent=await r.text();}}catch(e){{out.textContent='Request error: '+e;}}}}async function calc(){{const r=await fetch('/v1/execute',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:'calculator_test',args:{{expression:'2+3*4'}}}})}});out.textContent=await r.text();}}</script></body></html>''')


@app.get("/video/{task_id}")
def video(task_id: str):
    p = WORK / task_id / "genius.mp4"
    if not p.exists(): raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(p, media_type="video/mp4")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
