import os
from pathlib import Path

os.environ.setdefault("AI_INFINITY_FAST_MODE", "1")
os.environ.setdefault("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity-production-test")

import studio_ultimate as studio


def test_scene_asset_has_real_local_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_INFINITY_REQUIRE_SOURCE_VISUALS", "0")
    monkeypatch.setattr(studio, "_hf_video", lambda *a, **k: None)
    monkeypatch.setattr(studio, "_hf_image", lambda *a, **k: None)

    def no_remote(*args, **kwargs):
        return []

    for name in ("_nasa_images", "_openverse_images", "_commons_media", "_pexels", "_pixabay"):
        monkeypatch.setattr(studio, name, no_remote)

    scene = {
        "heading": "Fallback production scene",
        "visual_query": "professional technology workspace",
        "image_prompt": "premium editorial technology workspace, cinematic, realistic",
    }

    asset = studio.acquire_scene_asset(scene, Path(tmp_path), 1, prefer_motion=False, duration=5)
    path = Path(asset["path"])

    assert path.is_file()
    assert path.stat().st_size > 20_000
    assert asset.get("fallback") is True
    assert asset.get("quality_tier") == "original_motion_design_fallback"
    assert asset.get("rights_status") == "original_asset"
    assert asset.get("source") == "AI Infinity motion-design generator"


def test_command_language_inference_is_explicit():
    lang, voice = studio._infer_command_language_voice(
        "Create a 60 second Pashto launch video",
        "English",
        "en-US-AriaNeural",
    )
    assert lang == "Pashto"
    assert voice == "ps-AF-LatifaNeural"
