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

