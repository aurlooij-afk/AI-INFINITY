from __future__ import annotations
import hashlib, io, json, os, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, zipfile
from http.cookiejar import CookieJar
from pathlib import Path
import uuid

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
        if str(last.get("status","")).lower() in {"completed","completed_with_qc_warnings","failed","cancelled"}:
            assert str(last.get("status")).lower() == "completed", last
            assert str(last.get("state") or "").upper() == "COMPLETED", last
            return last
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
    if not shutil.which("ffprobe"):
        raise RuntimeError("acceptance runner is misconfigured: ffprobe is not installed")
    x=subprocess.run(["ffprobe","-v","error","-show_format","-show_streams","-of","json",str(p)],capture_output=True,text=True,timeout=90)
    assert x.returncode==0,x.stderr; return json.loads(x.stdout)

def decode(p):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("acceptance runner is misconfigured: ffmpeg is not installed")
    x=subprocess.run(["ffmpeg","-v","error","-xerror","-i",str(p),"-f","null","-"],capture_output=True,text=True,timeout=180)
    assert x.returncode==0,("full FFmpeg decode failed for "+p.name+": "+x.stderr[:3000])
    return {"passed":True,"stderr_empty":not bool(x.stderr.strip())}

def srt_seconds(value):
    match=re.fullmatch(r"(\\d{2,}):(\\d{2}):(\\d{2}),(\\d{3})",value.strip())
    assert match,("invalid SRT timestamp",value)
    hours,minutes,seconds,millis=map(int,match.groups())
    assert minutes<60 and seconds<60,("invalid SRT timestamp",value)
    return hours*3600+minutes*60+seconds+millis/1000.0

def inspect_srt(raw, media_duration):
    text=raw.decode("utf-8-sig","replace").strip()
    blocks=[b.strip() for b in re.split(r"\\r?\\n\\s*\\r?\\n",text) if b.strip()]
    assert blocks,"captions.srt contains no subtitle cues"
    last_end=0.0
    cues=[]
    for block in blocks:
        rows=block.splitlines()
        timing_index=next((i for i,row in enumerate(rows) if "-->" in row),None)
        assert timing_index is not None,("subtitle cue has no timing line",block[:300])
        left,right=[x.strip().split()[0] for x in rows[timing_index].split("-->",1)]
        start,end=srt_seconds(left),srt_seconds(right)
        lines=[x.strip() for x in rows[timing_index+1:] if x.strip()]
        assert lines,("subtitle cue has no text",block[:300])
        assert end>start,("subtitle cue has non-positive duration",block[:300])
        assert start>=last_end-0.001,("subtitle cues overlap or are out of order",block[:300])
        assert end<=media_duration+0.75,("subtitle cue exceeds final audio/video duration",block[:300])
        assert all(len(line)<=84 for line in lines),("subtitle line exceeds 84 characters",block[:300])
        last_end=end
        cues.append({"start":start,"end":end,"line_count":len(lines),"max_line_chars":max(map(len,lines))})
    return {"passed":True,"cue_count":len(cues),"cues":cues}

expected_revision=os.environ.get("EXPECTED_REVISION","").strip() or os.environ.get("GITHUB_SHA","").strip()
# Prefer an exact build revision when the host exposes one. Render exposes
# RENDER_GIT_COMMIT; other hosts may leave this field blank.
for _ in range(120):
    health,_=ok("/health")
    canonical,_=ok("/infinity/canonical/health")
    served=str(canonical.get("deployment_revision") or health.get("deployment_revision") or "").strip()
    revision_ok=(not expected_revision) or (not served) or served==expected_revision
    if health.get("canonical") is True and canonical.get("truthful") is True and revision_ok:
        if expected_revision and not served:
            print("LIVE_REVISION_UNEXPOSED_CONTINUING", {"expected": expected_revision, "base_url": BASE}, flush=True)
        break
    time.sleep(5)
else:
    raise AssertionError(f"live deployment revision mismatch: expected {expected_revision}, got {canonical.get('deployment_revision') or health.get('deployment_revision')}")
assert health.get("canonical") is True,health
assert canonical.get("truthful") is True,canonical
studio_health,_=ok("/infinity/studio/health")
assert studio_health.get("fast_mode") is True, studio_health
providers,_=ok("/infinity/studio/providers")
print("MEDIA_PROVIDER_DIAGNOSTICS", json.dumps(providers, sort_keys=True), flush=True)
caps,_=ok("/infinity/canonical/capabilities"); assert caps.get("local",{}).get("media_core") is True,caps
contract,_=ok("/infinity/studio/production/contract")
assert contract.get("version")=="TARGET-2050.3624", contract
assert contract.get("contract",{}).get("no_fake_completion") is True, contract
pre,_=ok("/infinity/canonical/preflight","POST",{"command":"Create a 20 second cinematic video about Earth from space"}); assert pre.get("ready") is True,pre

