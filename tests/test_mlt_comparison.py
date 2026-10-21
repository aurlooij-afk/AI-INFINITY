from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path


def _run(command: list[str], timeout: int = 120) -> float:
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    elapsed = time.monotonic() - started
    if result.returncode:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-4000:]}"
        )
    return round(elapsed, 3)


def _probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mean_rgb(path: Path, timestamp: float, width: int, height: int) -> tuple[float, float, float]:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(timestamp),
            "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ],
        capture_output=True,
        timeout=30,
    )
    pixels = width * height
    assert result.returncode == 0, result.stderr[-1000:]
    assert len(result.stdout) == pixels * 3, f"sample frame is incomplete: {len(result.stdout)} bytes"
    raw = result.stdout
    return (
        sum(raw[0::3]) / pixels,
        sum(raw[1::3]) / pixels,
        sum(raw[2::3]) / pixels,
    )


def _assert_red(rgb: tuple[float, float, float]) -> None:
    red, green, blue = rgb
    assert red > green * 1.4 and red > blue * 1.4, f"expected red scene, got mean RGB={rgb}"


def _assert_blue(rgb: tuple[float, float, float]) -> None:
    red, green, blue = rgb
    assert blue > red * 1.4 and blue > green * 1.4, f"expected blue scene, got mean RGB={rgb}"


def test_mlt_and_ffmpeg_render_same_two_scene_reference(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    melt = shutil.which("melt")
    assert ffmpeg and ffprobe and melt, "CI must install FFmpeg, FFprobe and MLT's melt CLI"

    mlt_output = tmp_path / "mlt-two-scene.mp4"
    ffmpeg_output = tmp_path / "ffmpeg-two-scene.mp4"
    mlt_elapsed = _run(
        [
            melt, "-profile", "atsc_720p_25",
            "color:red", "out=29", "color:blue", "out=29",
            "-consumer", f"avformat:{mlt_output}",
            "vcodec=libx264", "preset=ultrafast", "crf=30",
        ]
    )
    ffmpeg_elapsed = _run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=1280x720:r=25:d=1.2",
            "-f", "lavfi", "-i", "color=c=blue:s=1280x720:r=25:d=1.2",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
            "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            str(ffmpeg_output),
        ]
    )

    records = {}
    for label, path in (("mlt", mlt_output), ("ffmpeg", ffmpeg_output)):
        assert path.is_file() and path.stat().st_size > 5_000, (label, path.stat().st_size if path.exists() else None)
        media = _probe(path)
        streams = media.get("streams") or []
        video = next((row for row in streams if row.get("codec_type") == "video"), {})
        duration = float((media.get("format") or {}).get("duration") or 0)
        assert video.get("codec_name") == "h264", (label, media)
        assert (int(video.get("width") or 0), int(video.get("height") or 0)) == (1280, 720), (label, media)
        assert abs(duration - 2.4) <= 0.2, (label, media)
        for timestamp, color_check in ((0.4, _assert_red), (1.8, _assert_blue)):
            color_check(_mean_rgb(path, timestamp, 1280, 720))
        decoded = subprocess.run(
            [ffmpeg, "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        assert decoded.returncode == 0, (label, decoded.stderr[-2000:])
        records[label] = {
            "sha256": _sha(path),
            "size_bytes": path.stat().st_size,
            "duration_seconds": round(duration, 4),
            "resolution": [1280, 720],
            "codec": video.get("codec_name"),
            "full_decode_passed": True,
        }

    assert records["mlt"]["sha256"] != records["ffmpeg"]["sha256"]
    evidence = {
        "benchmark": "synthetic-two-scene-reference-v1",
        "scope": "renderer smoke comparison, not a full AI Infinity production benchmark",
        "profiles": {"width": 1280, "height": 720, "fps": 25, "duration_seconds": 2.4},
        "mlt_elapsed_seconds": mlt_elapsed,
        "ffmpeg_elapsed_seconds": ffmpeg_elapsed,
        "outputs": records,
        "scene_order_and_content_verified": True,
        "human_editorial_quality_reviewed": False,
    }
    print("MLT_FFMPEG_BENCHMARK=" + json.dumps(evidence, sort_keys=True))
