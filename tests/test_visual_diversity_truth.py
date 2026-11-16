"""Regression tests for the professional production visual-diversity truth gate."""
import hashlib
import re
from pathlib import Path

import ai_infinity_canonical as canonical
import production_closure_3624 as closure
import studio_ultimate as studio


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
    }) == 8


def test_professional_scene_floor_rejects_four_shot_sixty_second_launch_baseline():
    brief = {
        "content_type": "video",
        "format": "long",
        "duration": 60,
        "objective": "Create a professional 60-second AI Infinity launch video",
    }
    required = closure._minimum_professional_scene_count(brief)
    assert required == 8
    assert 4 < required, "the previously passing four-shot launch artifact must now remain unverified"


def test_professional_scene_floor_scales_launch_beats_to_requested_duration():
    assert closure._minimum_professional_scene_count({
        "content_type": "video", "format": "long", "duration": 30,
        "objective": "Create a 30-second branded product launch film",
    }) == 4
    assert closure._minimum_professional_scene_count({
        "content_type": "video", "format": "long", "duration": 60,
        "objective": "Create a 60-second branded product launch film",
    }) == 8


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

    # Exercise the exact validator used by production truth, not just local syntax.
    metrics = closure._caption_timing_metrics(path, 20.0)
    assert metrics["cue_count"] >= 4
    assert metrics["timing_valid"] is True


def test_scene_duration_allocation_keeps_caption_timeline_inside_requested_runtime(tmp_path):
    # These planned weights used to be independently clamped to four seconds:
    # 2 + 2 + 6 + 6 + 4 became 4 + 4 + 6 + 6 + 4 = 24 seconds, then the
    # final video was trimmed to 20 seconds while the subtitle file stayed 24s.
    planned = [
        {"heading": "Opening", "duration": 2.0, "narration": "Introduce the core creative challenge and its audience."},
        {"heading": "Context", "duration": 2.0, "narration": "Explain the practical context before considering solutions."},
        {"heading": "Core idea", "duration": 6.0, "narration": "Show how a clear creative decision changes the resulting work."},
        {"heading": "Application", "duration": 6.0, "narration": "Apply the approach to a specific example that viewers can follow."},
        {"heading": "Takeaway", "duration": 4.0, "narration": "End with one concrete next step the audience can use."},
    ]

    allocated = studio._allocate_scene_durations(planned, 20.0)
    durations = [float(scene["duration"]) for scene in allocated]
    assert len(allocated) == 5
    assert all(duration >= 4.0 for duration in durations), durations
    assert abs(sum(durations) - 20.0) <= 0.001, durations

    captions = tmp_path / "captions.srt"
    studio.write_srt(allocated, captions)
    metrics = closure._caption_timing_metrics(captions, 20.0)
    assert metrics["timing_valid"] is True, metrics
    assert metrics["cue_count"] >= 4, metrics
    assert metrics["max_cue_duration_seconds"] <= 5.75, metrics
    assert metrics["coverage_ratio"] >= 0.80, metrics


def test_scene_render_cache_signature_changes_when_caption_relevant_inputs_change():
    request = {"language": "English", "voice": "default", "aspect_ratio": "16:9"}
    scene = {
        "heading": "The core idea",
        "narration": "Explain the subject clearly and give the viewer a useful next step.",
        "on_screen": "A useful next step",
        "visual_query": "creator editing a video timeline",
        "duration": 4.0,
    }
    baseline = studio._scene_render_signature(scene, request, 1)
    assert studio._scene_render_signature(dict(scene), dict(request), 1) == baseline

    changed_duration = dict(scene, duration=5.0)
    assert studio._scene_render_signature(changed_duration, request, 1) != baseline

    changed_narration = dict(scene, narration="Explain the subject differently and give a concrete example.")
    assert studio._scene_render_signature(changed_narration, request, 1) != baseline



def test_creator_ui_uses_production_truth_for_master_delivery_and_keeps_mobile_navigation_single_row():
    ui = studio.CREATOR_STUDIO_UI
    assert 'api(projectUrl+"/truth").catch(function(){return null;})' in ui
    assert 'truth&&truth.verified===true' in ui
    assert 'x.status==="completed_with_qc_warnings"' in ui
    assert 'return "review-required"' in ui
    assert '.nav{display:flex;flex:0 0 auto;gap:4px;width:auto}' in ui
    assert "Evidence per project" in ui
    assert "Fallback labelled per run" in ui


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
    assert all(chapter["topic"] == "AI Infinity launch" for chapter in plan["chapters"])
    assert all(
        "content creator" in chapter["visual_query"]
        for chapter in plan["chapters"]
    )
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


def test_explicit_brief_topic_overrides_generic_project_title():
    studio = closure._studio()
    plan = studio._fallback_creative_plan(
        "Canonical Runtime Smoke",
        "Create a useful 20-second explainer about renewable energy.",
        "short", 20, "general audience", "clear", {"sources": []}, "English",
        topic="renewable energy",
    )
    assert plan["title"] == "Canonical Runtime Smoke"
    assert all("renewable energy" in chapter["visual_query"].lower() for chapter in plan["chapters"])
    assert all("Canonical Runtime Smoke" not in chapter["visual_query"] for chapter in plan["chapters"])



def test_subtitle_timestamp_rounding_carries_across_clock_boundaries():
    studio = closure._studio()
    assert studio._subtitle_time(4.9999) == "00:00:05,000"
    assert studio._subtitle_time(59.9999) == "00:01:00,000"


def test_primary_video_deliverable_is_not_reclassified_by_packaged_article():
    brief = (
        "Create a professional 60-second AI Infinity launch video in English, 16:9, "
        "cinematic, research-backed, with voiceover, background music, captions, "
        "thumbnail, article, SEO metadata, social campaign package and production/rights manifests."
    )
    intent = canonical.compile_intent(
        brief, {"duration": 60, "format": "long", "aspect_ratio": "16:9"}
    )
    assert intent["constraints"]["content_type"] == "video"
    assert intent["constraints"]["format"] == "long"
    assert intent["constraints"]["aspect_ratio"] == "16:9"
    assert "final.mp4" in intent["success"]["required_artifacts"]


def test_article_remains_primary_when_video_is_only_a_secondary_reference():
    brief = "Write a research-backed article about energy storage that includes a short video reference and social assets."
    intent = canonical.compile_intent(brief, {})
    assert intent["constraints"]["content_type"] == "article"
    assert "article.md" in intent["success"]["required_artifacts"]



def test_ai_infinity_launch_plan_searches_real_creator_workflow_scenes(monkeypatch):
    studio = closure._studio()
    monkeypatch.setattr(studio, "FAST_MODE", True)
    brief = (
        "Create a professional 60-second AI Infinity launch video with voiceover, "
        "captions, thumbnail, article and social campaign package."
    )
    plan = studio._fallback_creative_plan(
        brief, brief, "long", 60, "general audience",
        "cinematic, intelligent, useful", {"sources": []}, "English",
    )
    chapters = plan["chapters"]
    assert len(chapters) == 8
    assert all(
        chapter["visual_search_subject"] == "content creator"
        for chapter in chapters
    )
    assert len({chapter["visual_focus"] for chapter in chapters}) == len(chapters)
    assert any("editing timeline" in chapter["visual_query"] for chapter in chapters)
    assert any("camera" in chapter["visual_query"].lower() for chapter in chapters)
    assert any("production" in chapter["visual_query"].lower() for chapter in chapters)



def test_explicit_long_form_selection_survives_short_video_collateral():
    studio = closure._studio()
    text = "Create a long-form 16:9 film and include three Shorts as secondary campaign assets."
    assert studio._resolve_format_intent("long", text, 60, explicit=True) == "long"
    assert studio._resolve_format_intent("long", text, 60, explicit=False) == "short"


def test_explicit_short_form_selection_is_not_silently_rewritten():
    studio = closure._studio()
    text = "Create a portrait launch reel."
    assert studio._resolve_format_intent("short", text, 300, explicit=True) == "short"



def test_fast_mode_scene_budget_keeps_professional_coverage_in_all_profiles():
    studio = closure._studio()
    assert studio._max_scene_count(True, True, False, False) == 5
    assert studio._max_scene_count(False, True, False, False) == 8
    assert studio._max_scene_count(True, False, False, False) == 5
    assert studio._max_scene_count(True, True, False, True) == 1
    assert studio._max_scene_count(True, True, True, False) == 1



def test_ci_recovery_fixture_is_cc0_labelled_and_never_used_without_exact_marker(monkeypatch, tmp_path):
    studio = closure._studio()
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("AI_INFINITY_TEST_MEDIA", "1")
    assert studio._ci_test_visual({"heading": "Recovery scene"}, tmp_path, 1) is None

    asset = studio._ci_test_visual({
        "heading": "Recovery scene",
        "_acceptance_fixture_id": "cc0-recovery-illustration-v1",
    }, tmp_path, 1)
    assert asset is not None
    assert asset["fallback"] is True
    assert asset["quality_tier"] == "original_vector_editorial_fallback"
    assert asset["asset_kind"] == "illustrative_graphic_not_photograph"
    assert asset["rights_status"] == "original_asset"
    assert "CC0 1.0" in asset["license"]
    assert asset["license_url"] == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert closure._is_qualified_illustrative_fallback(asset)
    path = Path(asset["path"])
    assert path.is_file() and path.stat().st_size > 1000
    with studio.Image.open(path) as im:
        assert im.format == "PNG" and im.size == (1280, 720)


