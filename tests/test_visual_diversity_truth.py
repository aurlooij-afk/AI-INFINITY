"""Regression tests for the professional production visual-diversity truth gate."""
import hashlib
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

    # Exercise the exact validator used by production truth, not just local syntax.
    metrics = closure._caption_timing_metrics(path, 20.0)
    assert metrics["cue_count"] >= 4
    assert metrics["timing_valid"] is True


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
    assert len(chapters) == 4
    assert all(
        chapter["visual_search_subject"] == "content creator"
        for chapter in chapters
    )
    assert len({chapter["visual_focus"] for chapter in chapters}) == len(chapters)
    assert any("editing timeline" in chapter["visual_query"] for chapter in chapters)
    assert any("preview monitor" in chapter["visual_query"] for chapter in chapters)



def test_explicit_long_form_selection_survives_short_video_collateral():
    studio = closure._studio()
    text = "Create a long-form 16:9 film and include three Shorts as secondary campaign assets."
    assert studio._resolve_format_intent("long", text, 60, explicit=True) == "long"
    assert studio._resolve_format_intent("long", text, 60, explicit=False) == "short"


def test_explicit_short_form_selection_is_not_silently_rewritten():
    studio = closure._studio()
    text = "Create a portrait launch reel."
    assert studio._resolve_format_intent("short", text, 300, explicit=True) == "short"



def test_fast_mode_scene_budget_meets_sixty_second_professional_floor():
    studio = closure._studio()
    assert studio._max_scene_count(True, True, False, False) == 4
    assert studio._max_scene_count(False, True, False, False) == 4
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



def test_curated_pexels_source_is_keyless_late_scene_only_and_rights_documented(monkeypatch, tmp_path):
    scene = {
        "heading": "What changes",
        "visual_focus": "video editing workstation displaying a final cut on a computer monitor",
    }
    assert studio._curated_pexels_editorial_photo(scene, tmp_path, 3) is None

    def fake_download(url, path, **kwargs):
        assert "images.pexels.com/photos/695730/" in url
        image = studio.Image.new("RGB", (1280, 720), (18, 35, 60))
        image.save(path, "JPEG")
        return path

    monkeypatch.setattr(studio, "download", fake_download)
    candidate = studio._curated_pexels_editorial_photo(scene, tmp_path, 4)
    assert candidate is not None
    assert studio._visual_candidate_relevant(
        candidate, "content creator", scene["visual_focus"]
    )
    assert candidate["source_url"] == "https://www.pexels.com/photo/computer-monitor-turned-on-695730/"
    assert candidate["creator"] == "Francesco Paggiaro"
    assert candidate["license_url"] == "https://www.pexels.com/license/"
    assert candidate["commercial_use_state"] == "commercial_use_allowed_under_provider_terms"
    assert candidate["rights_status"] == "license_metadata_present"



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



def test_fast_product_launch_plan_uses_four_complete_story_scenes(monkeypatch):
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
    assert len(chapters) == 4
    script = " ".join(x["narration"] for x in chapters)
    assert len(script.split()) >= 60
    assert "research" in script.lower() and "captions" in script.lower()
    assert all(x["narration"].rstrip().endswith((".", "!", "?")) for x in chapters)
