from __future__ import annotations
import hashlib, json, os, subprocess, time, urllib.error, urllib.parse, urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE=os.environ.get("BASE_URL","https://ai-infinity-ca5e.onrender.com").rstrip("/")
JAR=CookieJar(); OPEN=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))

def req(path,method="GET",payload=None,timeout=90):
    data=None; headers={"User-Agent":"AI-Infinity-live-production-smoke/1"}
    if payload is not None: data=json.dumps(payload).encode(); headers["Content-Type"]="application/json"
    r=urllib.request.Request(BASE+path,data=data,headers=headers,method=method)
    try:
        with OPEN.open(r,timeout=timeout) as resp:
            raw=resp.read(); text=raw.decode("utf-8","replace")
            try: body=json.loads(text)
            except Exception: body=text
            return resp.status,body,raw
    except urllib.error.HTTPError as e:
        raw=e.read(); text=raw.decode("utf-8","replace")
        try: body=json.loads(text)
        except Exception: body=text
        raise AssertionError(f"{method} {path} -> HTTP {e.code}: {body}") from e

def ok(path,method="GET",payload=None,timeout=90):
    s,b,r=req(path,method,payload,timeout); assert 200<=s<300,(path,s,b); return b,r

def wait(pid,seconds=420):
    end=time.time()+seconds; last=None
    while time.time()<end:
        last,_=ok("/infinity/studio/project/"+urllib.parse.quote(pid,safe=""))
        if str(last.get("status","")).lower() in {"completed","completed_with_qc_warnings","failed","cancelled"}: return last
        time.sleep(4)
    raise AssertionError("live creator timeout: "+json.dumps(last)[:4000])

def download(path,out):
    with OPEN.open(urllib.request.Request(BASE+path,headers={"User-Agent":"AI-Infinity-live-production-smoke/1"}),timeout=90) as r: out.write_bytes(r.read())
    assert out.is_file() and out.stat().st_size>10000

def sha(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda:f.read(1024*1024),b""): h.update(c)
    return h.hexdigest()

def probe(p):
    x=subprocess.run(["ffprobe","-v","error","-show_format","-show_streams","-of","json",str(p)],capture_output=True,text=True,timeout=90)
    assert x.returncode==0,x.stderr; return json.loads(x.stdout)

health,_=ok("/health"); assert health.get("canonical") is True,health
canonical,_=ok("/infinity/canonical/health"); assert canonical.get("truthful") is True,canonical
caps,_=ok("/infinity/canonical/capabilities"); assert caps.get("local",{}).get("media_core") is True,caps
pre,_=ok("/infinity/canonical/preflight","POST",{"command":"Create a 20 second cinematic video about resilient creativity"}); assert pre.get("ready") is True,pre

created,_=ok("/infinity/canonical/create","POST",{"command":"Create a 20 second cinematic video about resilient creativity","duration":20,"format":"short","aspect_ratio":"16:9","idempotency_key":"live-production-proof-v2"})
pid=created["project_id"]; v1=created["version"]["version_id"]
state=wait(pid); assert state["status"] in {"completed","completed_with_qc_warnings"},state

truth,_=ok("/infinity/canonical/project/"+pid+"/truth")
# Evidence is the production proof. Accept a truthful VERIFIED current version even
# when the compatibility state field has not been normalized by an older DB row.
cur=truth["current_version"]
assert cur["version_id"]==v1 and cur.get("state")=="VERIFIED",truth
evidence=cur.get("evidence") or {}
if not evidence: evidence=json.loads(cur.get("evidence_json") or "{}")
assert evidence.get("valid") is True,truth

root=Path("/tmp/live-ai-infinity"); root.mkdir(parents=True,exist_ok=True)
one=root/"v1.mp4"; download("/infinity/studio/project/"+pid+"/asset/final.mp4",one)
p1=probe(one); streams=p1.get("streams",[])
video=next((s for s in streams if s.get("codec_type")=="video"),None); audio=next((s for s in streams if s.get("codec_type")=="audio"),None)
duration=float((p1.get("format") or {}).get("duration") or 0)
assert video and video.get("codec_name")=="h264",p1
assert audio and audio.get("codec_name") in {"aac","mp3"},p1
assert duration>2 and abs(duration-20)<=1.0,p1
verify,_=ok("/infinity/studio/project/"+pid+"/verify"); assert verify.get("passed") is True,verify

# Reject the exact failure mode that previously produced abstract/procedural placeholder movies.
project,_=ok("/infinity/canonical/project/"+pid)
asset_rows=project.get("assets") or []
visual_sources=[]
for row in asset_rows:
    meta=row.get("metadata") or {}
    source=str(meta.get("source") or meta.get("model") or "").strip().lower()
    kind=str(row.get("kind") or "").lower()
    if kind == "visual":
        visual_sources.append(source)
assert visual_sources, project
assert all("procedural-editorial-engine" not in s and "motion-design generator" not in s for s in visual_sources), visual_sources
assert any(("hugging face" in s) or ("pexels" in s) or ("pixabay" in s) or ("nasa" in s) or ("openverse" in s) or ("wikimedia" in s) for s in visual_sources), visual_sources

edit,_=ok("/infinity/canonical/project/"+pid+"/command","POST",{"command":"remove the first 2 seconds and make it cinematic"})
v2=edit["version_id"]; assert v2!=v1
two=root/"v2.mp4"; name=edit["artifact"]["name"]
download("/infinity/studio/project/"+pid+"/asset/"+urllib.parse.quote(name,safe=""),two)
p2=probe(two); duration2=float((p2.get("format") or {}).get("duration") or 0)
assert 2<duration2<duration,(duration,duration2)
h1,h2=sha(one),sha(two); assert h1!=h2

versions,_=ok("/infinity/canonical/project/"+pid+"/versions")
ids=[v["version_id"] for v in versions["versions"]]; assert v1 in ids and v2 in ids
undo,_=ok("/infinity/canonical/project/"+pid+"/undo","POST"); assert undo["current_version_id"]==v1,undo
restored=root/"restored.mp4"; download("/infinity/studio/project/"+pid+"/asset/final.mp4",restored)
assert sha(restored)==h1
print(json.dumps({"passed":True,"project_id":pid,"version_1":v1,"version_2":v2,"duration":duration,"edited_duration":duration2,"v1_sha256":h1,"v2_sha256":h2,"bytes":one.stat().st_size},indent=2))
