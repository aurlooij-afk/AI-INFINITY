from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from ai_infinity.timeline_interchange import (
    TimelineInterchangeError,
    _duration_seconds,
    export_otio_bundle,
    import_otio_timeline,
)


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg and FFprobe are required for real-media interchange tests",
)


def _run(*args: str) -> None:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def _project(tmp_path: Path) -> tuple[dict, dict, dict]:
    for filename, color, duration in (
        ("scene_01.mp4", "red", "1.25"),
        ("scene_02.mp4", "blue", "1.50"),
    ):
        _run(
            "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=24:d={duration}",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(tmp_path / filename),
        )
    _run(
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2.8",
        "-ac", "1", "-c:a", "libmp3lame", "-q:a", "9",
        str(tmp_path / "audio_master.mp3"),
    )
    rows = []
    for index, filename in enumerate(("scene_01.mp4", "scene_02.mp4"), 1):
        actual_duration = _duration_seconds(tmp_path / filename)
        trim_start = 0.1 * index
        trim_end = 0.05
        rows.append({
            "source_asset": filename,
            "source_index": index,
            "heading": f"Scene {index}",
            "narration": f"Useful narration for scene {index}.",
            "image_prompt": f"A distinct illustrative test shot {index}.",
            "on_screen": f"Scene {index}",
            "trim_start": trim_start,
            "trim_end": trim_end,
            "duration": actual_duration - trim_start - trim_end,
            "source_duration": actual_duration,
            "enabled": True,
        })
    (tmp_path / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:00,900\nTest captions\n", encoding="utf-8"
    )
    (tmp_path / "script.md").write_text("# Test script\n", encoding="utf-8")
    (tmp_path / "sources.json").write_text(
        json.dumps({"source_count": 2, "sources": []}), encoding="utf-8"
    )
    (tmp_path / "rights.json").write_text(
        json.dumps({"assets": [], "state": "REVIEW_REQUIRED"}), encoding="utf-8"
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"artifacts": []}), encoding="utf-8"
    )
    timeline = {
        "version": "test",
        "project_id": "project-test",
        "title": "Interchange acceptance",
        "fps": 24,
        "duration_seconds": sum(row["duration"] for row in rows),
        "scenes": rows,
    }
    project = {"title": "Interchange acceptance"}
    truth = {
        "state": "REVIEW_REQUIRED",
        "verified": False,
        "publish_allowed_by_quality_gate": False,
    }
    return timeline, truth, project


def test_otio_export_bundle_roundtrips_scene_order_trim_hashes_and_truth(tmp_path: Path) -> None:
    timeline, truth, project = _project(tmp_path)
    result = export_otio_bundle(tmp_path, "project-test", project, timeline, truth)

    assert result["truthful"] is True
    assert result["truth_state"] == "REVIEW_REQUIRED"
    assert result["truth_verified"] is False
    assert result["human_review_status"] == "PENDING"
    assert result["scene_count"] == 2
    assert result["media_files"] == 3
    assert result["timeline_sha256"] == hashlib.sha256(
        Path(result["timeline_path"]).read_bytes()
    ).hexdigest()

    with zipfile.ZipFile(result["bundle_path"]) as archive:
        names = set(archive.namelist())
        assert "project.otio" in names
        assert "media/scene_01.mp4" in names
        assert "media/scene_02.mp4" in names
        assert "media/audio_master.mp3" in names
        assert "metadata/captions.srt" in names
        assert "metadata/sources.json" in names
        assert "metadata/rights.json" in names
        manifest = json.loads(archive.read("metadata/timeline-export-manifest.json"))
        assert manifest["human_review_status"] == "PENDING"
        assert manifest["truth_state"] == "REVIEW_REQUIRED"
        assert {item["path"] for item in manifest["files"]} == {
            "media/scene_01.mp4", "media/scene_02.mp4", "media/audio_master.mp3"
        }
        for item in manifest["files"]:
            content = archive.read(item["path"])
            assert len(content) == item["size_bytes"]
            assert hashlib.sha256(content).hexdigest() == item["sha256"]

    imported = import_otio_timeline(
        Path(result["timeline_path"]).read_bytes(),
        tmp_path,
        "project-test",
        "Interchange acceptance",
    )
    assert imported["truthful"] is True
    assert [scene["source_asset"] for scene in imported["scenes"]] == [
        "scene_01.mp4", "scene_02.mp4"
    ]
    for original, restored in zip(timeline["scenes"], imported["scenes"], strict=True):
        assert abs(original["trim_start"] - restored["trim_start"]) <= 1 / timeline["fps"]
        assert abs(original["duration"] - (
            _duration_seconds(tmp_path / restored["source_asset"])
            - restored["trim_start"] - restored["trim_end"]
        )) <= 1 / timeline["fps"]
        assert restored["heading"] == original["heading"]


def test_otio_import_rejects_path_traversal_and_remote_media(tmp_path: Path) -> None:
    timeline, truth, project = _project(tmp_path)
    result = export_otio_bundle(tmp_path, "project-test", project, timeline, truth)
    original = Path(result["timeline_path"]).read_bytes()

    traversal = original.replace(b"media/scene_01.mp4", b"../../etc/passwd")
    with pytest.raises(TimelineInterchangeError, match="unsafe path|supported scene"):
        import_otio_timeline(traversal, tmp_path, "project-test", "Interchange acceptance")

    remote = original.replace(b"media/scene_01.mp4", b"https://evil.example/scene_01.mp4")
    with pytest.raises(TimelineInterchangeError, match="project-relative|unsafe path|supported scene"):
        import_otio_timeline(remote, tmp_path, "project-test", "Interchange acceptance")


def test_otio_export_fails_closed_for_missing_audio_or_scene(tmp_path: Path) -> None:
    timeline, truth, project = _project(tmp_path)
    (tmp_path / "audio_master.mp3").unlink()
    with pytest.raises(TimelineInterchangeError, match="audio_master.mp3"):
        export_otio_bundle(tmp_path, "project-test", project, timeline, truth)

    _run(
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2.8",
        "-ac", "1", "-c:a", "libmp3lame", "-q:a", "9",
        str(tmp_path / "audio_master.mp3"),
    )
    (tmp_path / "scene_02.mp4").unlink()
    with pytest.raises(TimelineInterchangeError, match="missing"):
        export_otio_bundle(tmp_path, "project-test", project, timeline, truth)


def test_otio_import_rejects_oversized_or_non_utf8_input(tmp_path: Path) -> None:
    with pytest.raises(TimelineInterchangeError, match="2 MiB"):
        import_otio_timeline(b" " * (2 * 1024 * 1024 + 1), tmp_path, "project-test", "Test")
    with pytest.raises(TimelineInterchangeError, match="UTF-8"):
        import_otio_timeline(b"\xff\xfe", tmp_path, "project-test", "Test")


def test_otio_bundle_contains_only_project_relative_media_references(tmp_path: Path) -> None:
    timeline, truth, project = _project(tmp_path)
    result = export_otio_bundle(tmp_path, "project-test", project, timeline, truth)
    payload = Path(result["timeline_path"]).read_text(encoding="utf-8")
    assert "media/scene_01.mp4" in payload
    assert "media/scene_02.mp4" in payload
    assert "file://" not in payload
    assert "https://" not in payload
