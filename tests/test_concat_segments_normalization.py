"""Reproduce mixed scene dimensions/audio and prove assembly emits decodable 1080p."""
import os
os.environ["AI_INFINITY_FAST_MODE"] = "0"

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from studio_ultimate import concat_segments, ffprobe_json, probe_duration


def make_scene(path: Path, width: int, height: int, fps: int, sample_rate: int, channels: int, hz: int, seconds: float):
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c={['red','blue','green'][hz % 3]}:size={width}x{height}:rate={fps}",
        "-f", "lavfi", "-i", f"sine=frequency={hz}:sample_rate={sample_rate}",
        "-t", str(seconds), "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ar", str(sample_rate), "-ac", str(channels),
        str(path),
    ]
    run = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr[-2000:]


def test_mixed_resolution_and_aac_scenes_assemble_to_decodable_1080p(tmp_path=None):
    with TemporaryDirectory(prefix="aii-concat-regression-") as temp:
        root = Path(temp)
        scenes = [
            root / "scene_01.mp4",
            root / "scene_02.mp4",
            root / "scene_03.mp4",
        ]
        make_scene(scenes[0], 1920, 1080, 24, 44100, 1, 440, 1.2)
        make_scene(scenes[1], 1280, 720, 24, 48000, 2, 660, 1.25)
        make_scene(scenes[2], 1920, 1080, 30, 44100, 1, 880, 1.1)

        output = root / "master.mp4"
        concat_segments(scenes, output)
        probe = ffprobe_json(output)
        streams = probe.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
        duration = probe_duration(output)
        assert (video.get("width"), video.get("height")) == (1920, 1080), probe
        assert video.get("codec_name") == "h264", probe
        assert audio.get("codec_name") == "aac", probe
        assert abs(duration - sum((1.2, 1.25, 1.1))) <= 1.0, duration
        decode = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-xerror", "-i", str(output), "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        assert decode.returncode == 0, decode.stderr[-3000:]


if __name__ == "__main__":
    test_mixed_resolution_and_aac_scenes_assemble_to_decodable_1080p()
    print("PASS: mixed 720p/1080p and AAC scenes assemble into a full-decode 1080p master.")
