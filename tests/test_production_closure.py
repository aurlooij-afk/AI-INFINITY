import os
from pathlib import Path

os.environ["AI_INFINITY_DATA_DIR"] = "/tmp/ai-infinity-closure-test"
os.environ["AI_INFINITY_3601_BACKGROUND"] = "false"
os.environ["AI_INFINITY_FAST_MODE"] = "1"

import main
import studio_ultimate as studio
from fastapi.testclient import TestClient

client = TestClient(main.app)


def test_closure_routes_and_truthful_readiness():
    r = client.get("/infinity/studio/production/contract")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == "TARGET-2050.3624"
    assert data["contract"]["real_artifacts_only"] is True
    assert data["contract"]["no_fake_completion"] is True
    assert data["production_policy"]["strict_visuals"] is True
    assert data["production_policy"]["strict_research"] is True


def test_share_urls_are_host_neutral():
    # The closure deliberately emits a relative URL so stale Render/Blitz
    # domains cannot become embedded in stored project metadata.
    url = studio.share_url("proj_12345678", "final.mp4", "u_123456789012")
    assert url.startswith("/infinity/studio/share/")
    assert "onrender.com" not in url
    assert "blitz.cloud" not in url


def test_root_ui_contains_professional_truth_gate():
    html = main.app.openapi()  # force route registration to finish deterministically
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "aii-professional-closure-3624" in body
    assert "Production truth gate" in body


def test_alternate_asgi_entrypoint_is_also_wired():
    import ai_infinity_app
    assert any(r.path == "/infinity/studio/system/readiness" for r in ai_infinity_app.app.routes)