def test_ci_recovery_fixture_is_disabled_outside_ci_even_with_allowlisted_id(monkeypatch, tmp_path):
    studio = closure._studio()
    monkeypatch.setenv("CI", "false")
    monkeypatch.setenv("AI_INFINITY_TEST_MEDIA", "1")
    assert studio._ci_test_visual({
        "heading": "This must not be generated",
        "_acceptance_fixture_id": "cc0-recovery-illustration-v1",
    }, tmp_path, 1) is None



def test_rights_gate_requires_source_specific_license_and_commercial_use_evidence():
    approved = {
        "source": "Openverse", "source_id": "asset-123",
        "source_url": "https://openverse.org/image/asset-123", "title": "Forest landscape",
        "creator": "Example Creator", "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "rights_status": "license_metadata_present",
        "commercial_use_state": "commercial_use_allowed_with_attribution",
    }
    assert closure._rights_evidence_state(approved) == "cleared"


def test_inherited_source_reuse_without_exact_provenance_is_review_required():
    inherited = {
        "source": "verified-source-reuse", "source_url": None, "title": None,
        "creator": None, "license": "inherited from original source",
        "rights_status": "inherited_source_evidence",
        "commercial_use_state": "UNKNOWN_REVIEW_REQUIRED",
    }
    assert closure._rights_evidence_state(inherited) == "review_required"


def test_rights_gate_rejects_missing_license_url_even_with_a_license_label():
    incomplete = {
        "source": "Wikimedia Commons",
        "source_url": "https://commons.wikimedia.org/wiki/File:example.jpg",
        "title": "Example image", "creator": "Example Creator", "license": "CC BY 4.0",
        "rights_status": "license_metadata_present",
        "commercial_use_state": "commercial_use_allowed_with_attribution",
    }
    assert closure._rights_evidence_state(incomplete) == "review_required"


def test_rights_gate_rejects_noncommercial_and_no_derivatives_licenses():
    for license_name, license_url in (
        ("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/"),
        ("CC BY-ND 4.0", "https://creativecommons.org/licenses/by-nd/4.0/"),
    ):
        row = {
            "source": "Openverse", "source_url": "https://openverse.org/image/asset-123",
            "title": "Example image", "creator": "Example Creator", "license": license_name,
            "license_url": license_url, "rights_status": "license_metadata_present",
            "commercial_use_state": "commercial_use_allowed_with_attribution",
        }
        assert closure._rights_evidence_state(row) == "review_required"


def test_rights_gate_rejects_provider_policy_labels_without_exact_asset_metadata():
    row = {
        "source": "Pexels", "source_url": "https://www.pexels.com/",
        "title": "Pexels video", "creator": "Example Creator", "license": "Pexels license",
        "license_url": "https://www.pexels.com/license/",
        "rights_status": "provider_license_metadata_present",
        "commercial_use_state": "commercial_use_allowed_under_provider_terms",
    }
    assert closure._rights_evidence_state(row) == "review_required"


def test_fallback_visual_can_never_pass_the_source_rights_gate():
    fixture = {
        "source": "AI Infinity deterministic CC0 recovery-test illustration",
        "source_url": None, "title": "Recovery illustration", "creator": "AI Infinity",
        "license": "CC0 1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "rights_status": "original_asset", "commercial_use_state": "public_domain_cc0_1_0", "fallback": True,
    }
    assert closure._rights_evidence_state(fixture) == "blocked_fallback"


def test_qualified_vector_fallback_has_separate_original_provenance_not_source_clearance():
    original = {
        "source": "AI Infinity original vector editorial fallback",
        "source_url": None,
        "creator": "AI Infinity",
        "license": "Original generated illustrative asset",
        "license_url": None,
        "rights_status": "original_asset",
        "rights_basis": "application_generated_original",
        "commercial_use_state": "owner_attested_original_work",
        "rights_evidence_method": "in_app_procedural_vector_renderer",
        "rights_evidence_note": "Original vector artwork; no provider-sourced media is incorporated into this image.",
        "rights_evidence_version": "3624.1",
        "quality_tier": "original_vector_editorial_fallback",
        "asset_kind": "illustrative_graphic_not_photograph",
        "fallback": True,
        "fallback_record": {
            "truthful": True,
            "execution_state": "DEGRADED",
            "fallback_method": "Topic-led original vector/editorial illustration",
            "quality_change": "Real-source visuals were unavailable; original illustration used",
            "license_change": "provider/source-specific -> original generated illustrative asset",
        },
    }
    # This is recorded as the application's own artwork, never as externally
    # licensed source media. The independent source-visual gate remains false.
    assert closure._rights_evidence_state(original) == "original_generated"
    assert closure._rights_evidence_state({
        **original, "rights_evidence_method": "unverified"
    }) == "blocked_fallback"
    assert closure._rights_evidence_state({
        **original, "source_url": "https://example.test/external-image.jpg"
    }) == "blocked_fallback"


def test_license_classifier_does_not_treat_cc_by_nc_or_nd_as_cleared():
    assert studio._commercial_use_from_license(
        "CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/"
    ) == "not_permitted_for_commercial_use"
    assert studio._commercial_use_from_license(
        "CC BY-ND 4.0", "https://creativecommons.org/licenses/by-nd/4.0/"
    ) == "not_cleared_for_derivative_editing"


def test_license_classifier_requires_specific_known_license_family():
    assert studio._commercial_use_from_license(
        "CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"
    ) == "commercial_use_allowed_with_attribution"
    assert studio._commercial_use_from_license("Unknown provider label", "") == "UNKNOWN_REVIEW_REQUIRED"



def test_visual_candidate_relevance_requires_scene_specific_metadata():
    relevant = {
        "title": "Video editor working at a computer workstation",
        "description": "A filmmaker edits clips and a timeline on a large monitor.",
        "tags": ["editing", "filmmaker", "workstation"],
    }
    unrelated = {
        "title": "Portrait of a creator outdoors in a garden",
        "description": "A person enjoying a sunny day.",
        "tags": ["portrait", "garden", "lifestyle"],
    }
    placeholder_only = {"title": "Pexels video 12345"}
    assert studio._visual_candidate_relevant(
        relevant, "content creator", "creator working at a video editing workstation"
    )
    assert not studio._visual_candidate_relevant(
        unrelated, "content creator", "creator working at a video editing workstation"
    )
    assert not studio._visual_candidate_relevant(
        placeholder_only, "content creator", "creator working at a video editing workstation"
    )
    assert not studio._visual_candidate_relevant(
        {"provider_query": "video editor workstation"}, "content creator", "creator working at a video editing workstation"
    )


def test_publish_quality_gate_blocks_fallback_or_unreviewed_factual_claims():
    assert not closure._publish_quality_gate_passed(
        False, {"visual_rights_evidence_present": False, "source_visual_policy_passed": False},
    )
    assert not closure._publish_quality_gate_passed(
        True, {"factual_review_required": True, "factual_review_auto_publish_safe": False},
    )
    assert closure._publish_quality_gate_passed(
        True, {"factual_review_required": False, "visual_rights_evidence_present": True},
    )



def test_openverse_search_requests_commercial_modification_compatible_licenses(monkeypatch, tmp_path):
    captured = {}
    def fake_http_json(url, *args, **kwargs):
        captured["url"] = url
        return {"results": []}
    assert studio._openverse_search_params("video editor workstation", 4) == {
        "q": "video editor workstation",
        "page_size": 4,
        "mature": "false",
        "license": "cc0,by",
        "license_type": "commercial,modification",
    }


def test_publish_quality_gate_is_required_even_when_legacy_toggle_is_disabled(monkeypatch):
    monkeypatch.setenv("AI_INFINITY_REQUIRE_VERIFIED_PUBLISH", "0")
    assert not closure._publish_quality_gate_passed(
        False, {"visual_rights_evidence_present": False}
    )
    assert not closure._publish_quality_gate_passed(
        True, {"factual_review_required": True, "factual_review_auto_publish_safe": False}
    )
    assert closure._publish_quality_gate_passed(
        True, {"factual_review_required": False, "visual_rights_evidence_present": True}
    )



def test_visual_acquisition_diagnostics_preserve_provider_failure_reasons_and_redact_secrets():
    rows = [{
        "path": "/tmp/project/procedural_visual_01.png",
        "metadata": {
            "fallback": True,
            "fallback_reason": "No relevant public visual was acquired.",
            "fallback_record": {
                "failed_providers": ["openverse", "wikimedia"],
                "failure_reasons": [
                    {"provider": "openverse", "reason": "HTTPError: 429"},
                    {"provider": "wikimedia", "reason": "url=https://example.org/api?token=secret"},
                ],
            },
        },
    }]
    result = closure._visual_acquisition_diagnostics(rows)
    assert result[0]["asset"] == "procedural_visual_01.png"
    assert result[0]["failed_providers"] == ["openverse", "wikimedia"]
    assert "429" in result[0]["failure_reasons"][0]["reason"]
    assert "secret" not in result[0]["failure_reasons"][1]["reason"]
    assert "[REDACTED]" in result[0]["failure_reasons"][1]["reason"]
    assert closure._visual_acquisition_diagnostics([{"path": "real.jpg", "metadata": {"source": "Openverse"}}]) == []



