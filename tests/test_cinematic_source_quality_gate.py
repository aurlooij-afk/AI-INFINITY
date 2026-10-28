"""Regression coverage for strict source-motion requirements on professional video jobs."""
import hashlib
import json
from pathlib import Path

import pytest

import reality_first_3901 as reality
import studio_ultimate as studio


def _caption_file(path: Path) -> None:
    cues = []
    for index in range(12):
        start = index * 5
        end = start + 5
        start_tc = f"00:{start // 60:02d}:{start % 60:02d},000"
        end_tc = f"00:{end // 60:02d}:{end % 60:02d},000"
        cues.append(f"{index + 1}\n{start_tc} --> {end_tc}\nA clear line of narration.")
    path.write_text("\n\n".join(cues) + "\n", encoding="utf-8")


def _setup_quality(monkeypatch, tmp_path):
    monkeypatch.setattr(studio, "FAST_MODE", False)
    monkeypatch.setattr(studio, "SMOKE", False)
    monkeypatch.setattr(studio, "ffprobe_json", lambda _path: {
        "format": {"duration": "60.0"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            {"codec_type": "audio", "codec_name": "aac", "duration": "60.0", "sample_rate": "48000", "channels": 2},
            {"codec_type": "subtitle", "codec_name": "mov_text"},
        ],
    })
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 60.0)
    monkeypatch.setattr(studio, "_audio_peak_db", lambda _path: -18.0)
    master = tmp_path / "final.mp4"
    master.write_bytes(b"x" * 20001)
    captions = tmp_path / "captions.srt"
    _caption_file(captions)
    chapters = [{"duration": seconds, "actual_duration": seconds} for seconds in (8, 8, 9, 9, 9, 9, 8)]
    return master, captions, chapters


def _licensed_motion_assets(tmp_path, count):
    rows = []
    for index in range(count):
        path = tmp_path / f"scene_{index + 1:02d}.mp4"
        path.write_bytes((f"real-media-fixture-{index + 1}".encode("utf-8") * 3000))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({
            "kind": "video",
            "path": str(path),
            "source_url": f"https://www.pexels.com/video/verified-free-clip-{index + 1}/",
            "license": "Pexels License",
            "license_url": "https://www.pexels.com/license/",
            "rights_status": "license_metadata_present",
            "source_id": index + 1,
            "asset_sha256": digest,
            "fallback": False,
        })
    return rows


def _truthful_fallback(tmp_path):
    return {
        "kind": "image",
        "path": str(tmp_path / "procedural_visual_end_card.png"),
        "source": "AI Infinity original vector editorial fallback",
        "fallback": True,
        "fallback_record": {
            "truthful": True,
            "execution_state": "DEGRADED",
            "fallback_method": "original vector brand end-card",
            "quality_change": "Reduced live-action coverage",
            "license_change": "Original illustrative artwork, not live-action footage",
        },
    }


def test_professional_launch_video_passes_with_six_distinct_motion_sources_and_one_end_card_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_INFINITY_REQUIRE_SOURCE_VISUALS", "1")
    master, captions, chapters = _setup_quality(monkeypatch, tmp_path)
    assets = _licensed_motion_assets(tmp_path, 6) + [_truthful_fallback(tmp_path)]

    result = studio.quality_check(master, chapters, assets, captions)

    assert result["checks"]["required_source_motion_coverage"] is True, result["checks"]
    assert result["checks"]["fallback_visual_budget_ok"] is True, result["checks"]
    assert result["visual_source_coverage"] == {
        "required": True,
        "scene_count": 7,
        "distinct_verified_motion_sources": 6,
        "minimum_motion_sources": 5,
        "fallback_visual_count": 1,
        "maximum_fallback_visuals": 1,
    }
    assert result["passed"] is True, json.dumps(result, indent=2)


