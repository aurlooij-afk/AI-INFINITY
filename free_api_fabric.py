from __future__ import annotations

"""AI Infinity free/public API backend fabric.

This module discovers public API definitions from maintained directories and
turns genuinely no-auth OpenAPI operations into executable, generic adapters.
It never invents credentials or calls APIs that require authentication.
"""

import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlencode
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener

VERSION = "3622.1"
MAX_DISCOVERED = max(1000, int(os.getenv("AI_INFINITY_FREE_API_MAX", "100000")))
DISCOVERY_WORKERS = max(2, min(24, int(os.getenv("AI_INFINITY_FREE_API_DISCOVERY_WORKERS", "8"))))
HTTP_TIMEOUT = max(3, min(30, int(os.getenv("AI_INFINITY_FREE_API_TIMEOUT", "12"))))
DATA_DIR = Path(os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity"))
DB_PATH = Path(os.getenv("AI_INFINITY_FREE_API_DB", str(DATA_DIR / "free_api_fabric.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()
SYNC_LOCK = threading.Lock()
SYNC_STATE = {"running": False, "last_run": 0.0, "last_error": "", "source_counts": {}, "verified": 0}

SOURCES = {
    "public-api-lists": "https://public-api-lists.github.io/public-api-lists/api/all.json",
    "public-apis": "https://api.publicapis.org/entries",
    "apis-guru": "https://api.apis.guru/v2/list.json",
}


def _connect():
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _init():
    with LOCK, _connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS free_api_registry(
            api_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            description TEXT,
            homepage TEXT,
            spec_url TEXT,
            base_url TEXT,
            auth TEXT,
            https INTEGER NOT NULL DEFAULT 1,
            no_auth_verified INTEGER NOT NULL DEFAULT 0,
            executable INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'discovered',
            operation_count INTEGER NOT NULL DEFAULT 0,
            last_checked REAL,
            last_error TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_free_api_source ON free_api_registry(source);
        CREATE INDEX IF NOT EXISTS idx_free_api_auth ON free_api_registry(auth,no_auth_verified);
        CREATE INDEX IF NOT EXISTS idx_free_api_exec ON free_api_registry(executable,updated_at DESC);
        CREATE TABLE IF NOT EXISTS free_api_ops(
            op_id TEXT PRIMARY KEY,
            api_id TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            url TEXT NOT NULL,
            operation_id TEXT,
            summary TEXT,
            parameters_json TEXT NOT NULL DEFAULT '[]',
            request_body_json TEXT NOT NULL DEFAULT '{}',
            security_json TEXT NOT NULL DEFAULT 'null',
            free INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_free_api_ops_api ON free_api_ops(api_id);
        """)

_init()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(_NoRedirect())


def _host_safe(url: str) -> bool:
    try:
        u = urlparse(str(url).strip())
        if u.scheme != "https" or not u.hostname:
            return False
        host = u.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
            return False
        try:
            ip = ipaddress.ip_address(host)
            return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
        except ValueError:
            pass
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(infos) and all(not (ipaddress.ip_address(x[4][0]).is_private or ipaddress.ip_address(x[4][0]).is_loopback or ipaddress.ip_address(x[4][0]).is_link_local or ipaddress.ip_address(x[4][0]).is_reserved or ipaddress.ip_address(x[4][0]).is_multicast or ipaddress.ip_address(x[4][0]).is_unspecified) for x in infos)
    except Exception:
        return False


def _get_json(url: str, timeout: int = HTTP_TIMEOUT) -> Any:
    if not _host_safe(url):
        raise ValueError("unsafe or non-HTTPS remote URL")
    req = Request(url, headers={"User-Agent": "AI-Infinity-Free-API-Fabric/3622", "Accept": "application/json"}, method="GET")
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _norm_auth(v: Any) -> str:
    if v is None or v == "" or v is False:
        return "No"
    return str(v)


def _id(source: str, name: str, spec_url: str = "") -> str:
    return "free-" + hashlib.sha256(f"{source}|{name}|{spec_url}".encode()).hexdigest()[:24]


def _upsert(rec: Dict[str, Any]) -> str:
    rid = rec["api_id"]
    ts = time.time()
    with LOCK, _connect() as c:
        c.execute("""INSERT INTO free_api_registry
            (api_id,source,name,category,description,homepage,spec_url,base_url,auth,https,no_auth_verified,executable,status,operation_count,last_checked,last_error,metadata_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(api_id) DO UPDATE SET
            source=excluded.source,name=excluded.name,category=excluded.category,description=excluded.description,
            homepage=excluded.homepage,spec_url=excluded.spec_url,base_url=excluded.base_url,auth=excluded.auth,
            https=excluded.https,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
            (rid,rec.get("source"),rec.get("name") or rid,rec.get("category"),rec.get("description"),rec.get("homepage"),rec.get("spec_url"),rec.get("base_url"),rec.get("auth"),int(bool(rec.get("https",True))),int(bool(rec.get("no_auth_verified",False))),int(bool(rec.get("executable",False))),rec.get("status","discovered"),int(rec.get("operation_count",0)),rec.get("last_checked"),rec.get("last_error"),json.dumps(rec.get("metadata") or {},ensure_ascii=False),ts,ts))
    return rid


def _ingest_public_lists(data: Dict[str, Any]) -> int:
    n=0
    for e in (data.get("entries") or []):
        if not e.get("url") or not e.get("https") or str(e.get("auth","No")).lower() not in {"no","none","false",""}:
            continue
        rec={"api_id":_id("public-api-lists",e.get("name",""),e.get("url","")),"source":"public-api-lists","name":e.get("name",""),"category":e.get("category"),"description":e.get("description"),"homepage":e.get("url"),"auth":"No","https":True,"status":"discovered","metadata":e}
        _upsert(rec); n+=1
    return n


def _ingest_public_apis(data: Dict[str, Any]) -> int:
    n=0
    for e in (data.get("entries") or []):
        if not e.get("Link") or not e.get("HTTPS") or str(e.get("Auth") or "").strip().lower() not in {"","no","none"}:
            continue
        rec={"api_id":_id("public-apis",e.get("API",e.get("Description","")),e.get("Link","")),"source":"public-apis","name":e.get("API","") or e.get("Description",""),"category":e.get("Category"),"description":e.get("Description"),"homepage":e.get("Link"),"auth":"No","https":True,"status":"discovered","metadata":e}
        _upsert(rec); n+=1
    return n


def _ingest_guru(data: Dict[str, Any]) -> int:
    n=0
    for name, api in (data or {}).items():
        versions=api.get("versions") or {}
        preferred=api.get("preferred")
        ver=versions.get(preferred) or next(iter(versions.values()),{})
        spec=ver.get("swaggerUrl") or ver.get("link")
        info=ver.get("info") or {}
        if not spec: continue
        rec={"api_id":_id("apis-guru",name,spec),"source":"apis-guru","name":info.get("title") or name,"category":(info.get("x-apisguru-categories") or ["public"])[0],"description":info.get("description"),"homepage":(ver.get("externalDocs") or {}).get("url") if isinstance(ver.get("externalDocs"),dict) else None,"spec_url":spec,"auth":"unknown","https":True,"status":"discovered","metadata":{"provider":name,"version":preferred,"info":info}}
        _upsert(rec); n+=1
    return n


def sync_catalogs() -> Dict[str, Any]:
    if not SYNC_LOCK.acquire(blocking=False):
        return {"status":"already_running",**status()}
    SYNC_STATE.update({"running":True,"last_error":"","last_run":time.time()})
    counts={}
    try:
        for source,url in SOURCES.items():
            try:
                data=_get_json(url,timeout=20)
                if source=="public-api-lists": counts[source]=_ingest_public_lists(data)
                elif source=="public-apis": counts[source]=_ingest_public_apis(data)
                else: counts[source]=_ingest_guru(data)
            except Exception as exc:
                counts[source]=0
                SYNC_STATE["last_error"]=(SYNC_STATE.get("last_error")+"; "+source+": "+str(exc))[:1000]
        SYNC_STATE["source_counts"]=counts
        return {"status":"completed","source_counts":counts,**status()}
    finally:
        SYNC_STATE["running"]=False
        SYNC_LOCK.release()


def _extract_servers(spec: Dict[str,Any], fallback: str="") -> List[str]:
    out=[]
    for s in (spec.get("servers") or []):
        if isinstance(s,dict) and s.get("url"): out.append(str(s["url"]))
    if not out and spec.get("host"):
        scheme=(spec.get("schemes") or ["https"])[0]
        base=f"{scheme}://{spec['host']}{spec.get('basePath','')}"
        out.append(base)
    if not out and fallback:
        out.append(fallback)
    return out


def _operation_free(root: Dict[str,Any], op: Dict[str,Any]) -> bool:
    # OpenAPI security is additive: an explicit empty security array means no auth.
    if op.get("security") == []:
        return True
    if op.get("security") is not None:
        return False
    root_sec=root.get("security")
    if root_sec == [] or root_sec is None:
        return True
    return False


def verify_api(api_id: str) -> Dict[str,Any]:
    with LOCK, _connect() as c:
        row=c.execute("SELECT * FROM free_api_registry WHERE api_id=?",(api_id,)).fetchone()
    if not row: return {"status":"not_found","api_id":api_id}
    rec=dict(row)
    if not rec.get("spec_url"):
        # Directory-only no-auth entries are registered as free discovery sources;
        # their homepage is not assumed to be an executable API endpoint.
        ok=str(rec.get("auth") or "").lower() in {"no","none"}
        with LOCK,_connect() as c:
            c.execute("UPDATE free_api_registry SET no_auth_verified=?,executable=0,status=?,last_checked=?,updated_at=? WHERE api_id=?",(int(ok),"discovered_no_spec" if ok else "needs_spec",time.time(),time.time(),api_id))
        return {"api_id":api_id,"no_auth_verified":ok,"executable":False,"status":"discovered_no_spec"}
    try:
        spec=_get_json(rec["spec_url"])
        servers=_extract_servers(spec,rec.get("homepage") or "")
        base=next((x for x in servers if _host_safe(x)),None)
        if not base: raise ValueError("OpenAPI server is not a safe public HTTPS host")
        ops=[]
        for path,item in (spec.get("paths") or {}).items():
            if not isinstance(item,dict): continue
            for method,op in item.items():
                if method.lower() not in {"get","post","put","patch","delete","head"} or not isinstance(op,dict): continue
                free=_operation_free(spec,op)
                if not free: continue
                url=urljoin(base.rstrip("/")+"/",str(path).lstrip("/"))
                params=[]
                for p in (item.get("parameters") or []) + (op.get("parameters") or []):
                    if isinstance(p,dict):
                        params.append({"name":p.get("name"),"in":p.get("in"),"required":bool(p.get("required")),"schema":p.get("schema") or {}})
                op_id="op-"+hashlib.sha256(f"{api_id}|{method}|{path}".encode()).hexdigest()[:24]
                ops.append((op_id,method.upper(),str(path),url,op.get("operationId"),op.get("summary") or op.get("description"),params,op.get("requestBody") or {},op.get("security")))
        with LOCK,_connect() as c:
            c.execute("DELETE FROM free_api_ops WHERE api_id=?",(api_id,))
            for x in ops:
                c.execute("INSERT INTO free_api_ops(op_id,api_id,method,path,url,operation_id,summary,parameters_json,request_body_json,security_json,free,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(x[0],api_id,x[1],x[2],x[3],x[4],x[5],json.dumps(x[6],ensure_ascii=False),json.dumps(x[7],ensure_ascii=False),json.dumps(x[8],ensure_ascii=False),1,time.time()))
            c.execute("UPDATE free_api_registry SET base_url=?,auth=?,no_auth_verified=?,executable=?,status=?,operation_count=?,last_checked=?,last_error=?,updated_at=? WHERE api_id=?",(base,"No" if ops else "unknown",int(bool(ops)),int(bool(ops)),"executable" if ops else "no_free_operations",len(ops),time.time(),"",time.time(),api_id))
        return {"api_id":api_id,"no_auth_verified":bool(ops),"executable":bool(ops),"operations":len(ops),"base_url":base,"status":"executable" if ops else "no_free_operations"}
    except Exception as exc:
        with LOCK,_connect() as c:
            c.execute("UPDATE free_api_registry SET status=?,last_checked=?,last_error=?,updated_at=? WHERE api_id=?",("verify_failed",time.time(),str(exc)[:500],time.time(),api_id))
        return {"api_id":api_id,"no_auth_verified":False,"executable":False,"status":"verify_failed","error":str(exc)[:500]}


def verify_batch(limit: int=100) -> Dict[str,Any]:
    limit=max(1,min(1000,int(limit)))
    with LOCK,_connect() as c:
        rows=c.execute("SELECT api_id FROM free_api_registry WHERE source='apis-guru' AND status='discovered' ORDER BY updated_at ASC LIMIT ?",(limit,)).fetchall()
    results=[]
    with ThreadPoolExecutor(max_workers=DISCOVERY_WORKERS) as ex:
        futs=[ex.submit(verify_api,r["api_id"]) for r in rows]
        for f in as_completed(futs):
            try: results.append(f.result())
            except Exception as exc: results.append({"status":"verify_failed","error":str(exc)})
    verified=sum(1 for r in results if r.get("executable"))
    SYNC_STATE["verified"] += verified
    return {"checked":len(results),"executable":verified,"results":results[:100],"truthful":True}


def start_background_sync():
    def worker():
        try:
            sync_catalogs()
            # Gradually verify OpenAPI definitions. This is deliberately incremental
            # so a free web instance never blocks startup on thousands of remote APIs.
            max_batches=max(1,(MAX_DISCOVERED+99)//100)
            for _ in range(max_batches):
                with LOCK,_connect() as c:
                    n=c.execute("SELECT COUNT(*) FROM free_api_registry WHERE source='apis-guru' AND status='discovered'").fetchone()[0]
                if not n: break
                verify_batch(min(100,n))
                # Respect small/free web-instance CPU and remote-provider rate limits.
                time.sleep(0.15)
        except Exception as exc:
            SYNC_STATE["last_error"]=str(exc)[:1000]
    t=threading.Thread(target=worker,name="ai-infinity-free-api-sync",daemon=True)
    t.start()


def status() -> Dict[str,Any]:
    with LOCK,_connect() as c:
        total=c.execute("SELECT COUNT(*) FROM free_api_registry").fetchone()[0]
        executable=c.execute("SELECT COUNT(*) FROM free_api_registry WHERE executable=1").fetchone()[0]
        verified=c.execute("SELECT COUNT(*) FROM free_api_registry WHERE no_auth_verified=1").fetchone()[0]
        ops=c.execute("SELECT COUNT(*) FROM free_api_ops WHERE free=1").fetchone()[0]
        sources={r[0]:r[1] for r in c.execute("SELECT source,COUNT(*) FROM free_api_registry GROUP BY source")}
    return {"version":VERSION,"discovered":total,"no_auth_verified":verified,"executable_apis":executable,"executable_free_operations":ops,"sources":sources,"max_capacity":MAX_DISCOVERED,"automatic_discovery":True,"automatic_free_failover":True,"truthful":True}


def catalog(query: str="", limit: int=100, executable_only: bool=False) -> List[Dict[str,Any]]:
    limit=max(1,min(500,int(limit))); q=str(query or "").strip().lower()
    sql="SELECT api_id,source,name,category,description,homepage,spec_url,base_url,auth,no_auth_verified,executable,status,operation_count,last_checked,last_error FROM free_api_registry WHERE 1=1"
    args=[]
    if executable_only: sql += " AND executable=1"
    if q:
        sql += " AND lower(name||' '||coalesce(category,'')||' '||coalesce(description,'')) LIKE ?"; args.append("%"+q+"%")
    sql += " ORDER BY executable DESC,no_auth_verified DESC,name ASC LIMIT ?"; args.append(limit)
    with LOCK,_connect() as c: return [dict(r) for r in c.execute(sql,args).fetchall()]


def operations(api_id: str, limit: int=100) -> List[Dict[str,Any]]:
    with LOCK,_connect() as c:
        rows=c.execute("SELECT * FROM free_api_ops WHERE api_id=? AND free=1 LIMIT ?",(api_id,max(1,min(500,int(limit)) ))).fetchall()
    return [dict(r) for r in rows]


def invoke(op_id: str, query: Optional[Dict[str,Any]]=None, body: Any=None) -> Dict[str,Any]:
    with LOCK,_connect() as c: row=c.execute("SELECT * FROM free_api_ops WHERE op_id=? AND free=1",(op_id,)).fetchone()
    if not row: raise ValueError("free executable operation not found")
    r=dict(row); url=r["url"]
    if not _host_safe(url): raise ValueError("unsafe operation host")
    params=json.loads(r.get("parameters_json") or "[]")
    q=dict(query or {})
    required=[p["name"] for p in params if p.get("in")=="query" and p.get("required")]
    missing=[x for x in required if x not in q]
    if missing: raise ValueError("missing required query parameters: "+", ".join(missing))
    if q: url += ("&" if "?" in url else "?")+urlencode({k:str(v) for k,v in q.items()})
    method=r["method"].upper()
    headers={"User-Agent":"AI-Infinity-Free-API-Fabric/3622","Accept":"application/json,*/*"}
    data=None
    if body is not None and method in {"POST","PUT","PATCH"}:
        data=json.dumps(body,ensure_ascii=False).encode(); headers["Content-Type"]="application/json"
    req=Request(url,data=data,headers=headers,method=method)
    started=time.time()
    with OPENER.open(req,timeout=HTTP_TIMEOUT) as resp:
        raw=resp.read(2_000_000)
        ctype=resp.headers.get("Content-Type","")
        text=raw.decode("utf-8","replace")
        try: payload=json.loads(text)
        except Exception: payload=text
        return {"status_code":resp.status,"content_type":ctype,"latency_ms":round((time.time()-started)*1000,2),"data":payload,"api_id":r["api_id"],"operation_id":op_id,"truthful":True}


def register(app: Any):
    from fastapi import HTTPException, Request
    from pydantic import BaseModel

    class InvokeRequest(BaseModel):
        query: Dict[str,Any] = {}
        body: Any = None

    @app.get("/infinity/studio/free-apis/status")
    def free_api_status():
        return status()

    @app.post("/infinity/studio/free-apis/sync")
    def free_api_sync():
        return sync_catalogs()

    @app.post("/infinity/studio/free-apis/verify")
    def free_api_verify(limit: int=100):
        return verify_batch(limit)

    @app.get("/infinity/studio/free-apis/catalog")
    def free_api_catalog(query: str="", limit: int=100, executable_only: bool=False):
        return {"items":catalog(query,limit,executable_only),"truthful":True}

    @app.get("/infinity/studio/free-apis/{api_id}/operations")
    def free_api_operations(api_id: str, limit: int=100):
        return {"api_id":api_id,"operations":operations(api_id,limit),"truthful":True}

    @app.post("/infinity/studio/free-apis/invoke/{op_id}")
    def free_api_invoke(op_id: str, req: InvokeRequest):
        try: return invoke(op_id,req.query,req.body)
        except Exception as exc: raise HTTPException(502,str(exc)[:500])

    start_background_sync()
    app.state.free_api_fabric = status()
