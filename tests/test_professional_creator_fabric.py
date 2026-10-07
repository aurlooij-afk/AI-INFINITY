import os
os.environ.setdefault("AI_INFINITY_DATA_DIR","/tmp/ai-infinity-fabric-test")
os.environ.setdefault("AI_INFINITY_3601_BACKGROUND","false")
os.environ.setdefault("AI_INFINITY_FAST_MODE","1")

from fastapi.testclient import TestClient
import main

client=TestClient(main.app)

def test_fabric_health_has_exact_607_registry():
    r=client.get("/infinity/fabric/health")
    assert r.status_code==200, r.text
    x=r.json()
    assert x["registry_count"]==607
    assert x["counts"]["registered"]==607
    assert x["truthful"] is True

def test_fabric_manifest_is_exactly_numbered():
    r=client.get("/infinity/fabric/manifest")
    assert r.status_code==200
    x=r.json()
    assert len(x["entries"])==607
    assert [e["number"] for e in x["entries"]]==list(range(1,608))
    assert len({e["id"] for e in x["entries"]})==607

def test_fabric_local_runtime_self_test():
    r=client.get("/infinity/fabric/self-test")
    assert r.status_code==200, r.text
    x=r.json()
    assert x["registry_count"]==607
    assert x["local"]["ffmpeg"] is True
    assert x["local"]["ffprobe"] is True
    assert x["local"]["offline_tts"] is True

def test_fabric_route_prefers_real_local_video_path():
    r=client.post("/infinity/fabric/route",json={"task":"render and edit a professional video","free_first":True})
    assert r.status_code==200
    x=r.json()
    assert x["truthful"] is True
    assert x["family"]=="video"
    assert x["selected"] is not None

def test_fabric_capability_lookup():
    r=client.get("/infinity/fabric/capability/1")
    assert r.status_code==200
    x=r.json()
    assert x["number"]==1
    assert x["id"]=="cap-001"


def test_every_registry_entry_has_required_truth_fields():
    from professional_creator_fabric import REGISTRY
    required = {
        "id","number","category","capability","resource/provider","implementation_type",
        "execution_mode","license","commercial_use_state","requires_api_key","requires_gpu",
        "requires_external_network","free_state","quality_tier","integration_state","health_state",
        "fallback_ids","supported_input_types","supported_output_types","language_support","notes",
        "source_url","last_verified"
    }
    for entry in REGISTRY["entries"]:
        assert required.issubset(entry), entry["number"]
        assert entry["number"] >= 1 and entry["number"] <= 607