def test_professional_launch_video_rejects_illustration_heavy_result_even_when_media_technical_checks_pass(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_INFINITY_REQUIRE_SOURCE_VISUALS", "1")
    master, captions, chapters = _setup_quality(monkeypatch, tmp_path)
    assets = _licensed_motion_assets(tmp_path, 1) + [_truthful_fallback(tmp_path) for _ in range(6)]

    result = studio.quality_check(master, chapters, assets, captions)

    assert result["checks"]["file_present"] is True
    assert result["checks"]["full_hd"] is True
    assert result["checks"]["audio_present"] is True
    assert result["checks"]["required_source_motion_coverage"] is False
    assert result["checks"]["fallback_visual_budget_ok"] is False
    assert result["passed"] is False


def test_standard_mode_can_still_label_visual_fallback_without_claiming_source_coverage(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_INFINITY_REQUIRE_SOURCE_VISUALS", raising=False)
    master, captions, chapters = _setup_quality(monkeypatch, tmp_path)
    monkeypatch.setattr(studio, "FAST_MODE", True)
    assets = _licensed_motion_assets(tmp_path, 1) + [_truthful_fallback(tmp_path) for _ in range(6)]

    result = studio.quality_check(master, chapters, assets, captions)

    assert result["checks"]["fallback_visual_used"] is True
    assert result["checks"]["required_source_motion_coverage"] is True
    assert result["checks"]["fallback_visual_budget_ok"] is True

def test_timecoded_ai_infinity_brand_film_sets_curated_campaign_profile():
    brief = """Create an extraordinary 60-second cinematic brand film for AI Infinity.
    0–4 seconds — The hook. A creator sees the first idea. Voiceover: “Every extraordinary world begins with something nobody else can see.”
    4–12 seconds — The spark. A dramatic landscape reveals an imagined future city.
    12–25 seconds — The transformation. Research notes and storyboards turn the idea into a script.
    25–40 seconds — The cinematic experience. Landscapes, futuristic architecture and a luminous tunnel reveal possibility.
    40–49 seconds — The craft. A creator edits the timeline and captions.
    49–56 seconds — The payoff. The creator sees the finished film.
    56–60 seconds — The signature ending. AI Infinity. One idea. Infinite worlds.
    VISUAL DIRECTION: Deep midnight blues, controlled cyan and violet, warm gold practical light.
    VOICE, MUSIC AND SOUND: Original narration and score."""
    plan = studio._explicit_timed_storyboard_plan(
        title="ONE IDEA. INFINITE WORLDS.",
        objective=brief,
        fmt="long",
        duration=60,
        audience="global creators",
        tone="premium cinematic, human, emotionally intelligent",
        language="English",
        topic="AI Infinity — One Idea. Infinite Worlds.",
    )

    assert plan is not None
    assert plan["storyboard_origin"] == "explicit_timecoded_user_brief"
    assert all(scene["campaign_profile"] == "ai_infinity_launch" for scene in plan["chapters"])
    # Keep every authored beat boundary, while splitting the two broad
    # transformation beats into shots that can carry distinct verified footage.
    assert len(plan["chapters"]) == 9
    assert [(scene["timecode"]["start_seconds"], scene["timecode"]["end_seconds"])
            for scene in plan["chapters"]] == [
        (0, 4), (4, 12), (12, 19), (19, 25), (25, 32),
        (32, 40), (40, 49), (49, 56), (56, 60),
    ]
    assert sum(float(scene["duration"]) for scene in plan["chapters"]) == 60.0
    assert len({scene["visual_search_subject"] for scene in plan["chapters"]}) == 9
    assert sum(len(scene["narration"].split()) for scene in plan["chapters"]) >= 120

def test_delivery_loudness_mastering_caps_output_to_source_programme_duration(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source programme bytes" * 1000)
    output = tmp_path / "mastered.mp4"
    source_probe = {
        "format": {"duration": "60.000"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "duration": "60.000"},
            {"codec_type": "audio", "codec_name": "aac", "duration": "60.000", "time_base": "1/48000"},
            {"codec_type": "subtitle", "codec_name": "mov_text", "duration": "66.245"},
        ],
    }
    output_probe = {
        "format": {"duration": "60.000"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "duration": "60.000"},
            {"codec_type": "audio", "codec_name": "aac", "duration": "60.000", "time_base": "1/48000"},
            {"codec_type": "subtitle", "codec_name": "mov_text", "duration": "60.000"},
        ],
    }
    command = {}

    def fake_ffmpeg(*args, timeout=240):
        command["args"] = args
        Path(args[-1]).write_bytes(b"mastered programme bytes" * 1000)

    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(studio, "ffprobe_json", lambda path: source_probe if Path(path) == source else output_probe)

    result = studio._normalize_delivery_loudness(source, output, fast_mode=False)

    args = list(command["args"])
    assert "-t" in args
    assert args[args.index("-t") + 1] == "60.000"
    assert result["output_duration_seconds"] == 60.0
    assert result["audio_video_duration_match"] is True


def test_final_loudness_mux_caps_a_sixty_six_second_input_to_the_requested_sixty_seconds(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-master" * 2000)
    out = tmp_path / "final.mp4"
    captured = {}

    def fake_ffmpeg(*args, timeout=240):
        captured["args"] = [str(value) for value in args]
        out_path = Path(str(args[-1]))
        out_path.write_bytes(b"final-master" * 2000)

    def fake_probe(path):
        duration = "66.245" if Path(path) == source else "60.000"
        return {
            "format": {"duration": duration},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "duration": duration},
                {"codec_type": "audio", "codec_name": "aac", "duration": duration},
                {"codec_type": "subtitle", "codec_name": "mov_text"},
            ],
        }

    monkeypatch.setattr(studio, "probe_duration", lambda path: 66.245)
    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(studio, "ffprobe_json", fake_probe)

    result = studio._normalize_delivery_loudness(
        source, out, fast_mode=False, target_duration_seconds=60.0
    )

    args = captured["args"]
    assert args[args.index("-t") + 1] == "60.000"
    audio_filter = args[args.index("-af") + 1]
    assert "atrim=duration=60.000" in audio_filter
    assert "apad=whole_dur=60.000" in audio_filter
    assert args[args.index("-c:v") + 1] == "copy"
    assert result["source_duration_seconds"] == 66.245
    assert result["requested_duration_seconds"] == 60.0
    assert result["output_duration_seconds"] == 60.0
    assert result["audio_video_duration_match"] is True


