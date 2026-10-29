"""Regression tests for duration-aware long-form storyboard expansion."""

import os
from pathlib import Path

os.environ.setdefault("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity-longform-planner-test")
os.environ.setdefault("AI_INFINITY_3601_BACKGROUND", "false")
os.environ.setdefault("AI_INFINITY_FAST_MODE", "1")

import studio_ultimate as studio


def _chapter(index: int, sentence_count: int = 10) -> dict:
    sentences = [
        f"Chapter {index} observation {n} reveals a specific human detail about creative work."
        for n in range(1, sentence_count + 1)
    ]
    return {
        "heading": f"Chapter {index}",
        "narration": " ".join(sentences),
        "visual_query": f"people developing creative ideas in workshop {index}",
        "image_prompt": f"Cinematic documentary footage of a creative workshop, chapter {index}",
        "on_screen": f"Chapter {index}",
        "duration": 100.0,
    }


def test_longform_scene_budget_scales_to_15_minutes_without_fast_mode_truncation():
    assert studio._max_scene_count(
        is_short=False,
        fast_mode=True,
        smoke=False,
        explicitly_single_scene=False,
        requested_duration_seconds=900,
    ) == 90
    assert studio._max_scene_count(
        is_short=False,
        fast_mode=False,
        smoke=False,
        explicitly_single_scene=False,
        requested_duration_seconds=3600,
    ) == 180


def test_longform_expansion_preserves_every_narrated_word_in_order_and_varies_shots():
    source = [_chapter(i, 10) for i in range(1, 10)]
    result = studio._expand_longform_chapters(source, 900, 90)

    assert len(result) == 90
    original = " ".join(ch["narration"] for ch in source)
    expanded = " ".join(ch["narration"] for ch in result)
    assert " ".join(original.split()) == " ".join(expanded.split())
    assert all(ch.get("longform_expansion") is True for ch in result)
    assert len({ch["visual_query"] for ch in result}) == len(result)
    assert sum(float(ch["duration"]) for ch in result) == 900.0
    assert sum(bool(ch.get("on_screen")) for ch in result) == len(source)


def test_longform_expansion_does_not_duplicate_narration_to_fill_shot_budget():
    source = [_chapter(i, 3) for i in range(1, 7)]
    result = studio._expand_longform_chapters(source, 900, 90)

    assert result
    assert len(result) < 90
    assert all(ch["narration"].strip() for ch in result)
    original = " ".join(ch["narration"] for ch in source)
    expanded = " ".join(ch["narration"] for ch in result)
    assert " ".join(original.split()) == " ".join(expanded.split())


def test_short_and_explicit_single_shot_plans_keep_existing_behavior():
    source = [_chapter(1, 5), _chapter(2, 5)]
    assert len(studio._expand_longform_chapters(source, 60, 8)) == 2
    assert studio._max_scene_count(
        is_short=False,
        fast_mode=False,
        smoke=True,
        explicitly_single_scene=False,
        requested_duration_seconds=900,
    ) == 1
    assert studio._max_scene_count(
        is_short=False,
        fast_mode=False,
        smoke=False,
        explicitly_single_scene=False,
        explicit_launch=True,
        requested_duration_seconds=60,
    ) == 9



def test_longform_narration_splitter_creates_shot_sized_units_without_losing_words():
    sentence = "A creator works through the night, tests a risky idea, learns from the first failure, and discovers a more honest way to tell the story."
    source = " ".join([sentence] * 8)
    units = studio._longform_narration_units(source)

    assert len(units) >= 8
    assert max(len(unit.split()) for unit in units) <= 24
    assert " ".join(" ".join(units).split()) == " ".join(source.split())


def test_commons_motion_first_search_keeps_real_video_extension_and_license(monkeypatch, tmp_path):
    video_page = {
        "pageid": 12345,
        "title": "File:Creative-workshop.webm",
        "canonicalurl": "https://commons.wikimedia.org/wiki/File:Creative-workshop.webm",
        "imageinfo": [{
            "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Creative-workshop.webm",
            "mime": "video/webm",
            "extmetadata": {
                "LicenseShortName": {"value": "CC BY 4.0"},
                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
                "Artist": {"value": "Example Contributor"},
                "ImageDescription": {"value": "A real workshop in motion"},
                "Credit": {"value": "Example Contributor"},
            },
        }],
    }
    still_page = {
        "pageid": 67890,
        "title": "File:Creative-workshop.jpg",
        "canonicalurl": "https://commons.wikimedia.org/wiki/File:Creative-workshop.jpg",
        "imageinfo": [{
            "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Creative-workshop.jpg",
            "mime": "image/jpeg",
            "extmetadata": {
                "LicenseShortName": {"value": "CC BY 4.0"},
                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
                "Artist": {"value": "Example Contributor"},
            },
        }],
    }
    requests = []

    def fake_http_json(url, *args, **kwargs):
        requests.append(url)
        return {"query": {"pages": {"12345": video_page, "67890": still_page}}}

    def fake_download(url, path, headers=None, timeout=60, max_bytes=40_000_000):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-motion-file" * 300)
        return path

    monkeypatch.setattr(studio, "FAST_MODE", True)
    monkeypatch.setattr(studio, "http_json", fake_http_json)
    monkeypatch.setattr(studio, "download", fake_download)
    monkeypatch.setattr(studio, "_commercial_use_from_license", lambda license_name, license_url: "commercial_use_allowed_with_attribution")

    results = studio._commons_media(
        "people collaborating on creative work",
        tmp_path,
        limit=6,
        prefer_motion=True,
    )

    assert requests and "filetype%3Avideo" in requests[0]
    assert len(results) == 1
    asset = results[0]
    assert asset["kind"] == "video"
    assert Path(asset["path"]).suffix == ".webm"
    assert asset["source_id"] == 12345
    assert asset["source_url"] == video_page["canonicalurl"]
    assert asset["license"] == "CC BY 4.0"
    assert asset["license_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert asset["rights_status"] == "license_metadata_present"



def test_scene_renderer_offsets_shared_music_to_the_scene_programme_time(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(studio, "FAST_MODE", True)

    def fake_ffmpeg(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs

    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    source = tmp_path / "source.webm"
    voice = tmp_path / "voice.mp3"
    music = tmp_path / "shared-music.m4a"
    sfx = tmp_path / "sfx.wav"
    output = tmp_path / "scene.mp4"

    studio._render_scene(
        {"kind": "video", "path": str(source)},
        voice,
        music,
        sfx,
        10.0,
        output,
        "A meaningful scene",
        "16:9",
        music_offset_seconds=125.5,
    )

    args = captured["args"]
    music_position = args.index(str(music))
    assert args[music_position - 1] == "-i"
    assert args[music_position - 3] == "125.500"
    assert args[music_position - 4] == "-ss"
    assert args[music_position - 5:music_position - 3] == ["-1", "-ss"] or args[music_position - 5:music_position - 3] == ["-stream_loop", "-1"]