def test_diagnostic_redaction_masks_bare_sensitive_fields_not_just_urls():
    result = closure._visual_acquisition_diagnostics([{
        "path": "fallback.png",
        "metadata": {
            "fallback": True,
            "fallback_record": {
                "failure_reasons": [
                    {"provider": "openverse", "reason": "token: top-secret"},
                    {"provider": "pexels", "reason": "Authorization=Bearer hidden-value"},
                    {"provider": "commons", "reason": "https://example.org/api?signature=private"},
                ]
            },
        },
    }])
    joined = " ".join(x["reason"] for x in result[0]["failure_reasons"])
    assert "top-secret" not in joined
    assert "hidden-value" not in joined
    assert "private" not in joined
    assert joined.count("[REDACTED]") >= 3

def test_product_brand_words_do_not_poison_scene_visual_relevance():
    candidate = {
        "title": "Content creator editing video at a computer workstation",
        "description": "A filmmaker uses an editing timeline and large monitor.",
        "tags": ["creator", "editing", "workstation"],
    }
    assert studio._visual_candidate_relevant(
        candidate, "AI Infinity creator", "creator"
    )
    assert not studio._visual_candidate_relevant(
        {"title": "Landscape at a mountain lake", "tags": ["nature", "mountain"]},
        "AI Infinity creator", "creator",
    )


def test_visual_search_queries_prioritize_scene_focus_and_remove_brand_noise():
    queries = studio._visual_search_query_variants(
        search_subject="AI Infinity creator",
        focus="Creator working at a video editing workstation",
        topic="AI Infinity launch video",
        raw_query="AI Infinity launch",
    )
    assert queries
    assert "infinity" not in " ".join(queries).lower()
    assert "editing" in queries[0].lower()
    assert "workstation" in queries[0].lower()
    assert any(
        {"editor", "computer", "workstation"}.issubset(set(query.lower().split()))
        for query in queries
    )

def test_persisted_project_status_tracks_truth_gate():
    import threading

    class Connection:
        def __init__(self):
            self.statements = []
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def execute(self, statement, params):
            self.statements.append((statement, params))
            return self

    class Studio:
        def __init__(self):
            self.DB_LOCK = threading.Lock()
            self.connection = Connection()
        def _connect(self):
            return self.connection

    studio_db = Studio()
    project = {"project_id": "project-review", "status": "completed", "stage": "completed"}
    closure._persist_project_truth_status(studio_db, project, verified=False)
    assert project["status"] == "completed_with_qc_warnings"
    assert project["stage"] == "quality_review_required"
    sql, params = studio_db.connection.statements[-1]
    assert "UPDATE studio_projects_3610 SET status=?,stage=?,updated_at=?" in sql
    assert params[0:2] == ("completed_with_qc_warnings", "quality_review_required")
    assert params[3] == "project-review"

    # Promotion is permitted only when the independent truth gate passes.
    studio_db = Studio()
    project = {"project_id": "project-recovered", "status": "completed_with_qc_warnings", "stage": "quality_review_required"}
    closure._persist_project_truth_status(studio_db, project, verified=True)
    assert project["status"] == "completed"
    assert project["stage"] == "completed"
    sql, params = studio_db.connection.statements[-1]
    assert params[0:2] == ("completed", "completed")
    assert params[3] == "project-recovered"

def test_earth_from_space_search_prioritizes_subject_and_rejects_incidental_word_overlap():
    queries = studio._visual_search_query_variants(
        search_subject="earth space",
        focus="the clearest subject that communicates the idea of earth",
        topic="Earth from space",
        raw_query="earth space clearest that earth documentary photography",
    )
    assert queries == [
        "Earth from space",
        "Earth globe seen from orbit satellite image",
        "Blue Marble Earth satellite view",
    ]

    # The long description can mention "Earth" and "space" without describing
    # an Earth-from-space image. Candidate identity metadata must carry both.
    assert studio._visual_candidate_relevant(
        {
            "title": "Earth from Space — Blue Marble view of the planet",
            "description": "A high resolution view of Earth against space.",
            "tags": ["Earth", "space", "globe"],
        },
        "earth space",
        "the clearest subject that communicates the idea of earth",
    )
    assert not studio._visual_candidate_relevant(
        {
            "title": "A Hubble Study of the Peculiar Asymmetry of NGC 949",
            "description": "From our point of view, the galaxy is seen through dust in space. This gives Earth's light a reddish hue at dusk.",
        },
        "earth space",
        "the clearest subject that communicates the idea of earth",
    )
    assert not studio._visual_candidate_relevant(
        {
            "title": "Spacelab",
            "description": "Astronauts performed this experiment in space and returned to Earth.",
        },
        "earth space",
        "why earth space affected",
    )
    assert not studio._visual_candidate_relevant(
        {
            "title": "NASA-Produced Maps Help Gauge Italy Earthquake Damage",
            "description": "Satellite imagery of earthquake damage on Earth's surface.",
        },
        "earth space",
        "demonstrating earth space action",
    )


def test_nasa_item_rights_are_attributed_and_exceptions_remain_review_required():
    allowed = studio._nasa_rights_evidence({
        "title": "Earth from Space — Blue Marble",
        "description": "A view of the planet Earth and its oceans from orbit. Credit: NASA.",
        "creator": "NASA",
        "center": "NASA",
    })
    assert allowed["license_url"] == "https://www.nasa.gov/nasa-brand-center/images-and-media/"
    assert allowed["commercial_use_state"] == "commercial_use_allowed_under_provider_terms"
    assert allowed["rights_status"] == "license_metadata_present"
    assert "NASA" in allowed["attribution"]
    assert "do not imply endorsement" in allowed["rights_status_reason"]

    third_party = studio._nasa_rights_evidence({
        "title": "A Hubble view of Earth and nearby stars",
        "description": "Credit: ESA/Hubble & NASA. Copyright holder terms apply.",
        "creator": "ESA/Hubble & NASA",
    })
    assert third_party["commercial_use_state"] == "UNKNOWN_REVIEW_REQUIRED"
    assert third_party["rights_status"] == "REVIEW_REQUIRED"
    assert "copyright" in third_party["rights_status_reason"].lower()

    people = studio._nasa_rights_evidence({
        "title": "Astronaut portrait from the International Space Station",
        "description": "NASA media use policy applies.",
        "creator": "NASA",
    })
    assert people["commercial_use_state"] == "UNKNOWN_REVIEW_REQUIRED"
    assert people["rights_status"] == "REVIEW_REQUIRED"

    # Third-party creator attribution must be screened even when the description
    # omits the literal word "credit".
    third_party_creator = studio._nasa_rights_evidence({
        "title": "Earth imagery from orbit",
        "description": "The Earth and nearby stars viewed from space.",
        "creator": "ESA/Hubble & NASA",
    })
    assert third_party_creator["commercial_use_state"] == "UNKNOWN_REVIEW_REQUIRED"
    assert third_party_creator["rights_status"] == "REVIEW_REQUIRED"
    assert "third-party creator" in third_party_creator["rights_status_reason"].lower()

    explicit_rights_field = studio._nasa_rights_evidence({
        "title": "Earth from orbit",
        "description": "Earth viewed from the International Space Station.",
        "creator": "NASA",
        "copyright_status": "third-party copyright; permission required",
    })
    assert explicit_rights_field["commercial_use_state"] == "UNKNOWN_REVIEW_REQUIRED"
    assert explicit_rights_field["rights_status"] == "REVIEW_REQUIRED"


def test_full_production_context_keeps_brand_launch_nasa_use_review_required():
    assert studio._scene_is_explicitly_promotional({
        "heading": "A world connected by possibility",
        "visual_query": "Earth from space documentary photo",
        "_production_context": {
            "title": "AI Infinity launch video",
            "content_type": "video",
            "format": "short",
        },
    })


def test_nasa_policy_only_auto_clears_editorial_context_not_explicit_brand_promotion():
    base = {
        "source": "NASA Image and Video Library",
        **studio._nasa_rights_evidence({
            "title": "Earth from Space — Blue Marble",
            "description": "The planet Earth seen from orbit. Credit: NASA.",
            "creator": "NASA",
        }),
    }
    editorial = studio._apply_nasa_scene_usage_policy(
        base,
        {"topic": "Earth from space", "visual_query": "Earth from space documentary"},
    )
    assert editorial["commercial_use_state"] == "commercial_use_allowed_under_provider_terms"
    assert editorial["rights_status"] == "license_metadata_present"

    promotional = studio._apply_nasa_scene_usage_policy(
        base,
        {
            "title": "AI Infinity product launch video",
            "topic": "Earth from space",
            "objective": "Create an AI Infinity launch film for the product",
        },
    )
    assert promotional["commercial_use_state"] == "UNKNOWN_REVIEW_REQUIRED"
    assert promotional["rights_status"] == "REVIEW_REQUIRED"
    assert "promotional/advertising use" in promotional["rights_status_reason"]