def test_programme_audio_master_resets_pts_and_pads_to_the_video_runtime(monkeypatch, tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"real programme container fixture" * 1000)
    output = tmp_path / "audio_master.mp3"
    captured = {}

    def fake_ffmpeg(*args, timeout=240):
        captured["args"] = list(args)
        Path(args[-1]).write_bytes(b"padded audio master bytes" * 2000)

    def fake_duration(path):
        if Path(path) == source:
            return 60.0
        return 60.026

    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(studio, "probe_duration", fake_duration)
    monkeypatch.setattr(studio, "_valid_audio_file", lambda _path, minimum_peak_db=-45.0: True)

    result = studio._export_programme_audio_master(source, output, 60.0, fast_mode=False)

    args = captured["args"]
    audio_filter = args[args.index("-af") + 1]
    assert audio_filter.startswith("aresample=48000:async=1:first_pts=0,asetpts=N/SR/TB,")
    assert "apad=whole_dur=60.000" in audio_filter
    assert "atrim=duration=60.000" in audio_filter
    assert "loudnorm=I=-16.0:TP=-1.5:LRA=11.0" in audio_filter
    assert args[args.index("-map") + 1] == "0:a:0"
    assert args[args.index("-t") + 1] == "60.000"
    assert result["status"] == "verified"
    assert result["requested_duration_seconds"] == 60.0
    assert result["output_duration_seconds"] == 60.026
    assert result["truthful"] is True


def test_programme_audio_master_rejects_still_short_output_after_pts_reset(monkeypatch, tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"real programme container fixture" * 1000)
    output = tmp_path / "audio_master.mp3"

    def fake_ffmpeg(*args, timeout=240):
        Path(args[-1]).write_bytes(b"short audio master bytes" * 2000)

    def fake_duration(path):
        return 60.0 if Path(path) == source else 53.832

    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(studio, "probe_duration", fake_duration)
    monkeypatch.setattr(studio, "_valid_audio_file", lambda _path, minimum_peak_db=-45.0: True)

    with pytest.raises(RuntimeError, match="duration mismatch after timestamp reset and padding"):
        studio._export_programme_audio_master(source, output, 60.0, fast_mode=False)


