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


def test_strict_visual_gate_keeps_qualified_illustrative_fallback_review_required(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_INFINITY_REQUIRE_SOURCE_VISUALS", "1")
    monkeypatch.setattr(studio, "_hf_video", lambda *a, **k: None)
    monkeypatch.setattr(studio, "_hf_image", lambda *a, **k: None)

    def no_remote(*args, **kwargs):
        return []

    for name in (
        "_nasa_images", "_openverse_images", "_commons_media", "_pexels", "_pixabay",
        "_pexels_photos", "_pixabay_images", "_loc_images",
        "_curated_pexels_launch_video", "_curated_pexels_editorial_photo",
    ):
        monkeypatch.setattr(studio, name, no_remote)

    result = studio.acquire_scene_asset(
        {
            "heading": "Gate test",
            "visual_query": "professional technology workspace",
            "image_prompt": "professional editorial technology workspace",
        },
        Path(tmp_path), 1, prefer_motion=False, duration=5,
    )

    # A qualified original illustration may be retained as a degraded draft,
    # but is never relabelled as professional source footage.
    import production_closure_3624 as closure

    assert result.get("fallback") is True
    assert result.get("quality_tier") == "original_vector_editorial_fallback"
    assert result.get("asset_kind") == "illustrative_graphic_not_photograph"
    assert result.get("fallback_record", {}).get("execution_state") == "DEGRADED"
    assert result.get("fallback_record", {}).get("truthful") is True
    assert closure._is_qualified_illustrative_fallback(result) is True

def test_final_audio_master_pads_early_ended_audio_stream_to_video_duration(tmp_path):
    """A 4s video with 2s audio must be mastered with aligned audio, not delivered short."""
    import shutil
    import subprocess

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        import pytest
        pytest.skip("Real FFmpeg stream regression runs only on media-enabled workers")
    source = tmp_path / "short_audio_source.mp4"
    mastered = tmp_path / "duration_aligned.mp4"
    subprocess.run(
        [
            ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=navy:s=320x180:r=24:d=4",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-map", "0:v:0", "-map", "1:a:0", "-t", "4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", str(source),
        ],
        check=True,
        timeout=60,
    )

    source_probe = studio.ffprobe_json(source)
    source_duration = float((source_probe.get("format") or {}).get("duration") or 0)
    source_audio = next(row for row in source_probe["streams"] if row.get("codec_type") == "audio")
    source_audio_duration = float(source_audio.get("duration") or 0)
    assert 3.8 <= source_duration <= 4.2, source_probe
    assert 0 < source_audio_duration < 3.0, source_probe

    result = studio._normalize_delivery_loudness(source, mastered, fast_mode=True)

    output_probe = studio.ffprobe_json(mastered)
    output_duration = float((output_probe.get("format") or {}).get("duration") or 0)
    output_audio = next(row for row in output_probe["streams"] if row.get("codec_type") == "audio")
    audio_duration = float(result["audio_duration_seconds"])
    assert 3.8 <= output_duration <= 4.2, output_probe
    assert abs(audio_duration - output_duration) <= 0.75, (audio_duration, output_duration, output_probe)
    assert result["audio_video_duration_match"] is True
    subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-i", str(mastered), "-f", "null", "-"],
        check=True,
        timeout=60,
    )

def test_final_delivery_loudness_master_applies_post_normalization_sample_peak_ceiling(monkeypatch, tmp_path):
    source = tmp_path / "programme.mp4"
    output = tmp_path / "mastered.mp4"
    source.write_bytes(b"source-programme-bytes" * 1000)
    captured = {}

    probe = {
        "format": {"duration": "20.0"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "duration": "20.0"},
            {"codec_type": "audio", "codec_name": "aac", "duration": "20.0", "sample_rate": "48000"},
        ],
    }

    def fake_ffmpeg(*args, **kwargs):
        captured["args"] = [str(value) for value in args]
        output.write_bytes(b"normalized-and-peak-limited-delivery" * 500)

    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(studio, "ffprobe_json", lambda _path: probe)
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 20.0)

    result = studio._normalize_delivery_loudness(source, output, fast_mode=True)
    args = captured["args"]
    audio_filter = args[args.index("-af") + 1]

    assert "loudnorm=I=-16.0:TP=-1.5:LRA=11.0" in audio_filter
    limiter = "alimiter=limit=0.80:attack=5:release=50:level=false"
    assert limiter in audio_filter
    assert audio_filter.index("loudnorm=") < audio_filter.index(limiter), (
        "the hard sample-peak ceiling must run after loudness normalization"
    )
    assert result["status"] == "completed"
