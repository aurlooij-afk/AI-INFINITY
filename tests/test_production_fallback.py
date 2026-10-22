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
    assert path.stat().st_size > 1_000

    # Validate the actual fallback pixels, not an arbitrary encoded-file byte threshold.
    from PIL import Image

    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.width >= 320 and image.height >= 240
        extrema = image.convert("RGB").getextrema()
        assert any(low < high for low, high in extrema), "fallback image must contain real visual variation"

    assert asset.get("fallback") is True
    assert asset.get("quality_tier") == "original_vector_editorial_fallback"
    assert asset.get("asset_kind") == "illustrative_graphic_not_photograph"
    assert asset.get("rights_status") == "original_asset"
    assert asset.get("source") == "AI Infinity original vector editorial fallback"
    assert asset.get("fallback_record", {}).get("execution_state") == "DEGRADED"
    assert asset.get("fallback_record", {}).get("truthful") is True


def test_command_language_inference_is_explicit():
    lang, voice = studio._infer_command_language_voice(
        "Create a 60 second Pashto launch video",
        "English",
        "en-US-AriaNeural",
    )
    assert lang == "Pashto"
    assert voice == "ps-AF-LatifaNeural"