def test_nasa_adapter_exports_asset_level_policy_url_and_rights_state(monkeypatch, tmp_path):
    monkeypatch.setattr(studio, "FAST_MODE", True)
    json_calls = []

    def fake_json(url, headers=None, timeout=20):
        json_calls.append(url)
        assert url.startswith("https://images-api.nasa.gov/search?")
        assert "Earth+from+space" in url or "Earth%20from%20space" in url
        return {
            "collection": {
                "items": [{
                    "href": "https://images-assets.nasa.gov/image/earth-001/collection.json",
                    "data": [{
                        "title": "Earth from Space — Blue Marble",
                        "description": "The planet Earth and its oceans seen from orbit. Credit: NASA.",
                        "creator": "NASA",
                        "center": "NASA",
                        "keywords": ["Earth", "space", "Blue Marble"],
                    }],
                    "links": [{
                        "rel": "preview",
                        "href": "https://images-assets.nasa.gov/image/earth-001/earth-001~thumb.jpg",
                    }],
                }]
            }
        }

    def fake_download(url, path, **kwargs):
        assert "earth-001" in url
        _write_test_jpeg(studio, Path(path), color=(25, 80, 140))
        return Path(path)

    monkeypatch.setattr(studio, "http_json", fake_json)
    monkeypatch.setattr(studio, "download", fake_download)
    rows = studio._nasa_images("Earth from space", tmp_path, limit=1)

    assert len(rows) == 1, {
        "media_debug_errors": dict(studio.MEDIA_DEBUG_ERRORS),
        "outdir_files": sorted(x.name for x in tmp_path.iterdir()),
        "json_calls": json_calls,
        "adapter_module": studio._nasa_images.__module__,
    }
    row = rows[0]
    assert row["source_url"] == "https://images-assets.nasa.gov/image/earth-001/collection.json"
    assert row["license_url"] == "https://www.nasa.gov/nasa-brand-center/images-and-media/"
    assert row["rights_status"] == "license_metadata_present"
    assert row["commercial_use_state"] == "commercial_use_allowed_under_provider_terms"
    assert row["tags"] == ["Earth", "space", "Blue Marble"]
    assert studio._visual_candidate_relevant(row, "earth space", "clearest view of Earth from space")


def test_transient_visual_provider_failure_is_cooled_down_but_irrelevant_results_are_not():
    with studio._MEDIA_PROVIDER_COOLDOWN_LOCK:
        studio._MEDIA_PROVIDER_COOLDOWNS.clear()
    try:
        assert studio._trip_media_provider_cooldown(
            "_openverse_images", "TimeoutError: The read operation timed out"
        ) is True
        reason = studio._media_provider_cooldown_reason("_openverse_images")
        assert reason and "TimeoutError" in reason

        # A provider returning an irrelevant candidate is not a network failure;
        # it must remain eligible for the next scene-specific query.
        assert studio._media_provider_failure_is_circuit_worthy(
            "HTTP 429 Too Many Requests"
        ) is True
        assert studio._media_provider_failure_is_circuit_worthy(
            "no-relevant-distinct-asset"
        ) is False

        with studio._MEDIA_PROVIDER_COOLDOWN_LOCK:
            studio._MEDIA_PROVIDER_COOLDOWNS["_openverse_images"]["until_monotonic"] = 0
        assert studio._media_provider_cooldown_reason("_openverse_images") is None
    finally:
        with studio._MEDIA_PROVIDER_COOLDOWN_LOCK:
            studio._MEDIA_PROVIDER_COOLDOWNS.clear()


def test_focus_lexical_overlap_can_resolve_unclassified_scene_concepts():
    relevant = {
        "title": "Software workflow dashboard on a computer screen",
        "description": "A connected software workflow interface for creative teams.",
        "tags": ["software", "workflow", "dashboard"],
    }
    unrelated = {
        "title": "Mountain lake landscape photography",
        "description": "A scenic outdoor view.",
        "tags": ["mountain", "nature", "landscape"],
    }
    assert studio._visual_candidate_relevant(
        relevant, "AI Infinity launch", "software workflow dashboard"
    )
    assert not studio._visual_candidate_relevant(
        unrelated, "AI Infinity launch", "software workflow dashboard"
    )

def test_editing_workflow_queries_prefer_captions_and_waveform_specificity():
    queries = studio._visual_search_query_variants(
        search_subject="content creator",
        focus="hands editing video clips audio waveforms and caption tracks on a workstation",
        topic="AI Infinity launch",
        raw_query="AI Infinity launch video",
    )
    assert len(queries) == 2
    assert "infinity" not in " ".join(queries).lower()
    assert "waveforms" in queries[0].lower()
    assert "captions" in queries[1].lower()
    assert "timeline" in queries[1].lower()
    assert "monitor" in queries[1].lower()



def test_raw_final_scene_has_a_specific_editing_search_synonym():
    queries = studio._visual_search_query_variants(
        search_subject="content creator",
        focus="before-and-after comparison of raw footage and a finished edited film on screens",
        topic="AI Infinity launch",
        raw_query="content creator before-and-after comparison of raw footage and a finished edited film on screens documentary photography",
    )
    assert len(queries) == 3, queries
    assert queries[1] == "video editing before after raw footage finished cut", queries
    assert queries[2] == "video editor reviewing completed edit preview monitor footage playback export", queries



def test_launch_outcome_scene_targets_retrievable_editing_media():
    plan = studio._fallback_creative_plan(
        "AI Infinity launch",
        "Create a professional 60-second AI Infinity launch video in English, research-backed.",
        "long",
        60,
        "general audience",
        "clear, intelligent, human",
        {"sources": [], "source_count": 0},
        "English",
        "AI Infinity launch",
    )
    outcome = next(
        scene for scene in plan["chapters"]
        if str(scene.get("heading", "")).lower().startswith("what changes")
    )
    assert "workstation" in outcome["visual_focus"].lower()
    assert "final cut" in outcome["visual_focus"].lower()


def test_later_openverse_scenes_request_a_broader_free_candidate_pool():
    assert studio._scene_asset_search_limit(2, "_openverse_images") == 4
    assert studio._scene_asset_search_limit(3, "_openverse_images") == 8
    assert studio._scene_asset_search_limit(4, "_openverse_images") == 8
    assert studio._scene_asset_search_limit(4, "_commons_media") == 4



def test_curated_pexels_sources_cover_four_distinct_creator_workflow_scenes(monkeypatch, tmp_path):
    scenes = {
        1: ("Hook", "content creator working at a video editing workstation", 14541040, "Alan Quirván"),
        2: ("Why it matters", "creator recording a short video in a real studio with camera and microphone", 17196831, "Angel Moranchel"),
        3: ("Practical example", "creator editing a vertical video timeline at a workstation, with camera microphone and lights nearby", 8102676, "Ron Lach"),
        4: ("Takeaway", "finished edited video playing on a large studio display", 695730, "Francesco Paggiaro"),
    }
    expected_pages = {
        1: "https://www.pexels.com/photo/back-view-of-a-man-using-laptop-14541040/",
        2: "https://www.pexels.com/photo/young-brunette-man-fixing-a-lavalier-microphone-to-his-shirt-17196831/",
        3: "https://www.pexels.com/photo/man-sitting-at-the-desk-and-editing-footage-on-a-computer-8102676/",
        4: "https://www.pexels.com/photo/computer-monitor-turned-on-695730/",
    }

    def fake_download(url, path, **kwargs):
        from urllib.parse import urlparse
        parsed = urlparse(url)
        assert parsed.scheme == "https" and parsed.netloc == "images.pexels.com"
        source_id = next((value for value in (14541040, 17196831, 8102676, 695730) if str(value) in url), None)
        assert source_id in {row[2] for row in scenes.values()}, "Unexpected curated Pexels source: " + url
        color = {
            14541040: (18, 35, 60),
            17196831: (110, 55, 95),
            8102676: (70, 30, 15),
            695730: (25, 115, 70),
        }[source_id]
        studio.Image.new("RGB", (1280, 720), color).save(path, "JPEG")
        return path

    monkeypatch.setattr(studio, "download", fake_download)
    results = []
    for index, (heading, focus, source_id, creator) in scenes.items():
        scene = {"heading": heading, "visual_focus": focus}
        candidate = studio._curated_pexels_editorial_photo(scene, tmp_path, index)
        assert candidate is not None, (index, scene)
        assert studio._visual_candidate_relevant(candidate, "content creator", focus), candidate
        assert candidate["source_id"] == source_id
        assert candidate["creator"] == creator
        assert candidate["source_url"] == expected_pages[index]
        assert candidate["license_url"] == "https://www.pexels.com/license/"
        assert candidate["commercial_use_state"] == "commercial_use_allowed_under_provider_terms"
        assert candidate["rights_status"] == "license_metadata_present"
        assert candidate["attribution"] == f"Photo by {creator} / Pexels"
        results.append(candidate)

    assert len({studio.file_sha256(Path(row["path"])) for row in results}) == 4


def test_uploaded_source_topic_inference_uses_extracted_text_and_preserves_explicit_topics():
    request = {
        "objective": (
            "Turn the uploaded PDF into research-backed content: produce a podcast package, "
            "video, article and social campaign with evidence and captions."
        ),
        "title": "Turn the uploaded PDF into",
        "source_file_names": ["creator-brief.pdf"],
    }
    uploaded_sources = [{
        "source": "user upload",
        "title": "creator-brief.pdf",
        "text_extracted": True,
        "snippet": (
            "Title: AI Infinity Creator Operating System\n"
            "Brief: AI Infinity connects research, writing, visual selection and editing.\n"
            "Delivery: Render narration, SRT captions, video, article, audio and social copy."
        ),
    }]
    topic = studio._infer_uploaded_source_topic(request, uploaded_sources)
    assert topic == "AI Infinity Creator Operating System", topic
    research_request = {**request, "topic": topic, "title": topic}
    assert studio._research_search_topic(research_request, topic) == (
        "video editing workflow content creation captions subtitles accessibility creator software production"
    )

    explicit = {**request, "topic": "Renewable energy economics"}
    assert studio._infer_uploaded_source_topic(explicit, uploaded_sources) == ""


