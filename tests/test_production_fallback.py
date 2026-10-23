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


def test_english_earth_from_space_fallback_uses_specific_narration_and_overlays():
    copy = studio._localized_fallback_copy(
        "English",
        "Hook",
        "Earth from space",
        "Earth from space",
    )
    narration = " ".join(copy["narration_by_name"].values()).lower()

    assert "audience needs and practical limits" not in narration
    assert "city lights" in narration
    assert "mission notes" in copy["cta"].lower()
    assert copy["on_screen"]["Hook"] == "Earth, seen from orbit"
    assert copy["on_screen"]["Practical example"] == "City lights after dark"
    assert len(set(copy["on_screen"].values())) == len(copy["on_screen"])


def test_earth_from_space_specialization_does_not_leak_into_unrelated_topics():
    copy = studio._localized_fallback_copy(
        "English",
        "Hook",
        "Python programming",
        "Python programming",
    )

    assert copy["on_screen"]["Hook"] != "Earth, seen from orbit"
    assert "city lights" not in " ".join(copy["narration_by_name"].values()).lower()

def test_fallback_creative_plan_uses_topic_filtered_research_for_narration():
    sources = [
        {
            "title": f"Volcanic eruptions: {topic}",
            "summary": statement,
            "url": f"https://example.org/volcano/{index}",
        }
        for index, (topic, statement) in enumerate([
            ("ash columns", "Volcanic eruptions send ash high into the atmosphere, and winds can carry fine particles beyond the vent."),
            ("gas monitoring", "Volcanic eruptions may release changing gas mixtures as magma moves underground before a surface event."),
            ("seismic activity", "Volcanic eruptions are monitored with seismic signals that help scientists track movement beneath the ground."),
            ("ground deformation", "Volcanic eruptions can be preceded by measurable ground deformation as pressure changes within a volcanic system."),
            ("ash hazards", "Volcanic eruptions can spread ash across nearby communities and disrupt air travel far beyond the crater."),
        ], start=1)
    ]
    plan = studio._fallback_creative_plan(
        "Volcanic eruptions",
        "Create a 20 second documentary short about volcanic eruptions",
        "short",
        20,
        "general audience",
        "clear and evidence-led",
        {"sources": sources},
        "English",
        "Volcanic eruptions",
    )

    chapters = plan["chapters"]
    narration = " ".join(chapter["narration"] for chapter in chapters).lower()
    overlays = [chapter["on_screen"] for chapter in chapters]

    assert len(chapters) == 5
    assert "ash high into the atmosphere" in narration
    assert "gas mixtures" in narration
    assert "audience needs and practical limits" not in narration
    assert all(chapter["proof_needed"] for chapter in chapters)
    assert len(set(overlays)) == len(overlays)
    assert all(overlay.casefold() not in {"a clear problem", "know the stakes", "one useful principle", "show the process", "test and improve"} for overlay in overlays)
    assert plan["source_context"]["research_narration_points_used"] == 5