created,_=ok("/infinity/canonical/create","POST",{"command":"Create a 20 second cinematic video about Earth from space","duration":20,"format":"short","aspect_ratio":"16:9","idempotency_key":"live-production-proof-" + (os.environ.get("GITHUB_RUN_ID") or uuid.uuid4().hex)})
pid=created["project_id"]; v1=created["version"]["version_id"]
state=wait(pid); assert state["status"] in {"completed","completed_with_qc_warnings"},state

studio_truth,_=ok("/infinity/studio/project/"+pid+"/truth")
assert studio_truth.get("verified") is True, studio_truth

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
p1=probe(one); decode_v1=decode(one); streams=p1.get("streams",[])
video=next((s for s in streams if s.get("codec_type")=="video"),None); audio=next((s for s in streams if s.get("codec_type")=="audio"),None)
duration=float((p1.get("format") or {}).get("duration") or 0)
assert video and video.get("codec_name")=="h264",p1
assert audio and audio.get("codec_name") in {"aac","mp3"},p1
assert duration>2 and abs(duration-20)<=1.0,p1

assert int(video.get("width") or 0)>int(video.get("height") or 0),("20-second acceptance video must be landscape",video)
artifact_names=["final.mp4","thumbnail.jpg","audio_master.mp3","captions.srt","article.md","seo.json","social_campaign.json","production_manifest.json","rights_manifest.json","package.zip"]
artifact_bytes={}
artifact_evidence={}
for artifact_name in artifact_names:
    _,_,raw=req("/infinity/studio/project/"+urllib.parse.quote(pid,safe="")+"/asset/"+urllib.parse.quote(artifact_name,safe=""))
    assert raw,("artifact download returned zero bytes",artifact_name)
    artifact_bytes[artifact_name]=raw
    artifact_evidence[artifact_name]={"size_bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}
for json_name in ("seo.json","social_campaign.json","production_manifest.json","rights_manifest.json"):
    parsed=json.loads(artifact_bytes[json_name].decode("utf-8-sig"))
    assert isinstance(parsed,(dict,list)),("JSON deliverable has an unexpected root type",json_name)
caption_evidence=inspect_srt(artifact_bytes["captions.srt"],duration)
assert artifact_evidence["final.mp4"]["sha256"]==sha(one),"artifact endpoint final.mp4 differs from the tested app-served version"
with zipfile.ZipFile(io.BytesIO(artifact_bytes["package.zip"])) as package:
    damaged=package.testzip()
    assert damaged is None,("ZIP CRC validation failed",damaged)
    package_entries=set(package.namelist())
package_required=(set(artifact_names)-{"package.zip"})|{"provenance.json"}
missing_package=sorted(package_required-package_entries)
assert not missing_package,("package.zip is missing required deliverables",missing_package)
package_evidence={"passed":True,"entry_count":len(package_entries),"required_entries":sorted(package_required),"missing_entries":[]}
verify,_=ok("/infinity/studio/project/"+pid+"/verify"); assert verify.get("passed") is True,verify

# The professional closure has already checked source visuals, visual diversity,
# rights evidence, narration, required artifacts and media integrity. Provider
# choice remains runtime-dependent.
assert studio_truth.get("checks",{}).get("source_visual_policy_passed") is True, studio_truth
assert studio_truth.get("checks",{}).get("visual_rights_evidence_present") is True, studio_truth
edit,_=ok("/infinity/canonical/project/"+pid+"/command","POST",{"command":"remove the first 2 seconds and make it cinematic"})
v2=edit["version_id"]; assert v2!=v1
two=root/"v2.mp4"; name=edit["artifact"]["name"]
download("/infinity/studio/project/"+pid+"/asset/"+urllib.parse.quote(name,safe=""),two)
p2=probe(two); decode_v2=decode(two); duration2=float((p2.get("format") or {}).get("duration") or 0)
assert 2<duration2<duration,(duration,duration2)
h1,h2=sha(one),sha(two); assert h1!=h2

versions,_=ok("/infinity/canonical/project/"+pid+"/versions")
ids=[v["version_id"] for v in versions["versions"]]; assert v1 in ids and v2 in ids
undo,_=ok("/infinity/canonical/project/"+pid+"/undo","POST"); assert undo["current_version_id"]==v1,undo
restored=root/"restored.mp4"; download("/infinity/studio/project/"+pid+"/asset/final.mp4",restored)
assert sha(restored)==h1

# Wait for a real project-state snapshot write/read-back to settle after the edit+undo.
storage={}
backend_state={}
durability_deadline=time.time()+30
while time.time()<durability_deadline:
    canonical_after,_=ok("/infinity/canonical/health")
    storage=(canonical_after.get("capabilities") or {}).get("storage") or {}
    backend_state=storage.get("project_state_backend_status") or {}
    if storage.get("persistent") is True and backend_state.get("snapshot_verified") is True and len(str(backend_state.get("last_sha256") or ""))==64 and backend_state.get("last_sync_at"):
        break
    time.sleep(1)
else:
    raise AssertionError("live B2 snapshot was not durably read-back verified after edit/undo: "+json.dumps({"storage":storage,"backend":backend_state},sort_keys=True)[:5000])

# Prove a binary artifact is in remote B2 storage, read back byte-for-byte,
# and downloadable independently of the local Render filesystem.
provider_status,_=ok("/infinity/storage/v1/status")
configured_providers=provider_status.get("configured_providers") or []
assert "backblaze_b2" in configured_providers, {
    "configured_providers": configured_providers,
    "message": "The existing B2 state credentials must also be usable for artifacts."
}
storage_list,_=ok("/infinity/storage/v1/assets?limit=200")
remote_asset=None
for row in storage_list.get("assets") or []:
    if row.get("filename") != "final.mp4":
        continue
    details,_=ok("/infinity/storage/v1/assets/"+urllib.parse.quote(str(row.get("asset_id") or ""),safe=""))
    item=details.get("asset") or {}
    if (item.get("metadata") or {}).get("project_id")==pid:
        remote_asset=item
        break
assert remote_asset is not None, "the generated final.mp4 was not registered in durable object storage for the test project"
remote_id=str(remote_asset["asset_id"])
full_verify,_=ok("/infinity/storage/v1/verify/"+urllib.parse.quote(remote_id,safe="")+"?full=true","POST")
checks=full_verify.get("results") or []
b2_check=next((x for x in checks if x.get("provider")=="backblaze_b2"),None)
assert b2_check and b2_check.get("status")=="verified" and (b2_check.get("details") or {}).get("readback_verified") is True, {
    "status":full_verify.get("status"),
    "providers":[{"provider":x.get("provider"),"status":x.get("status")} for x in checks]
}
signed,_=ok("/infinity/storage/v1/url/"+urllib.parse.quote(remote_id,safe=""))
assert signed.get("provider")=="backblaze_b2" and signed.get("url"), {
    "provider":signed.get("provider"),"signed_url_returned":bool(signed.get("url"))
}
try:
    with urllib.request.urlopen(
        urllib.request.Request(signed["url"],headers={"User-Agent":"AI-Infinity-object-readback/1"}),
        timeout=120
    ) as response:
        remote_bytes=response.read()
except Exception as exc:
    # Never print a signed URL or query parameters in CI logs.
    raise AssertionError("signed durable artifact download failed: "+type(exc).__name__) from exc
remote_digest=hashlib.sha256(remote_bytes).hexdigest()
assert len(remote_bytes)==int(remote_asset.get("size_bytes") or -1), "signed download size mismatch"
assert remote_digest==str(remote_asset.get("sha256") or ""), "signed download SHA-256 mismatch"
assert remote_digest==h1, "object-store final.mp4 does not match the app-served restored version"

evidence={
    "passed":True,
    "tested_revision":served or expected_revision,
    "project_id":pid,
    "versions":{"original":v1,"edited":v2,"undo_restored":undo.get("current_version_id")},
    "media":{
        "original":{"size_bytes":one.stat().st_size,"sha256":h1,"duration_seconds":duration,"width":video.get("width"),"height":video.get("height"),"ffprobe_passed":True,"full_decode":decode_v1},
        "edited":{"size_bytes":two.stat().st_size,"sha256":h2,"duration_seconds":duration2,"ffprobe_passed":True,"full_decode":decode_v2},
        "original_restored_after_undo":{"size_bytes":restored.stat().st_size,"sha256":sha(restored),"matches_exact_original":sha(restored)==h1},
    },
    "deliverables":artifact_evidence,
    "captions":caption_evidence,
    "package":package_evidence,
    "edit_undo_reload":{"edit_created_new_version":v2!=v1,"edited_media_hash_differs":h1!=h2,"undo_restored_original_version":undo.get("current_version_id")==v1,"undo_restored_original_hash":sha(restored)==h1,"history_contains_both_versions":v1 in ids and v2 in ids},
    "durability":{
        "project_state":{"persistent":storage.get("persistent"),"snapshot_verified":backend_state.get("snapshot_verified"),"snapshot_sha256":backend_state.get("last_sha256"),"last_sync_at":backend_state.get("last_sync_at")},
        "artifact_object_store":{"provider":"backblaze_b2","asset_id":remote_id,"readback_verified":b2_check["details"].get("readback_verified"),"download_size_bytes":len(remote_bytes),"download_sha256":remote_digest,"matches_app_served_artifact":remote_digest==h1},
    },
}
evidence_path=root/"live-acceptance-evidence.json"
evidence_path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(evidence,ensure_ascii=False,indent=2))