def test_scene_concatenation_copies_rendered_video_but_reencodes_aac_audio(monkeypatch, tmp_path):
    scenes = []
    for index in range(2):
        scene = tmp_path / f"scene_{index + 1:02d}.mp4"
        scene.write_bytes((f"rendered scene fixture {index + 1}".encode("utf-8") * 2000))
        scenes.append(scene)
    output = tmp_path / "master.mp4"
    captured = {}

    def fake_ffmpeg(*args, timeout=240):
        captured["args"] = list(args)
        Path(args[-1]).write_bytes(b"assembled master fixture" * 2000)

    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    # reality_first_3901 wraps the concat function during normal app startup and
    # independently probes its output. This test is about command construction,
    # so stub only that wrapper's acceptance helpers; the production smoke test
    # still uses the real FFmpeg output probe and decoder.
    monkeypatch.setattr(reality, "_valid_file", lambda path, minimum=1: Path(path).is_file() and Path(path).stat().st_size >= minimum)
    monkeypatch.setattr(reality, "_media_probe", lambda _path: {"ok": True})
    monkeypatch.setattr(reality, "_upsert_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr(reality, "_event", lambda *args, **kwargs: None)

    studio.concat_segments(scenes, output)

    args = captured["args"]
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-b:a") + 1] == "192k"
    assert args[args.index("-ar") + 1] == "48000"
    assert args[args.index("-ac") + 1] == "2"
    manifest_path = Path(args[args.index("-i") + 1])
    manifest = manifest_path.read_text(encoding="utf-8")
    assert all(str(scene) in manifest for scene in scenes)
    assert output.is_file()
    assert output.stat().st_size > 10_000

def test_curated_launch_uses_scene_focal_concept_when_brief_has_multiple_visual_concepts(monkeypatch):
    monkeypatch.setattr(studio, "_visual_candidate_relevant", lambda *_args: False)
    mountains = {
        "title": "Clouds formation in the sky above the mountains",
        "description": "Dynamic clouds move over peaceful mountain peaks during sunset.",
        "tags": "cinematic mountains landscape clouds moving clouds panoramic horizon nature",
    }
    assert studio._curated_launch_visual_candidate_relevant(
        mountains,
        required_concepts={"landscape"},
        scene_concepts={"landscape", "cityscape"},
        explicit_launch_profile=True,
        search_subject="dramatic mountains and futuristic city",
        focus="mountain landscape opens into a futuristic city",
    ) is True

    unrelated = {
        "title": "A cat in a garden",
        "description": "A house cat sleeping in a garden",
        "tags": "cat pet garden",
    }
    assert studio._curated_launch_visual_candidate_relevant(
        unrelated,
        required_concepts={"landscape"},
        scene_concepts={"landscape", "cityscape"},
        explicit_launch_profile=True,
        search_subject="dramatic mountains and futuristic city",
        focus="mountain landscape opens into a futuristic city",
    ) is False
    assert studio._curated_launch_visual_candidate_relevant(
        mountains,
        required_concepts={"landscape"},
        scene_concepts={"landscape", "cityscape"},
        explicit_launch_profile=False,
        search_subject="dramatic mountains and futuristic city",
        focus="mountain landscape opens into a futuristic city",
    ) is False

def test_explicit_launch_uses_scene_local_visual_query_for_landscape_clip(monkeypatch, tmp_path):
    monkeypatch.setattr(studio, "_visual_candidate_relevant", lambda *_args: False)
    media = tmp_path / "pexels_launch_video_5040711.mp4"
    media.write_bytes(b"validated landscape source fixture" * 1500)
    monkeypatch.setattr(studio, "ffprobe_json", lambda _path: {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
    })
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 8.0)
    scene = {
        "topic": "AI Infinity — One Idea. Infinite Worlds.",
        "heading": "The Spark",
        "campaign_profile": "ai_infinity_launch",
        "visual_focus": "An idea opens into an imagined world",
        "visual_query": "dramatic mountain landscape opens toward a futuristic city",
        "narration": "The first idea reveals a vast landscape of possibility.",
        "on_screen": "Step into the vision",
    }

    monkeypatch.setattr(studio, "_source_has_prolonged_black_frames", lambda *_args, **_kwargs: False)
    selected = studio._curated_pexels_launch_video(scene, tmp_path, index=2)

    assert selected is not None
    assert selected["source_id"] == 5040711
    assert selected["source"] == "Pexels curated free editorial video"
    assert selected["validated_media"] is True
    assert selected["path"] == str(media)

