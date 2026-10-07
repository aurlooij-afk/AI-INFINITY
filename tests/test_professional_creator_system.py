import os

os.environ.setdefault("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity-professional-test")
os.environ["AI_INFINITY_3601_BACKGROUND"] = "false"
os.environ["AI_INFINITY_FAST_MODE"] = "1"

import main
from fastapi.testclient import TestClient

client = TestClient(main.app)


def test_professional_creator_surface_is_mounted():
    r = client.get("/infinity/pro2/health")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["build"].startswith("PROFESSIONAL-CREATOR-SYSTEM")
    assert any(p["name"] == "FFmpeg render/edit" and p["ready"] for p in data["providers"])


def test_professional_creator_ui_is_real_route():
    r = client.get("/infinity/pro2")
    assert r.status_code == 200, r.text
    assert "Professional Creator System" in r.text
    assert "Start production" in r.text