def test_raw_final_scene_gets_three_bounded_specific_search_queries():
    queries = studio._visual_search_query_variants(
        search_subject="content creator",
        focus="before-and-after comparison of raw footage and a finished edited film on screens",
        topic="AI Infinity launch",
        raw_query="content creator before-and-after comparison of raw footage and a finished edited film on screens documentary photography",
    )
    assert len(queries) == 3, queries
    assert queries[1] == "video editing before after raw footage finished cut", queries
    assert queries[2] == "video editor reviewing completed edit preview monitor footage playback export", queries



def test_visual_prompt_parsing_recovers_subject_and_scene_focus():
    topic, focus = studio._parse_scene_visual_intent(
        {},
        (
            "Premium editorial documentary image about AI Infinity launch; "
            "visual focus: before-and-after comparison of raw footage and a finished "
            "edited film on screens; realistic cinematic lighting"
        ),
        "content creator before-and-after comparison of raw footage",
        "What comes next",
    )
    assert topic == "AI Infinity launch", topic
    assert focus == "before-and-after comparison of raw footage and a finished edited film on screens", focus



def test_visual_concept_patterns_cover_real_editorial_metadata_variants():
    patterns = studio._VISUAL_CONCEPTS
    assert __import__("re").search(patterns["editing"], "Edit Suite 2 - edited footage")
    assert __import__("re").search(patterns["workstation"], "editing monitors and screens")
    assert __import__("re").search(patterns["captions"], "subtitle tracks and captions")
    assert __import__("re").search(patterns["finished_video"], "finished edited film playback monitor")


def test_raw_final_scene_uses_specific_three_queries_before_generic_terms():
    queries = studio._visual_search_query_variants(
        search_subject="content creator",
        focus="before-and-after comparison of raw footage and a finished edited film on screens",
        topic="AI Infinity launch",
        raw_query="content creator before-and-after comparison of raw footage and a finished edited film on screens documentary photography",
    )
    assert len(queries) == 3, queries
    assert queries[1] == "video editing before after raw footage finished cut", queries
    assert queries[2] == "video editor reviewing completed edit preview monitor footage playback export", queries



def test_fast_product_launch_plan_uses_eight_distinct_story_beats(monkeypatch):
    monkeypatch.setattr(studio, "FAST_MODE", True)
    plan = studio._fallback_creative_plan(
        title="Create a professional 60-second AI Infinity launch video in English",
        objective=(
            "Create a professional 60-second AI Infinity launch video in English, "
            "16:9, research-backed, with voiceover, captions, and production manifests."
        ),
        fmt="long",
        duration=60,
        audience="general audience",
        tone="clear and practical",
        research={"sources": [], "source_count": 0},
        language="English",
        topic="Create a professional 60-second AI Infinity launch video in English",
    )
    chapters = plan["chapters"]
    assert len(chapters) == 8
    assert [x["heading"].split("—")[0].split(":")[0].strip() for x in chapters] == [
        "Opening question", "Context", "Core principle", "How it works",
        "Real examples", "What changes", "Next step", "Closing",
    ]
    assert sum(float(x["duration"]) for x in chapters) == 60
    script = " ".join(x["narration"] for x in chapters)
    assert len(script.split()) >= 80
    assert len({x["visual_focus"] for x in chapters}) == 8
    assert all(x["narration"].rstrip().endswith((".", "!", "?")) for x in chapters)
    assert all("production/rights manifests" not in x["visual_query"].lower() for x in chapters)




def test_uploaded_pdf_narration_points_are_extracted_from_user_content_only():
    research = {
        "sources": [
            {
                "source": "user upload",
                "title": "acceptance-source.pdf",
                "text_extracted": True,
                "snippet": (
                    "Title: AI Infinity Creator Operating System\n"
                    "Brief: AI Infinity connects research writing visual selection and editing.\n"
                    "Source rights: Record creator item page license terms and file SHA-256.\n"
                    "Delivery: Render narration captions video article audio and social copy.\n"
                    "Quality: Verify duration aspect ratio media decode and audio.\n"
                    "Recovery: Reload saved projects and confirm artifact checksums after restart.\n"
                ),
                "summary": "",
            },
            # A web result is not the user's document and must not become narration.
            {"source": "web", "title": "Unrelated article", "snippet": "unrelated advertisement language", "text_extracted": True},
        ]
    }
    points = studio._uploaded_source_narration_points(research)
    assert len(points) == 5, points
    joined = " ".join(points).lower()
    assert "ai infinity connects research, writing, visual selection, and editing" in joined
    assert "license terms" in joined and "file sha-256" in joined
    assert "captions" in joined and "social copy" in joined
    assert "artifact checksums" in joined
    assert "unrelated advertisement" not in joined
    assert all(re.search(r"[.!?…۔؟]$", point) for point in points)


def test_document_grounded_fallback_does_not_replace_uploaded_points_with_launch_boilerplate():
    source_text = (
        "Title: AI Infinity Creator Operating System\n"
        "Brief: AI Infinity connects research writing visual selection and editing.\n"
        "Source rights: Record creator item page license terms and file SHA-256.\n"
        "Delivery: Render narration captions video article audio and social copy.\n"
        "Quality: Verify duration aspect ratio media decode and audio.\n"
        "Recovery: Reload saved projects and confirm artifact checksums after restart.\n"
    )
    plan = studio._fallback_creative_plan(
        title="AI Infinity Creator Operating System",
        objective="Turn the uploaded PDF into research-backed content: produce a podcast package, video, article and social campaign with evidence, captions and a final production package.",
        fmt="short",
        duration=20,
        audience="general audience",
        tone="clear, practical, human",
        research={"sources":[{
            "source":"user upload",
            "title":"acceptance-source.pdf",
            "snippet":source_text,
            "summary":source_text[:1200],
            "text_extracted":True,
        }]},
        language="English",
        topic="AI Infinity Creator Operating System",
    )
    narration = " ".join(str(chapter.get("narration") or "") for chapter in plan["chapters"])
    assert plan["source_context"]["mode"] == "uploaded_document_grounded", plan["source_context"]
    assert plan["source_context"]["uploaded_source_point_count"] == 5, plan["source_context"]
    assert plan["source_context"]["uploaded_source_points_used"] == 5, plan["source_context"]
    assert "AI Infinity connects research, writing, visual selection, and editing" in narration
    assert "captions" in narration.lower() and "editing" in narration.lower()
    assert "A playable MP4 alone does not prove quality" not in narration
    assert "Every strong video starts with a clear point of view" not in narration



def test_uploaded_source_parser_handles_pdf_extractors_that_concatenate_heading_boundaries():
    # Some PDF extractors return "Operating SystemBrief:" with no whitespace.
    # The title field must not consume and discard the source's first substantive section.
    extracted = (
        "Title: AI Infinity Creator Operating System"
        "Brief: AI Infinity connects research writing visual selection and editing."
        "Source rights: Record creator item page license terms and file SHA-256."
        "Delivery: Render narration captions video article audio and social copy."
        "Quality: Verify duration aspect ratio media decode and audio."
        "Recovery: Reload saved projects and confirm artifact checksums after restart."
    )
    points = studio._uploaded_source_narration_points({"sources":[{
        "source":"user upload","title":"acceptance-source.pdf",
        "snippet":extracted,"text_extracted":True,
    }]})
    assert len(points) == 5, points
    assert points[0] == "AI Infinity connects research, writing, visual selection, and editing.", points
    assert any("captions" in point.lower() and "social copy" in point.lower() for point in points)



def test_creator_operating_system_topic_is_not_misclassified_as_launch_film():
    assert studio._is_explicit_ai_infinity_launch_brief("AI Infinity Creator Operating System") is False
    assert studio._is_explicit_ai_infinity_launch_brief("Create an AI Infinity launch video") is True
    assert studio._is_explicit_ai_infinity_launch_brief("AI Infinity platform tutorials and creator tools") is False


def _write_test_jpeg(studio, path: Path, color=(35, 95, 145)):
    """Write a real, decodable image so source adapter tests exercise normalization."""
    path.parent.mkdir(parents=True, exist_ok=True)
    studio.Image.new("RGB", (1280, 720), color).save(path, "JPEG")
    return path


def test_pexels_photo_adapter_preserves_asset_identity_and_license(monkeypatch, tmp_path):
    monkeypatch.setenv("PEXELS_API_KEY", "test-pexels-key")

    def fake_json(url, headers=None, timeout=20):
        assert url.startswith("https://api.pexels.com/v1/search?")
        assert headers == {"Authorization": "test-pexels-key"}
        return {
            "photos": [{
                "id": 695730,
                "url": "https://www.pexels.com/photo/computer-monitor-turned-on-695730/",
                "photographer": "Francesco Paggiaro",
                "alt": "computer monitor showing a video editing timeline",
                "width": 1280,
                "height": 720,
                "src": {"large2x": "https://images.pexels.com/photos/695730/example.jpeg"},
            }]
        }

    def fake_download(url, path, **kwargs):
        assert url == "https://images.pexels.com/photos/695730/example.jpeg"
        return _write_test_jpeg(studio, path)

    monkeypatch.setattr(studio, "http_json", fake_json)
    monkeypatch.setattr(studio, "download", fake_download)
    rows = studio._pexels_photos("video editing timeline", tmp_path, limit=1)

    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "image"
    assert row["source"] == "Pexels"
    assert row["source_id"] == 695730
    assert row["source_url"] == "https://www.pexels.com/photo/computer-monitor-turned-on-695730/"
    assert row["creator"] == "Francesco Paggiaro"
    assert row["license_url"] == "https://www.pexels.com/license/"
    assert row["rights_status"] == "license_metadata_present"
    assert row["commercial_use_state"] == "commercial_use_allowed_under_provider_terms"
    assert studio.Image.open(row["path"]).size == (1280, 720)