def test_procedural_fallback_title_uses_scene_subject_not_render_prompt_boilerplate(tmp_path):
    prompt = (
        "Premium cinematic live-action film still: AI Infinity logo reveal against a deep cinematic background. "
        "Overall visual direction: midnight blue, restrained cyan and warm gold. "
        "No fabricated UI, no gibberish text."
    )

    asset = studio._procedural_image(prompt, tmp_path, 7, width=640, height=360)

    assert asset is not None
    assert asset["topic"] == "AI Infinity logo reveal against a deep cinematic background"
    assert "Premium cinematic" not in asset["topic"]
    assert "Overall visual direction" not in asset["topic"]

def test_curated_launch_retries_a_second_distinct_landscape_source(monkeypatch, tmp_path):
    """A duplicate mountain clip must not force an avoidable illustrative fallback."""
    monkeypatch.setattr(studio, "FAST_MODE", False)
    monkeypatch.setattr(studio, "_visual_candidate_relevant", lambda *_args: True)
    monkeypatch.setattr(studio, "_curated_launch_visual_candidate_relevant", lambda *_args: True)

    first_bytes = b"previously selected landscape source" * 900
    (tmp_path / "selected_visual_01.mp4").write_bytes(first_bytes)
    # Simulate the first curated mountain clip having already been selected by
    # an earlier scene in this same project.
    (tmp_path / "pexels_launch_video_5040711.mp4").write_bytes(first_bytes)

    def fake_download(url, path, **_kwargs):
        if "28946305" in url:
            Path(path).write_bytes(b"second distinct mountain landscape source" * 900)
            return
        raise AssertionError(f"Unexpected source download: {url}")

    monkeypatch.setattr(studio, "download", fake_download)
    monkeypatch.setattr(studio, "ffprobe_json", lambda _path: {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
    })
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 8.0)

    scene = {
        "topic": "AI Infinity — One Idea. Infinite Worlds.",
        "heading": "The Spark",
        "campaign_profile": "ai_infinity_launch",
        "visual_focus": "A dramatic mountain landscape with moving clouds",
        "visual_query": "mountain landscape clouds moving at sunset",
        "narration": "One idea opens into a world of possibility.",
        "on_screen": "Step into the vision",
    }

    monkeypatch.setattr(studio, "_source_has_prolonged_black_frames", lambda *_args, **_kwargs: False)
    selected = studio._curated_pexels_launch_video(scene, tmp_path, index=2)

    assert selected is not None
    assert selected["source_id"] == 28946305
    assert selected["source_url"] == "https://www.pexels.com/video/clouds-over-mountain-landscape-timelapse-28946305/"
    assert selected["validated_media"] is True
    assert selected["quality_tier"] == "curated_free_source_motion_video"

def test_curated_launch_uses_distinct_scriptwriting_source_instead_of_fallback(monkeypatch, tmp_path):
    """A duplicate first writing clip should retry a second relevant source."""
    monkeypatch.setattr(studio, "FAST_MODE", False)

    duplicate_bytes = b"existing writing clip already assigned to another scene" * 900
    alternate_bytes = b"second distinct scriptwriting and storyboard clip" * 1000
    (tmp_path / "selected_visual_01.mp4").write_bytes(duplicate_bytes)

    def fake_download(url, path, **_kwargs):
        if "6550131" in url:
            Path(path).write_bytes(duplicate_bytes)
            return
        if "8086204" in url:
            Path(path).write_bytes(alternate_bytes)
            return
        raise AssertionError(f"Unexpected source download: {url}")

    monkeypatch.setattr(studio, "download", fake_download)
    monkeypatch.setattr(studio, "ffprobe_json", lambda _path: {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
    })
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 8.0)
    monkeypatch.setattr(studio, "_source_has_prolonged_black_frames", lambda *_args, **_kwargs: False)

    scene = {
        "topic": "AI Infinity — One Idea. Infinite Worlds.",
        "heading": "The Transformation",
        "campaign_profile": "ai_infinity_launch",
        "visual_focus": "Overhead script pages and storyboard thumbnails, deliberate marks and natural paper texture",
        "visual_query": "close up of scriptwriting process with pen and script",
        "visual_search_subject": "script pages and storyboard thumbnails",
        "narration": "The idea becomes a researched story and a script.",
        "on_screen": "Research. Shape. Create.",
    }
    selected = studio._curated_pexels_launch_video(scene, tmp_path, index=13)

    assert selected is not None
    assert selected["source_id"] == 8086204
    assert selected["source_url"] == "https://www.pexels.com/video/close-up-of-a-script-8086204/"
    assert selected["quality_tier"] == "curated_free_source_motion_video"
    assert selected["validated_media"] is True
    assert hashlib.sha256(Path(selected["path"]).read_bytes()).hexdigest() == hashlib.sha256(alternate_bytes).hexdigest()
    assert Path(selected["path"]).name == "pexels_launch_video_8086204.mp4"


