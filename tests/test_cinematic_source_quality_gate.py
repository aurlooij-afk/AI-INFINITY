"""Regression coverage for strict source-motion requirements on professional video jobs."""
import hashlib
import json
from pathlib import Path

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
