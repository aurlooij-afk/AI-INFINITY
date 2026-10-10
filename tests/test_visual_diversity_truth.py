"""Regression tests for the professional production visual-diversity truth gate."""
import hashlib
from pathlib import Path

import production_closure_3624 as closure


class _FileHasher:
    @staticmethod
    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()


def test_single_scene_ignores_duplicate_asset_rows_for_diversity_threshold(monkeypatch, tmp_path):
    monkeypatch.setattr(closure, "_studio", lambda: _FileHasher())
    asset = tmp_path / "visual.jpg"
    asset.write_bytes(b"same verified visual bytes")

    rows = [{"path": str(asset)}, {"path": str(asset)}]
    result = closure._visual_diversity_metrics(rows, scene_count=1)

    assert result["unique_visual_hashes"] == 1
    assert result["required_unique_visual_hashes"] == 1
    assert result["visual_diversity_ok"] is True


def test_multi_scene_diversity_threshold_is_not_weakened_by_duplicate_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(closure, "_studio", lambda: _FileHasher())
    asset_a = tmp_path / "visual-a.jpg"
    asset_b = tmp_path / "visual-b.jpg"
    asset_a.write_bytes(b"scene visual A")
    asset_b.write_bytes(b"scene visual B")

    rows = [
        {"path": str(asset_a)},
        {"path": str(asset_a)},
        {"path": str(asset_b)},
        {"path": str(asset_b)},
    ]
    result = closure._visual_diversity_metrics(rows, scene_count=4)

    assert result["unique_visual_hashes"] == 2
    assert result["required_unique_visual_hashes"] == 3
    assert result["visual_diversity_ok"] is False


def test_multi_scene_diversity_passes_at_the_existing_sixty_percent_threshold(monkeypatch, tmp_path):
    monkeypatch.setattr(closure, "_studio", lambda: _FileHasher())
    assets = []
    for index, payload in enumerate((b"visual A", b"visual B", b"visual C"), start=1):
        asset = tmp_path / f"visual-{index}.jpg"
        asset.write_bytes(payload)
        assets.append(asset)

    rows = [{"path": str(path)} for path in assets]
    result = closure._visual_diversity_metrics(rows, scene_count=4)

    assert result["unique_visual_hashes"] == 3
    assert result["required_unique_visual_hashes"] == 3
    assert result["visual_diversity_ok"] is True


def test_professional_scene_floor_rejects_single_scene_for_normal_video():
    assert closure._minimum_professional_scene_count({
        "content_type": "video",
        "duration": 20,
        "objective": "Create a cinematic video about resilient creativity",
    }) == 3


def test_professional_scene_floor_scales_up_for_longer_video():
    assert closure._minimum_professional_scene_count({
        "content_type": "video",
        "duration": 60,
        "objective": "Create an educational video",
    }) == 4


def test_professional_scene_floor_allows_explicit_single_shot_brief():
    assert closure._minimum_professional_scene_count({
        "content_type": "video",
        "duration": 60,
        "objective": "Create a one-shot continuous cinematic scene",
    }) == 1


def test_english_fallback_uses_editorial_copy_instead_of_internal_labels():
    studio = closure._studio()
    copy = studio._localized_fallback_copy(
        "English", "Hook",
        "Create a 20-second cinematic video about resilient creativity",
        "resilient creativity",
    )
    assert copy["on_screen"]["Hook"] == "A clear problem"
    assert "resilient creativity" in copy["narration_by_name"]["Hook"].lower()
    assert not copy["narration_by_name"]["Hook"].startswith("Hook.")


def test_long_scene_subtitles_are_split_into_time_bounded_cues(tmp_path):
    studio = closure._studio()
    path = tmp_path / "captions.srt"
    narration = (
        "What makes resilient creativity worth understanding? Name the problem and the change you want people to notice. "
        "Audience needs and practical limits shape useful decisions. Identify them before choosing an approach. "
        "Choose one measurable next step, test it, observe the result, and improve from evidence rather than assumption."
    )
    studio.write_srt([{"actual_duration": 20.0, "narration": narration}], path)
    text = path.read_text(encoding="utf-8")
    cues = [part for part in text.strip().split("\n\n") if "-->" in part]
    assert len(cues) >= 4
    durations = []
    for cue in cues:
        timing = next(line for line in cue.splitlines() if "-->" in line)
        left, right = [x.strip() for x in timing.split("-->")]
        def seconds(value):
            hh, mm, rest = value.split(":")
            ss, ms = rest.split(",")
            return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000
        durations.append(seconds(right) - seconds(left))
    assert max(durations) <= 5.75


def test_fallback_brief_extracts_subject_instead_of_delivery_metadata():
    studio = closure._studio()
    brief = (
        "Create a professional 60-second AI Infinity launch video in English, 16:9, "
        "cinematic, research-backed, with voiceover, background music, captions, "
        "thumbnail, article, SEO metadata, social campaign package and production/rights manifests."
    )
    plan = studio._fallback_creative_plan(
        brief, brief, "long", 60, "general audience", "clear, intelligent, human", {"sources": []}, "English"
    )
    assert plan["title"].lower() == "ai infinity launch"
    assert all("AI Infinity launch" in chapter["visual_query"] for chapter in plan["chapters"])
    assert all("production/rights manifests" not in chapter["visual_query"].lower() for chapter in plan["chapters"])