def test_source_black_frame_gate_detects_sustained_near_black_segments(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"test media bytes")
    monkeypatch.setattr(studio.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    simulated = {"returncode": 0, "stderr": "[blackdetect] black_start:3.29 black_end:5.00 black_duration:1.710000", "stdout": ""}

    def fake_run(*_args, **_kwargs):
        return type("ProcessResult", (), simulated)()

    monkeypatch.setattr(studio.subprocess, "run", fake_run)
    assert studio._source_has_prolonged_black_frames(source, threshold_seconds=0.75) is True

    simulated["stderr"] = "ffmpeg decoded every frame; no sustained black interval"
    assert studio._source_has_prolonged_black_frames(source, threshold_seconds=0.75) is False

    simulated["returncode"] = 1
    assert studio._source_has_prolonged_black_frames(source, threshold_seconds=0.75) is True


def test_curated_launch_skips_a_black_source_and_selects_a_distinct_alternate(monkeypatch, tmp_path):
    monkeypatch.setattr(studio, "FAST_MODE", False)
    monkeypatch.setattr(studio, "_visual_candidate_relevant", lambda *_args: True)
    monkeypatch.setattr(studio, "_curated_launch_visual_candidate_relevant", lambda *_args: True)

    first_bytes = b"source containing prolonged near-black interval" * 800
    second_bytes = b"distinct clean mountain-motion source bytes" * 900

    def fake_download(url, path, **_kwargs):
        if "5040711" in url:
            Path(path).write_bytes(first_bytes)
        elif "28946305" in url:
            Path(path).write_bytes(second_bytes)
        else:
            raise AssertionError(f"Unexpected source download: {url}")

    monkeypatch.setattr(studio, "download", fake_download)
    monkeypatch.setattr(studio, "ffprobe_json", lambda _path: {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
    })
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 8.0)
    monkeypatch.setattr(
        studio, "_source_has_prolonged_black_frames",
        lambda path, **_kwargs: "5040711" in str(path),
    )

    scene = {
        "topic": "AI Infinity — One Idea. Infinite Worlds.",
        "heading": "The Spark",
        "campaign_profile": "ai_infinity_launch",
        "visual_focus": "A dramatic mountain landscape with moving clouds",
        "visual_search_subject": "cinematic mountains and moving clouds",
        "visual_query": "mountain landscape clouds moving at sunset",
        "narration": "One idea opens into a world of possibility.",
        "on_screen": "One idea. Infinite worlds.",
    }
    selected = studio._curated_pexels_launch_video(scene, tmp_path, index=9)

    assert selected is not None
    assert selected["source_id"] == 28946305
    assert hashlib.sha256(Path(selected["path"]).read_bytes()).hexdigest() == hashlib.sha256(second_bytes).hexdigest()
    assert not (tmp_path / "pexels_launch_video_5040711.mp4").exists()


