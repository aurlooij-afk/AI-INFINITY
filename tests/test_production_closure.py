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
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "aii-professional-closure-3624" in body
    assert "Production truth gate" in body


def test_alternate_asgi_entrypoint_is_also_wired():
    import ai_infinity_app
    assert any(r.path == "/infinity/studio/system/readiness" for r in ai_infinity_app.app.routes)

def test_short_master_uses_real_media_loop_for_requested_duration(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    out = tmp_path / "out.mp4"
    source.write_bytes(b"real-media-source")
    calls = []

    def fake_ffmpeg(*args, **kwargs):
        calls.append((args, kwargs))
        out.write_bytes(b"real-looped-media" * 1000)

    durations = iter([36.4, 60.0])
    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(studio, "probe_duration", lambda _path: next(durations))

    result = studio._normalize_delivery_duration(source, 60, out)

    assert result == out
    assert out.stat().st_size > 10000
    assert any("-stream_loop" in args and "-1" in args for args, _ in calls)
    assert any("-t" in args and "60.000" in args for args, _ in calls)


def test_strict_visual_gate_blocks_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_INFINITY_REQUIRE_SOURCE_VISUALS", "1")
    import pytest
    with pytest.raises(RuntimeError, match="fallback visual is not eligible"):
        studio.acquire_scene_asset(
            {"heading":"Gate test","visual_query":"professional technology workspace","image_prompt":"professional editorial technology workspace"},
            Path(tmp_path), 1, prefer_motion=False, duration=5,
        )