def test_curated_pexels_uses_relevant_monitor_photo_for_editing_timeline_and_preserves_studio_shot(monkeypatch, tmp_path):
    studio = closure._studio()
    monitor_url = "https://images.pexels.com/photos/24497392/pexels-photo-24497392.jpeg?cs=srgb&dl=pexels-alshreef-24497392.jpg&fm=jpg"
    microphone_url = "https://images.pexels.com/photos/17196831/pexels-photo-17196831.jpeg?cs=srgb&dl=pexels-angel-moranchel-212557293-17196831.jpg&fm=jpg"
    downloaded = []

    def fake_download(url, path, **kwargs):
        downloaded.append(url)
        return _write_test_jpeg(studio, path)

    monkeypatch.setattr(studio, "download", fake_download)

    timeline = studio._curated_pexels_editorial_photo({
        "heading": "The core idea",
        "visual_focus": "close-up computer monitor displaying a video editing timeline and subtitle tracks",
    }, tmp_path / "timeline", 2)
    assert timeline is not None
    assert timeline["source_id"] == 24497392
    assert timeline["source_url"] == "https://www.pexels.com/photo/a-computer-monitor-displaying-a-video-editing-program-24497392/"
    assert timeline["creator"] == "abdo alshreef"
    assert timeline["license_url"] == "https://www.pexels.com/license/"
    assert timeline["rights_status"] == "license_metadata_present"
    assert timeline["commercial_use_state"] == "commercial_use_allowed_under_provider_terms"
    assert studio.Image.open(timeline["path"]).size == (1280, 720)

    context = studio._curated_pexels_editorial_photo({
        "heading": "Context",
        "visual_focus": "wide view of a creative production studio with camera microphone and editing monitors",
    }, tmp_path / "context", 2)
    assert context is not None
    assert context["source_id"] == 17196831
    assert context["source_url"] == "https://www.pexels.com/photo/young-brunette-man-fixing-a-lavalier-microphone-to-his-shirt-17196831/"
    assert context["creator"] == "Angel Moranchel"
    assert studio.Image.open(context["path"]).size == (1280, 720)
    assert downloaded == [monitor_url, microphone_url]


def test_curated_launch_video_adapter_preserves_motion_source_and_item_rights(monkeypatch, tmp_path):
    studio_module = closure._studio()
    target = tmp_path / "pexels_launch_video_15517230.mp4"
    downloads = []

    def fake_download(url, path, **kwargs):
        downloads.append(url)
        Path(path).write_bytes(b"mocked-but-nonempty-mp4-bytes" * 2000)
        return Path(path)

    def fake_probe(path):
        return {
            "format": {"duration": "12.0"},
            "streams": [{
                "codec_type": "video", "codec_name": "h264",
                "width": 3840, "height": 2160, "avg_frame_rate": "60/1",
            }],
        }

    monkeypatch.setattr(studio_module, "download", fake_download)
    monkeypatch.setattr(studio_module, "ffprobe_json", fake_probe)
    monkeypatch.setattr(studio_module, "probe_duration", lambda path: 12.0)
    monkeypatch.setattr(studio_module, "FAST_MODE", True)
    # These tests mock media bytes and FFprobe. Keep the independent real-media
    # black-frame check disabled only for the synthetic fixture, not production.
    monkeypatch.setattr(studio_module, "_source_has_prolonged_black_frames", lambda *_args, **_kwargs: False)

    row = studio_module._curated_pexels_launch_video({
        "topic": "AI Infinity launch",
        "title": "AI Infinity launch",
        "heading": "Core principle — AI Infinity launch",
        "visual_focus": "close-up of an editing timeline moving across a computer screen",
        "visual_query": "content creator editing timeline video editing workstation",
    }, tmp_path, 3)

    assert row is not None
    assert row["kind"] == "video"
    assert row["source"] == "Pexels curated free editorial video"
    assert row["source_id"] == 15517230
    assert row["source_url"] == "https://www.pexels.com/video/a-computer-screen-showing-a-video-editing-screen-15517230/"
    assert row["creator"] == "Ludovic Delot"
    assert row["license"] == "Pexels License"
    assert row["license_url"] == "https://www.pexels.com/license/"
    assert row["commercial_use_state"] == "commercial_use_allowed_under_provider_terms"
    assert row["validated_media"] is True
    assert row["resolution"] == [3840, 2160]
    assert downloads == ["https://videos.pexels.com/video-files/15517230/15517230-uhd_3840_2160_60fps.mp4"]
    assert target.is_file()


def test_curated_launch_video_selects_by_scene_semantics_not_ordinal(monkeypatch, tmp_path):
    module = closure._studio()
    downloads = []

    def fake_download(url, path, **kwargs):
        downloads.append(url)
        Path(path).write_bytes(b"mocked valid motion source bytes " * 1400)
        return Path(path)

    def fake_probe(path):
        return {
            "format": {"duration": "12.0"},
            "streams": [{
                "codec_type": "video", "codec_name": "h264",
                "width": 1920, "height": 1080, "avg_frame_rate": "30/1",
            }],
        }

    monkeypatch.setattr(module, "download", fake_download)
    monkeypatch.setattr(module, "ffprobe_json", fake_probe)
    monkeypatch.setattr(module, "probe_duration", lambda path: 12.0)
    monkeypatch.setattr(module, "FAST_MODE", True)
    # These tests mock media bytes and FFprobe. Keep the independent real-media
    # black-frame check disabled only for the synthetic fixture, not production.
    monkeypatch.setattr(module, "_source_has_prolonged_black_frames", lambda *_args, **_kwargs: False)

    # Scene 5 of the five-scene short storyboard is final playback, not a
    # creator-filming shot. The old index-only mapping chose source 7677005.
    row = module._curated_pexels_launch_video({
        "topic": "AI Infinity launch",
        "title": "AI Infinity launch",
        "heading": "Takeaway",
        "visual_focus": "finished edited video playing on a large studio display",
        "visual_query": "content creator finished edited video studio display",
        "visual_search_subject": "content creator",
    }, tmp_path, 5)

    assert row is not None, module.MEDIA_DEBUG_ERRORS.get("pexels-curated-launch-video")
    assert row["kind"] == "video"
    assert row["quality_tier"] == "curated_free_source_motion_video"
    assert row["source_id"] in {12358562, 35820856, 7239491}
    assert row["source_id"] != 7677005
    assert row["selection_reason"] == "scene_concepts_and_source_metadata_match"
    assert row["source_url"].startswith("https://www.pexels.com/video/")
    assert row["license_url"] == "https://www.pexels.com/license/"
    assert row["validated_media"] is True
    assert row["resolution"] == [1920, 1080]
    assert len(downloads) == 1


def test_curated_launch_video_retries_another_relevant_clip_after_download_failure(monkeypatch, tmp_path):
    module = closure._studio()
    downloads = []

    def fake_download(url, path, **kwargs):
        downloads.append(url)
        if "/12358562/" in url:
            raise TimeoutError("simulated timeout for preferred matching source")
        Path(path).write_bytes(b"mocked valid motion source bytes " * 1400)
        return Path(path)

    def fake_probe(path):
        return {
            "format": {"duration": "12.0"},
            "streams": [{
                "codec_type": "video", "codec_name": "h264",
                "width": 1920, "height": 1080, "avg_frame_rate": "30/1",
            }],
        }

    monkeypatch.setattr(module, "download", fake_download)
    monkeypatch.setattr(module, "ffprobe_json", fake_probe)
    monkeypatch.setattr(module, "probe_duration", lambda path: 12.0)
    monkeypatch.setattr(module, "FAST_MODE", True)
    # These tests mock media bytes and FFprobe. Keep the independent real-media
    # black-frame check disabled only for the synthetic fixture, not production.
    monkeypatch.setattr(module, "_source_has_prolonged_black_frames", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        module,
        "_curated_launch_visual_candidate_relevant",
        lambda candidate, *_args, **_kwargs: candidate.get("title") in {
            "Close up of computer screen",
            "Professional video editing at workstation",
        },
    )

    row = module._curated_pexels_launch_video({
        "topic": "AI Infinity launch",
        "title": "AI Infinity launch",
        "heading": "Takeaway",
        "visual_focus": "finished edited video playing on a large studio display",
        "visual_query": "content creator finished edited video studio display",
        "visual_search_subject": "content creator",
    }, tmp_path, 5)

    assert row is not None, module.MEDIA_DEBUG_ERRORS.get("pexels-curated-launch-video")
    assert row["source_id"] in {35820856, 7239491}
    assert len(downloads) == 2, downloads
    assert downloads[0].endswith("/12358562-hd_1920_1080_30fps.mp4")
    assert downloads[1].endswith(("/15188259_1920_1080_30fps.mp4", "/7239491-hd_1920_1080_30fps.mp4"))