def test_acquire_scene_asset_accepts_curated_focal_match_and_retries_duplicate_motion(monkeypatch, tmp_path):
    """Curated concept matches bypass only duplicate generic relevance and retry collisions."""
    monkeypatch.setattr(studio, "_external_providers_disabled", lambda: True)
    monkeypatch.setattr(studio, "_visual_candidate_relevant", lambda *_args: False)
    monkeypatch.setattr(studio, "ffprobe_json", lambda _path: {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
    })
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 8.0)

    duplicate_bytes = b"same landscape source already committed by another scene" * 100
    alternate_bytes = b"second distinct mountain source for the spark scene" * 100
    prior = tmp_path / "selected_visual_01.mp4"
    prior.write_bytes(duplicate_bytes)
    duplicate_source = tmp_path / "pexels_launch_video_5040711.mp4"
    duplicate_source.write_bytes(duplicate_bytes)
    alternate_source = tmp_path / "pexels_launch_video_28946305.mp4"
    alternate_source.write_bytes(alternate_bytes)

    def curated(path, source_id, title, source_url):
        return {
            "kind": "video",
            "path": str(path),
            "source": "Pexels curated free editorial video",
            "provider": "Pexels",
            "source_id": source_id,
            "source_url": source_url,
            "creator": "Verified Pexels contributor",
            "attribution": "Video by contributor / Pexels",
            "license": "Pexels License",
            "license_url": "https://www.pexels.com/license/",
            "rights_status": "license_metadata_present",
            "quality_tier": "curated_free_source_motion_video",
            "selection_reason": "scene_concepts_and_source_metadata_match",
            "validated_media": True,
            "title": title,
        }

    candidates = [
        curated(
            duplicate_source, 5040711,
            "Clouds formation in the sky above the mountains",
            "https://www.pexels.com/video/clouds-formation-in-the-sky-above-the-mountains-5040711/",
        ),
        curated(
            alternate_source, 28946305,
            "Clouds Over Mountain Landscape Timelapse",
            "https://www.pexels.com/video/clouds-over-mountain-landscape-timelapse-28946305/",
        ),
    ]
    calls = []

    def next_curated(_scene, _outdir, _index):
        calls.append(1)
        return candidates.pop(0) if candidates else None

    monkeypatch.setattr(studio, "_curated_pexels_launch_video", next_curated)
    scene = {
        "topic": "AI Infinity — One Idea. Infinite Worlds.",
        "heading": "The Spark",
        "campaign_profile": "ai_infinity_launch",
        "visual_focus": "wide dramatic mountain landscape and futuristic cityscape",
        "visual_search_subject": "cinematic mountains ocean and futuristic city",
        "visual_query": "mountain landscape clouds moving at sunset",
        "narration": "One small idea becomes a doorway into another world.",
        "on_screen": "One idea. Infinite possibilities.",
    }

    selected = studio.acquire_scene_asset(scene, tmp_path, index=2, prefer_motion=True, duration=8)

    assert len(calls) == 2, "a duplicate first choice should trigger a bounded second-source attempt"
    assert selected["kind"] == "video"
    assert selected["source_id"] == 28946305
    assert selected["quality_tier"] == "curated_free_source_motion_video"
    assert selected["asset_sha256"] == hashlib.sha256(alternate_bytes).hexdigest()
    selected_path = Path(selected["path"])
    assert selected_path.is_file()
    assert selected_path.name == "selected_visual_02.mp4"

def test_explicit_launch_profile_matches_scene_local_landscape_without_repeating_brand(monkeypatch, tmp_path):
    """Project-level launch intent must not be lost when scene text names only its focal shot."""
    monkeypatch.setattr(studio, "FAST_MODE", False)
    monkeypatch.setattr(studio, "_visual_candidate_relevant", lambda *_args: False)
    media_bytes = b"distinct validated mountain motion source" * 1200

    def fake_download(url, path, **_kwargs):
        assert "5040711" in url, f"unexpected source URL: {url}"
        Path(path).write_bytes(media_bytes)

    monkeypatch.setattr(studio, "download", fake_download)
    monkeypatch.setattr(studio, "ffprobe_json", lambda _path: {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
    })
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 8.0)

    scene = {
        "topic": "A mountain landscape opens into a futuristic city",
        "heading": "The Spark",
        "campaign_profile": "ai_infinity_launch",
        "visual_focus": "A dramatic mountain landscape with moving clouds",
        "visual_query": "mountain landscape clouds moving at sunset",
        "narration": "One small idea opens into a vast world of possibility.",
        "on_screen": "One idea. Infinite worlds.",
    }

    monkeypatch.setattr(studio, "_source_has_prolonged_black_frames", lambda *_args, **_kwargs: False)
    selected = studio._curated_pexels_launch_video(scene, tmp_path, index=9)

    assert selected is not None
    assert selected["source_id"] == 5040711
    assert selected["validated_media"] is True
    assert selected["quality_tier"] == "curated_free_source_motion_video"
    assert hashlib.sha256(Path(selected["path"]).read_bytes()).hexdigest() == hashlib.sha256(media_bytes).hexdigest()