def test_offline_visual_fallback_is_topic_led_and_truthfully_labelled(monkeypatch, tmp_path):
    studio = closure._studio()
    monkeypatch.setenv("AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS", "1")
    monkeypatch.setenv("AI_INFINITY_TEST_MEDIA", "0")
    monkeypatch.setattr(studio, "FAST_MODE", True)
    first = studio.acquire_scene_asset({
        "topic": "renewable energy",
        "heading": "Energy transition — Opening",
        "visual_focus": "wind turbines and solar panels in a landscape",
        "visual_query": "renewable energy wind turbines solar panels",
        "image_prompt": "Premium editorial image about renewable energy; visual focus: wind turbines and solar panels in a landscape; no text, no logo",
    }, tmp_path, 1, duration=6)
    second = studio.acquire_scene_asset({
        "topic": "renewable energy",
        "heading": "Energy transition — Practical example",
        "visual_focus": "solar panels on an operational farm",
        "visual_query": "renewable energy solar panels on an operational farm",
        "image_prompt": "Premium editorial image about renewable energy; visual focus: solar panels on an operational farm; no text, no logo",
    }, tmp_path, 2, duration=6)

    assert first["fallback"] is True
    assert first["quality_tier"] == "original_vector_editorial_fallback"
    assert first["asset_kind"] == "illustrative_graphic_not_photograph"
    assert first["fallback_record"]["execution_state"] == "DEGRADED"
    assert "not a photograph" in first["visual_quality_notice"].lower()
    assert studio.file_sha256(Path(first["path"])) != studio.file_sha256(Path(second["path"]))
    with studio.Image.open(first["path"]) as image:
        assert image.size == (960, 540)
        image.verify()


def test_unrelated_public_visual_candidate_is_not_accepted(monkeypatch, tmp_path):
    studio = closure._studio()
    monkeypatch.setenv("AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS", "0")
    monkeypatch.setenv("AI_INFINITY_FORCE_PRIMARY_VISUAL_FAILURE", "1")
    monkeypatch.setenv("AI_INFINITY_TEST_MEDIA", "0")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACEHUB_API_TOKEN", raising=False)
    monkeypatch.setattr(studio, "FAST_MODE", True)

    unrelated = tmp_path / "unrelated-candidate.png"
    studio.Image.new("RGB", (640, 360), (55, 70, 90)).save(unrelated, "PNG")

    def unrelated_result(query, outdir, limit=6):
        return [{
            "kind": "image",
            "path": str(unrelated),
            "title": "A cat in a garden",
            "source": "Openverse",
            "source_url": "https://example.invalid/cat",
            "license": "test fixture",
        }]

    for name in ("_nasa_images", "_openverse_images", "_commons_media", "_pexels", "_pixabay"):
        monkeypatch.setattr(studio, name, unrelated_result)

    asset = studio.acquire_scene_asset({
        "topic": "renewable energy",
        "heading": "Solar panels",
        "visual_focus": "solar panels on a utility-scale farm",
        "visual_query": "renewable energy solar panels on a utility-scale farm",
        "image_prompt": "Premium editorial image about renewable energy; visual focus: solar panels on a utility-scale farm",
    }, tmp_path, 3, duration=6)

    assert asset["fallback"] is True
    assert asset["source"] == "AI Infinity original vector editorial fallback"
    assert asset["fallback_record"]["execution_state"] == "DEGRADED"


def test_scene_selection_keeps_opening_middle_and_closing_of_long_storyboard():
    studio = closure._studio()
    chapters = [{"heading": name, "narration": name} for name in [
        "Hook", "Context", "Core idea", "How it works",
        "Real examples", "What changes", "Practical takeaway", "Closing",
    ]]
    selected = studio._select_scene_coverage(chapters, 4)
    assert [row["heading"] for row in selected] == [
        "Hook", "Core idea", "What changes", "Closing"
    ]


def test_scene_selection_keeps_short_arc_for_five_scene_short():
    studio = closure._studio()
    chapters = [{"heading": name} for name in [
        "Hook", "Why it matters", "The key idea", "Practical example", "Takeaway"
    ]]
    selected = studio._select_scene_coverage(chapters, 3)
    assert [row["heading"] for row in selected] == ["Hook", "The key idea", "Takeaway"]


def test_only_truthfully_labelled_vector_art_is_eligible_as_degraded_draft():
    assert closure._is_qualified_illustrative_fallback({
        "fallback": True,
        "quality_tier": "original_vector_editorial_fallback",
        "asset_kind": "illustrative_graphic_not_photograph",
        "rights_status": "original_asset",
        "fallback_record": {
            "truthful": True,
            "execution_state": "DEGRADED",
            "fallback_method": "Topic-led original vector/editorial illustration",
            "quality_change": "AI/public media -> illustrative graphic",
            "license_change": "provider terms -> original generated illustrative asset",
        },
    })


def test_unlabelled_procedural_placeholder_is_not_eligible_as_degraded_draft():
    assert not closure._is_qualified_illustrative_fallback({
        "fallback": True,
        "quality_tier": "original_motion_design_fallback",
        "asset_kind": "placeholder",
        "rights_status": "original_asset",
        "fallback_record": {
            "truthful": True,
            "execution_state": "DEGRADED",
            "fallback_method": "generic shapes",
            "quality_change": "photo -> generic card",
            "license_change": "provider -> original",
        },
    })