def test_curated_launch_video_keeps_creator_filming_clip_when_that_scene_is_requested(monkeypatch, tmp_path):
    module = closure._studio()

    def fake_download(url, path, **kwargs):
        Path(path).write_bytes(b"mocked valid motion source bytes " * 1400)
        return Path(path)

    def fake_probe(path):
        return {
            "format": {"duration": "12.0"},
            "streams": [{
                "codec_type": "video", "codec_name": "h264",
                "width": 1920, "height": 1080, "avg_frame_rate": "25/1",
            }],
        }

    monkeypatch.setattr(module, "download", fake_download)
    monkeypatch.setattr(module, "ffprobe_json", fake_probe)
    monkeypatch.setattr(module, "probe_duration", lambda path: 12.0)
    monkeypatch.setattr(module, "FAST_MODE", True)
    # These tests mock media bytes and FFprobe. Keep the independent real-media
    # black-frame check disabled only for the synthetic fixture, not production.
    monkeypatch.setattr(module, "_source_has_prolonged_black_frames", lambda *_args, **_kwargs: False)

    row = module._curated_pexels_launch_video({
        "topic": "AI Infinity launch",
        "title": "AI Infinity launch",
        "heading": "Real-world examples",
        "visual_focus": "a creator recording a short video on camera with a phone and studio lighting",
        "visual_query": "content creator filming camera studio",
        "visual_search_subject": "content creator",
    }, tmp_path, 5)

    assert row is not None
    assert row["source_id"] == 7677005
    assert row["selection_scene_index"] == 5


def test_pixabay_image_adapter_preserves_asset_identity_and_license(monkeypatch, tmp_path):
    monkeypatch.setenv("PIXABAY_API_KEY", "test-pixabay-key")

    def fake_json(url, headers=None, timeout=20):
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(url)
        assert parsed.scheme == "https" and parsed.netloc == "pixabay.com"
        assert parsed.path == "/api/"
        params = parse_qs(parsed.query)
        assert params["key"] == ["test-pixabay-key"]
        assert params["q"] == ["solar panel farm"]
        assert params["safesearch"] == ["true"]
        return {
            "hits": [{
                "id": 123456,
                "pageURL": "https://pixabay.com/photos/solar-panel-farm-123456/",
                "user": "Example Photographer",
                "tags": "solar panel, renewable energy, farm",
                "imageWidth": 1280,
                "imageHeight": 720,
                "largeImageURL": "https://pixabay.com/get/example-large.jpg",
            }]
        }

    def fake_download(url, path, **kwargs):
        assert url == "https://pixabay.com/get/example-large.jpg"
        return _write_test_jpeg(studio, path, color=(25, 115, 70))

    monkeypatch.setattr(studio, "http_json", fake_json)
    monkeypatch.setattr(studio, "download", fake_download)
    rows = studio._pixabay_images("solar panel farm", tmp_path, limit=1)

    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "image"
    assert row["source"] == "Pixabay"
    assert row["source_id"] == 123456
    assert row["creator"] == "Example Photographer"
    assert "renewable energy" in row["tags"]
    assert row["source_url"] == "https://pixabay.com/photos/solar-panel-farm-123456/"
    assert row["license_url"] == "https://pixabay.com/service/license-summary/"
    assert row["rights_status"] == "license_metadata_present"
    assert row["commercial_use_state"] == "commercial_use_allowed_under_provider_terms"
    assert studio.Image.open(row["path"]).size == (1280, 720)


def test_library_of_congress_adapter_keeps_item_rights_review_required(monkeypatch, tmp_path):
    def fake_json(url, headers=None, timeout=20):
        assert url.startswith("https://www.loc.gov/photos/?")
        return {
            "results": [{
                "id": "https://www.loc.gov/item/loc-test-1/",
                "url": "https://www.loc.gov/item/loc-test-1/",
                "title": "Solar panels at an energy research site",
                "description": "Solar panels on a research facility.",
                "contributor": ["Example Creator"],
                "rights": "Rights Advisory: consult the individual item record.",
                "image_url": [
                    "https://tile.loc.gov/storage-services/small.jpg",
                    "https://tile.loc.gov/storage-services/large.jpg",
                ],
            }]
        }

    def fake_download(url, path, **kwargs):
        assert url == "https://tile.loc.gov/storage-services/large.jpg"
        return _write_test_jpeg(studio, path, color=(145, 95, 25))

    monkeypatch.setattr(studio, "http_json", fake_json)
    monkeypatch.setattr(studio, "download", fake_download)
    rows = studio._loc_images("solar panels", tmp_path, limit=1)

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "Library of Congress"
    assert row["source_url"] == "https://www.loc.gov/item/loc-test-1/"
    assert row["rights_advisory"].startswith("Rights Advisory:")
    assert row["rights_status"] == "REVIEW_REQUIRED"
    assert row["commercial_use_state"] == "UNKNOWN_REVIEW_REQUIRED"
    assert row["license_url"] is None
    assert closure._rights_evidence_state(row) == "review_required"
    assert studio.Image.open(row["path"]).size == (1280, 720)


def test_caption_qc_uses_measured_narration_timeline_near_duration_boundary(monkeypatch, tmp_path):
    """Equal-time cue timing avoids a >5.75s last cue after word balancing."""
    master = tmp_path / "master.mp4"
    master.write_bytes(b"x" * 12001)
    captions = tmp_path / "captions.srt"
    chapters = [{
        "duration": 20.0,
        "actual_duration": 20.0,
        "narration": "Solar panels turn sunlight into electricity",
    }]
    studio.write_srt(chapters, captions)
    srt_text = captions.read_text(encoding="utf-8")
    assert sum(line.strip().isdigit() for line in srt_text.splitlines()) == 5
    assert "00:00:16,000 --> 00:00:20,000" in srt_text

    monkeypatch.setattr(studio, "FAST_MODE", True)
    monkeypatch.setattr(studio, "SMOKE", True)
    monkeypatch.setattr(studio, "ffprobe_json", lambda path: {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
            {"codec_type": "audio", "codec_name": "aac", "duration": "20.000"},
        ]
    })
    monkeypatch.setattr(studio, "probe_duration", lambda path: 20.02)
    monkeypatch.setattr(studio, "_audio_peak_db", lambda path: -15.0)

    result = studio.quality_check(master, chapters, [{"source": "test source"}], captions)

    assert result["checks"]["caption_count_matches_scenes"] is True
    assert result["checks"]["voice_video_duration_aligned"] is True
    assert result["checks"]["scene_count"] is True
    assert result["checks"]["visual_assets_present"] is True
    assert result["checks"]["fallback_visual_policy_passed"] is True

def test_full_hd_resolution_truth_requires_real_1080p_in_either_orientation():
    studio = closure._studio()

    assert studio._is_full_hd_resolution(1280, 720) is False
    assert studio._is_full_hd_resolution(1920, 1080) is True
    assert studio._is_full_hd_resolution(1080, 1920) is True
    assert studio._is_full_hd_resolution(0, 1920) is False
    assert studio._is_full_hd_resolution(1920, 0) is False


def test_natural_language_title_uses_subject_after_video_for_clause():
    for label in ("ALPHA", "BETA"):
        intent = canonical.compile_intent(
            f"Create a 20-second cinematic video for CONCURRENCY-{label} with a distinct narrative.",
            {"duration": 20, "format": "short"},
        )
        assert intent["title"] == f"CONCURRENCY-{label}"

def test_cc0_test_fixture_label_does_not_bypass_original_work_rights_evidence():
    # A public-domain label on the recovery-test image is not, by itself,
    # sufficient evidence for the application's original-vector ownership path.
    fixture = {
        "source": "AI Infinity deterministic CC0 recovery-test illustration",
        "source_url": None,
        "creator": "AI Infinity repository test fixture",
        "license": "CC0 1.0 Universal Public Domain Dedication",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "rights_status": "original_asset",
        "commercial_use_state": "public_domain_cc0_1_0",
        "fallback": True,
        "quality_tier": "original_vector_editorial_fallback",
        "asset_kind": "illustrative_graphic_not_photograph",
        "fallback_record": {
            "truthful": True,
            "execution_state": "DEGRADED",
            "fallback_method": "Deterministic CC0 illustration used only in fault-injection acceptance tests",
            "quality_change": "Real scene media replaced by a labelled test illustration; not source footage",
            "license_change": "Provider-specific rights replaced by original CC0 1.0 test fixture",
        },
    }

    assert closure._rights_evidence_state(fixture) == "blocked_fallback"


def test_edge_tts_candidates_map_canonical_language_keys_and_ignore_ui_placeholders():
    studio = closure._studio()

    assert studio._edge_tts_voice_candidates("English", "default") == [
        "en-US-AriaNeural", "en-US-JennyNeural"
    ]
    assert studio._edge_tts_voice_candidates("Urdu", "default") == [
        "ur-PK-AsadNeural", "ur-PK-UzmaNeural"
    ]
    assert studio._edge_tts_voice_candidates("Pashto", "default") == [
        "ps-AF-LatifaNeural", "ps-AF-GulNawazNeural"
    ]
    assert studio._edge_tts_voice_candidates("Arabic", "default") == [
        "ar-AE-FatimaNeural", "ar-SA-ZariyahNeural"
    ]
    # A voice from another language must not silently override the requested one.
    assert studio._edge_tts_voice_candidates("Urdu", "en-US-AriaNeural") == [
        "ur-PK-AsadNeural", "ur-PK-UzmaNeural"
    ]
    assert "default" not in studio._edge_tts_voice_candidates("English", "default")
    assert studio._edge_tts_voice_candidates("Dari", "default") == []