@pytest.mark.parametrize(
    ("scene", "expected_source_id"),
    [
        ({
            "topic": "AI Infinity — One Idea. Infinite Worlds.",
            "heading": "The Cinematic World",
            "campaign_profile": "ai_infinity_launch",
            "visual_focus": "A futuristic cityscape skyline at night, tall illuminated architecture and moving city lights",
            "visual_search_subject": "futuristic cityscape at night",
            "visual_query": "futuristic cityscape skyline at night",
            "narration": "The imagined idea expands into a city of tomorrow.",
            "on_screen": "A world takes shape",
        }, 34719182),
        ({
            "topic": "AI Infinity — One Idea. Infinite Worlds.",
            "heading": "Transformation in Motion",
            "campaign_profile": "ai_infinity_launch",
            "visual_focus": "Abstract glowing-line tunnel transitioning from the imagined idea into a crafted visual world",
            "visual_search_subject": "abstract tunnel with glowing lines cinematic transition",
            "visual_query": "abstract tunnel with glowing lines cinematic transition",
            "narration": "Each transition carries the same idea forward through light and motion.",
            "on_screen": "Possibility becomes motion",
        }, 29109378),
    ],
)
def test_launch_focal_scenes_choose_matching_city_or_abstract_motion_source(
    monkeypatch, tmp_path, scene, expected_source_id
):
    monkeypatch.setattr(studio, "FAST_MODE", False)
    monkeypatch.setattr(studio, "_visual_candidate_relevant", lambda *_args: False)
    payload = (f"source-{expected_source_id}-verified-motion-bytes\n".encode() * 1400)

    def fake_download(url, path, **_kwargs):
        assert str(expected_source_id) in url, f"focal source mismatch: {url}"
        Path(path).write_bytes(payload)

    monkeypatch.setattr(studio, "download", fake_download)
    monkeypatch.setattr(studio, "ffprobe_json", lambda _path: {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
    })
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 8.0)
    monkeypatch.setattr(studio, "_source_has_prolonged_black_frames", lambda *_args, **_kwargs: False)

    selected = studio._curated_pexels_launch_video(scene, tmp_path, index=99)

    assert selected is not None
    assert selected["source_id"] == expected_source_id
    assert selected["kind"] == "video"
    assert selected["validated_media"] is True
    assert selected["quality_tier"] == "curated_free_source_motion_video"
    assert hashlib.sha256(Path(selected["path"]).read_bytes()).hexdigest() == hashlib.sha256(payload).hexdigest()
    assert Path(selected["path"]).is_file()


def test_launch_signature_endcard_uses_real_screen_motion_source(monkeypatch, tmp_path):
    monkeypatch.setattr(studio, "FAST_MODE", False)
    monkeypatch.setattr(studio, "_visual_candidate_relevant", lambda *_args: False)
    payload = b"genuine-metadata-validated-screen-motion-source-bytes\n" * 1400

    def fake_download(url, path, **_kwargs):
        assert any(str(source_id) in url for source_id in (15517230, 35820856, 12358562)), url
        Path(path).write_bytes(payload)

    monkeypatch.setattr(studio, "download", fake_download)
    monkeypatch.setattr(studio, "ffprobe_json", lambda _path: {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
    })
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 8.0)
    monkeypatch.setattr(studio, "_source_has_prolonged_black_frames", lambda *_args, **_kwargs: False)

    scene = {
        "topic": "AI Infinity — One Idea. Infinite Worlds.",
        "heading": "Signature Ending",
        "campaign_profile": "ai_infinity_launch",
        "visual_focus": (
            "Restrained midnight-blue brand end-card displayed on a large dark monitor, "
            "subtle cyan and violet light, clean negative space, elegant AI Infinity title reveal"
        ),
        "visual_search_subject": "minimal dark cinematic brand end card",
        "visual_query": "minimal dark cinematic brand end card; display the finished title on screen",
        "narration": "AI Infinity. One idea. Infinite worlds.",
        "on_screen": "AI INFINITY • ONE IDEA. INFINITE WORLDS.",
    }

    selected = studio._curated_pexels_launch_video(scene, tmp_path, index=99)

    assert selected is not None
    assert selected["source_id"] in {15517230, 35820856, 12358562}
    assert selected["kind"] == "video"
    assert selected["validated_media"] is True
    assert selected["quality_tier"] == "curated_free_source_motion_video"
    assert hashlib.sha256(Path(selected["path"]).read_bytes()).hexdigest() == hashlib.sha256(payload).hexdigest()