def test_voice_provider_quality_never_mislabels_local_synthetic_fallback_as_neural():
    assert closure._voice_provider_quality_tier([
        "edge-tts-neural:en-US-AriaNeural",
        "edge-tts-neural:en-US-AriaNeural-retimed",
    ]) == "neural_tts"
    # The optional configured ElevenLabs adapter returns this exact provider label
    # after validating its real audio artifact; it must not be marked unknown.
    assert closure._voice_provider_quality_tier(["elevenlabs"]) == "neural_tts"
    assert closure._voice_provider_quality_tier(["elevenlabs-retimed"]) == "neural_tts"
    assert closure._voice_provider_quality_tier([
        "local-espeak-fast-fallback",
    ]) == "local_synthetic_fallback"
    assert closure._voice_provider_quality_tier([
        "edge-tts-neural:en-US-AriaNeural",
    ], edge_tts_fallback_count=1) == "provider_fallback_or_mixed"
    assert closure._voice_provider_quality_tier(["unknown"]) == "unknown_or_unverified"
    assert closure._voice_provider_quality_tier(
        ["edge-tts-neural:en-US-AriaNeural"], silent_fallback_count=1
    ) == "silent_or_unavailable"


def test_ai_infinity_launch_fallback_is_product_specific_and_uses_clear_overlays(monkeypatch):
    studio = closure._studio()
    monkeypatch.setattr(studio, "FAST_MODE", True)
    brief = (
        "Create a professional 60-second AI Infinity launch video with narration, "
        "captions, a thumbnail and a complete creator package."
    )
    plan = studio._fallback_creative_plan(
        brief, brief, "long", 60, "general audience",
        "cinematic, confident, clear", {"sources": []}, "English",
    )
    chapters = plan["chapters"]

    assert len(chapters) == 8
    assert chapters[0]["narration"].startswith(
        "AI Infinity turns a creative brief into connected, editable work"
    )
    assert "source records" in chapters[2]["narration"].lower()
    assert plan["hook"].startswith("One clear brief.")
    assert "actual deliverables" in plan["cta"]
    assert "quality and rights checks" in plan["cta"]
    assert "What changes when the usual approach stops working" not in plan["hook"]
    assert "Every strong video starts with a clear point of view" not in " ".join(
        chapter["narration"] for chapter in chapters
    )
    assert [chapter["on_screen"] for chapter in chapters] == [
        "From brief to finished work",
        "Define the creative brief",
        "Evidence stays visible",
        "Script, visuals, voice, captions",
        "Inspect the rendered cut",
        "Revise. Render. Review.",
        "Export the complete package",
        "Proof before publishing",
    ]

    # The same deterministic fallback must contain enough spoken material for
    # a real three-minute render; a playable timeline with a short script is not
    # professional long-form coverage.
    long_plan = studio._fallback_creative_plan(
        brief, brief, "long", 180, "general audience",
        "cinematic, confident, clear", {"sources": []}, "English",
    )
    long_words = sum(len(chapter["narration"].split()) for chapter in long_plan["chapters"])
    assert len(long_plan["chapters"]) >= 6
    assert long_words >= 360, (long_words, long_plan["chapters"])
    assert sum(len(chapter["narration"].split()) for chapter in chapters) >= 60

def test_pashto_ai_infinity_launch_fallback_has_runtime_sufficient_product_copy_and_local_overlays(monkeypatch):
    studio = closure._studio()
    monkeypatch.setattr(studio, "FAST_MODE", True)
    brief = (
        "Create a professional 60-second AI Infinity launch video in Pashto, "
        "9:16 vertical, with natural voiceover, captions and music."
    )
    plan = studio._fallback_creative_plan(
        brief, brief, "short", 60, "general audience",
        "cinematic, confident, clear", {"sources": []}, "Pashto",
    )
    chapters = plan["chapters"]
    assert len(chapters) == 5
    narration = " ".join(chapter["narration"] for chapter in chapters)
    # The production truth gate requires two spoken words per requested second.
    assert len(re.findall(r"[\w'-]+", narration)) >= 120, narration
    assert any(char in narration for char in "ځښږڅټډړګڼ")
    assert plan["hook"].startswith("یوه روښانه موخه")
    assert "حقونه تایید کړئ" in plan["cta"]
    assert [chapter["on_screen"] for chapter in chapters] == [
        "له نظره تر بشپړ کاره",
        "روښانه هدف، یو بهیر",
        "شواهد او د حقونو ریکارډ",
        "صحنه، غږ او سرلیکونه",
        "د ارزونې لپاره بشپړه بسته",
    ]

def test_final_delivery_loudness_master_preserves_video_and_validates_streams(monkeypatch, tmp_path):
    target_studio = closure._studio()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video" * 1200)
    captured = {}

    def fake_ffmpeg(*args, **kwargs):
        captured["args"] = [str(value) for value in args]
        captured["timeout"] = kwargs.get("timeout")
        Path(args[-1]).write_bytes(b"normalized-delivery" * 1200)

    def fake_probe(path):
        return {
            "format": {"duration": "20.0"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "duration": "20.0"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "20.0"},
                {"codec_type": "subtitle", "codec_name": "mov_text"},
            ],
        }

    monkeypatch.setattr(target_studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(target_studio, "ffprobe_json", fake_probe)
    monkeypatch.setattr(target_studio, "probe_duration", lambda path: 20.0)
    out = tmp_path / "mastered.mp4"

    result = target_studio._normalize_delivery_loudness(source, out, fast_mode=False)

    args = captured["args"]
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "copy"
    assert "-af" in args
    audio_filter = args[args.index("-af") + 1]
    assert "atrim=duration=20.000" in audio_filter
    assert "apad=whole_dur=20.000" in audio_filter
    assert "loudnorm=I=-16.0:TP=-1.5:LRA=11.0" in audio_filter
    assert "0:s?" in args
    assert result["status"] == "completed" and result["truthful"] is True
    assert result["target_integrated_lufs"] == -16.0
    assert result["video_stream_copied"] is True
    assert result["subtitle_stream_count"] == 1
    assert out.is_file() and out.stat().st_size > 10_000


def test_final_delivery_loudness_master_rejects_invalid_targets(monkeypatch, tmp_path):
    target_studio = closure._studio()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video" * 1200)
    monkeypatch.setenv("AI_INFINITY_DELIVERY_TARGET_LUFS", "-40")
    try:
        target_studio._normalize_delivery_loudness(source, tmp_path / "mastered.mp4")
    except ValueError as exc:
        assert "between -20 and -14 LUFS" in str(exc)
    else:
        raise AssertionError("unsafe loudness target should have been rejected")

def test_final_delivery_profile_enforces_full_hd_for_high_quality_briefs(monkeypatch, tmp_path):
    target_studio = closure._studio()
    source = tmp_path / "source_720p.mp4"
    output = tmp_path / "source_1080p.mp4"
    source.write_bytes(b"real-source-fixture" * 1200)
    captured = {}

    def fake_ffmpeg(*args, **kwargs):
        captured["args"] = [str(value) for value in args]
        captured["timeout"] = kwargs.get("timeout")
        Path(args[-1]).write_bytes(b"normalized-full-hd-fixture" * 1200)

    def fake_probe(path):
        resolution = (1920, 1080) if Path(path) == output else (1280, 720)
        return {
            "format": {"duration": "60.0"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": resolution[0], "height": resolution[1]},
                {"codec_type": "audio", "codec_name": "aac", "duration": "60.0"},
            ],
        }

    monkeypatch.setattr(target_studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(target_studio, "ffprobe_json", fake_probe)
    monkeypatch.setattr(target_studio, "probe_duration", lambda path: 60.0)

    result = target_studio._normalize_delivery_profile(source, output, "16:9", require_full_hd=True)

    args = captured["args"]
    assert result == output
    assert "-vf" in args
    assert "scale=1920:1080:force_original_aspect_ratio=increase:flags=lanczos,crop=1920:1080,setsar=1" in args
    assert args[args.index("-map") + 1] == "0:v:0"
    assert "0:a:0" in args
    assert "-crf" in args and args[args.index("-crf") + 1] == "18"
    assert output.is_file() and output.stat().st_size > 10_000


def test_final_delivery_subtitle_mux_uses_authoritative_srt(monkeypatch, tmp_path):
    target_studio = closure._studio()
    source = tmp_path / "final_video.mp4"
    captions = tmp_path / "captions.srt"
    output = tmp_path / "final_with_subtitles.mp4"
    source.write_bytes(b"real-video-fixture" * 1200)
    captions.write_text(
        "1\\n00:00:00,000 --> 00:00:03,000\\nEvery world begins with an idea.\\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_ffmpeg(*args, **kwargs):
        captured["args"] = [str(value) for value in args]
        Path(args[-1]).write_bytes(b"video-with-subtitle-fixture" * 1200)

    def fake_probe(path):
        streams = [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            {"codec_type": "audio", "codec_name": "aac", "duration": "60.0"},
        ]
        if Path(path) == output:
            streams.append({"codec_type": "subtitle", "codec_name": "mov_text", "duration": "60.0"})
        return {"format": {"duration": "60.0"}, "streams": streams}

    monkeypatch.setattr(target_studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(target_studio, "ffprobe_json", fake_probe)
    monkeypatch.setattr(target_studio, "probe_duration", lambda path: 60.0)

    result = target_studio._ensure_delivery_subtitle_track(source, captions, output)

    args = captured["args"]
    assert result == output
    assert str(captions) in args
    assert "-map" in args and "1:0" in args
    assert "-c:s" in args and args[args.index("-c:s") + 1] == "mov_text"
    assert "-metadata:s:s:0" in args and "language=eng" in args
    assert "-disposition:s:0" in args and "default" in args
    assert output.is_file() and output.stat().st_size > 10_000
