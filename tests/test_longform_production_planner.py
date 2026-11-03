"""Regression tests for duration-aware long-form storyboard expansion."""

import os
from pathlib import Path

os.environ.setdefault("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity-longform-planner-test")
os.environ.setdefault("AI_INFINITY_3601_BACKGROUND", "false")
os.environ.setdefault("AI_INFINITY_FAST_MODE", "1")

import studio_ultimate as studio


def _base_scene_renderer():
    # The application wraps the canonical renderer with the reality/provenance
    # layer at startup. Unit tests for FFmpeg argument construction should call
    # the preserved base implementation; wrapper forwarding is tested separately.
    try:
        import reality_first_3901 as reality
        original = reality._ORIGINALS.get("_render_scene")
        if callable(original):
            return original
    except Exception:
        pass
    return studio._render_scene


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


def test_official_media_reference_catalog_fetches_real_pages_only_for_media_topics(monkeypatch):
    fetched = []
    page_text = " ".join(
        ["Video production workflows include editing, storyboarding, accessibility, captions, audio and delivery standards."]
        * 14
    )

    def fake_http_get(url, timeout=8, max_bytes=500_000):
        fetched.append(url)
        return (
            "<html><head><title>Official media production reference</title></head>"
            "<body><main>" + page_text + "</main></body></html>"
        ).encode("utf-8")

    monkeypatch.setattr(studio, "http_get", fake_http_get)
    rows = studio._longform_media_reference_sources("video editing captions and accessibility")
    assert len(rows) == len(studio._LONGFORM_MEDIA_REFERENCE_CATALOG)
    assert len({row["url"] for row in rows}) == len(rows)
    assert len({studio.urlparse(row["url"]).hostname for row in rows}) >= 8
    assert all(row["source"] == "official_media_reference_catalog" for row in rows)
    assert all(len(row["summary"].split()) >= 80 for row in rows)
    assert len(fetched) == len(rows)
    assert studio._longform_media_reference_sources("ancient Roman architecture") == []


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
    from urllib.parse import parse_qs, urlparse

    search_page = {
        "pageid": 12345,
        "title": "File:Creative-workshop.webm",
        "canonicalurl": "https://commons.wikimedia.org/wiki/File:Creative-workshop.webm",
    }
    video_page = {
        **search_page,
        "videoinfo": [{
            "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Creative-workshop.webm",
            "mime": "video/webm",
            "extmetadata": {
                "LicenseShortName": {"value": "CC BY 4.0"},
                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
                "Artist": {"value": "Example Contributor"},
                "ImageDescription": {"value": "A real workshop in motion"},
                "Credit": {"value": "Example Contributor"},
            },
            "derivatives": [{
                "src": "https://upload.wikimedia.org/wikipedia/commons/transcoded/a/ab/Creative-workshop.webm/Creative-workshop.720p.vp9.webm",
                "type": "video/webm; codecs=vp9, opus",
                "transcodekey": "720p.vp9.webm",
                "width": 1280,
                "height": 720,
                "bandwidth": 1600000,
            }],
        }],
    }
    requests = []

    def fake_http_json(url, *args, **kwargs):
        params = parse_qs(urlparse(url).query)
        requests.append((url, params))
        if "generator" in params:
            return {"query": {"pages": {"12345": search_page}}}
        assert params.get("pageids") == ["12345"]
        return {"query": {"pages": {"12345": video_page}}}

    def fake_download(url, path, headers=None, timeout=60, max_bytes=40_000_000):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-motion-file" * 300)
        return path

    monkeypatch.setattr(studio, "FAST_MODE", True)
    monkeypatch.setitem(studio._commons_media.__globals__, "http_json", fake_http_json)
    monkeypatch.setitem(studio._commons_media.__globals__, "download", fake_download)
    monkeypatch.setattr(studio, "_commercial_use_from_license", lambda license_name, license_url: "commercial_use_allowed_with_attribution")
    studio.MEDIA_DEBUG_ERRORS.clear()

    results = studio._commons_media(
        "people collaborating on creative work",
        tmp_path,
        limit=6,
        prefer_motion=True,
    )

    assert len(requests) == 2, {"requests": requests, "provider_errors": dict(studio.MEDIA_DEBUG_ERRORS)}
    assert "filetype%3Avideo" in requests[0][0]
    assert requests[0][1].get("prop") == ["info"]
    assert requests[1][1].get("prop") == ["info|videoinfo"]
    assert "derivatives" in requests[1][1].get("viprop", [""])[0]
    assert len(results) == 1
    asset = results[0]
    assert asset["kind"] == "video"
    assert Path(asset["path"]).suffix == ".webm"
    assert asset["source_id"] == 12345
    assert asset["source_url"] == search_page["canonicalurl"]
    assert asset["downloaded_media_url"].endswith("720p.vp9.webm")
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

    _base_scene_renderer()(
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
    music_position = next(i for i, value in enumerate(args) if str(value) == str(music))
    assert args[music_position - 1] == "-i"
    assert args[music_position - 2] == "125.500"
    assert args[music_position - 3] == "-ss"
    assert args[music_position - 5:music_position - 3] == ["-stream_loop", "-1"]



def test_openverse_music_picker_requires_valid_license_source_and_decodable_audio(monkeypatch, tmp_path):
    item = {
        "id": "music-source-uuid-01",
        "url": "https://media.example.org/music-theme.mp3",
        "foreign_landing_url": "https://example.org/music-theme",
        "title": "Cinematic ambient theme",
        "creator": "Example Composer",
        "license": "by",
        "license_version": "4.0",
        "license_name": "CC BY",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "filetype": "mp3",
        "duration": 120,
    }
    monkeypatch.setattr(studio, "http_json", lambda *args, **kwargs: {"results": [item]})
    monkeypatch.setattr(studio, "_commercial_use_from_license", lambda *_: "commercial_use_allowed_with_attribution")

    def fake_download(url, path, headers=None, timeout=60, max_bytes=40_000_000):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-music-audio" * 3000)
        return path

    monkeypatch.setattr(studio, "download", fake_download)
    monkeypatch.setattr(studio, "ffprobe_json", lambda path: {
        "format": {"duration": "120.0"},
        "streams": [{"codec_type": "audio", "codec_name": "mp3", "sample_rate": "44100", "channels": 2}],
    })
    monkeypatch.setattr(studio, "ffmpeg", lambda *args, **kwargs: None)

    tracks = studio._openverse_music_tracks("cinematic ambient instrumental", tmp_path, limit=1)

    assert len(tracks) == 1
    assert tracks[0]["source_id"] == item["id"]
    assert tracks[0]["rights_status"] == "license_metadata_present"
    assert tracks[0]["license_url"] == item["license_url"]
    assert tracks[0]["creator"] == "Example Composer"
    assert Path(tracks[0]["path"]).is_file()
    assert len(tracks[0]["asset_sha256"]) == 64


def test_music_builder_crossfades_tracks_and_writes_source_manifest(monkeypatch, tmp_path):
    track_a = tmp_path / "licensed-a.mp3"
    track_b = tmp_path / "licensed-b.mp3"
    track_a.write_bytes(b"a" * 1024)
    track_b.write_bytes(b"b" * 1024)
    tracks = [
        {"path": str(track_a), "source_id": "a", "title": "A", "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/", "duration_seconds": 60.0},
        {"path": str(track_b), "source_id": "b", "title": "B", "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/", "duration_seconds": 60.0},
    ]
    captured = {}

    def fake_ffmpeg(*args, **kwargs):
        captured["args"] = list(args)
        out = args[-1]
        if isinstance(out, Path):
            out.write_bytes(b"composed-music" * 3000)

    monkeypatch.setattr(studio, "_openverse_music_tracks", lambda *args, **kwargs: tracks)
    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(studio, "probe_duration", lambda *_: 90.0)

    output = studio.make_music(tmp_path, 90.0, query="quiet cinematic instrumental")

    manifest = __import__("json").loads((tmp_path / "music_source.json").read_text(encoding="utf-8"))
    command = " ".join(str(value) for value in captured["args"])
    assert output.is_file()
    assert "acrossfade=d=2" in command
    assert "aloop=loop=-1" in command
    assert manifest["method"] == "openverse_openly_licensed_tracks_crossfaded_and_arranged"
    assert [track["source_id"] for track in manifest["tracks"]] == ["a", "b"]
    assert manifest["requested_duration_seconds"] == 90.0
    assert len(manifest["output_sha256"]) == 64



def test_creative_planner_repairs_an_underwritten_15_minute_script():
    import json

    calls = []

    def short_plan():
        return {
            "title": "The Future of Human Creativity",
            "hook": "Every meaningful work begins with a question.",
            "premise": "A grounded documentary about how people make things.",
            "chapters": [
                {
                    "heading": f"Chapter {index}",
                    "narration": f"Chapter {index} introduces an important idea and explains why it matters.",
                    "visual_query": f"documentary creative work chapter {index}",
                    "image_prompt": f"Human creativity documentary shot in chapter {index}",
                    "on_screen": "",
                    "duration": 100,
                    "proof_needed": [],
                }
                for index in range(1, 6)
            ],
        }

    def complete_plan():
        plan = short_plan()
        for chapter_index, chapter in enumerate(plan["chapters"], 1):
            chapter["narration"] = " ".join(
                f"In chapter {chapter_index}, observation {n} follows a creator through a specific decision, a real constraint, a failed attempt, and a meaningful improvement."
                for n in range(1, 28)
            )
        return plan

    def model_fn(payload):
        calls.append(payload)
        plan = short_plan() if len(calls) == 1 else complete_plan()
        return {"text": json.dumps(plan), "provider": "test-longform-writer"}

    plan, provider = studio.creative_plan(
        {
            "title": "The Future of Human Creativity",
            "objective": "Create a grounded long-form documentary about the future of human creativity.",
            "topic": "human creativity",
            "format": "long",
            "duration": 900,
            "language": "English",
            "tone": "curious, cinematic, compassionate",
        },
        {"sources": []},
        model_fn,
    )

    word_count = sum(
        len(chapter["narration"].split())
        for chapter in plan["chapters"]
    )
    assert len(calls) == 2
    assert "TARGET SPOKEN WORD COUNT" in calls[0]["messages"][0]["content"]
    assert "Expand the existing story" in calls[1]["messages"][0]["content"]
    assert word_count >= 1800
    assert provider == "test-longform-writer"



def test_scene_renderer_does_not_burn_generic_heading_when_no_overlay_is_requested(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(studio, "FAST_MODE", True)

    def fake_ffmpeg(*args, **kwargs):
        captured["args"] = list(args)

    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    _base_scene_renderer()(
        {"kind": "video", "path": str(tmp_path / "source.webm")},
        tmp_path / "voice.mp3",
        tmp_path / "music.m4a",
        tmp_path / "sfx.wav",
        6.0,
        tmp_path / "scene.mp4",
        "",
        "16:9",
    )
    args = captured["args"]
    filter_graph = args[args.index("-vf") + 1]
    assert "color=black@0.0:t=fill" in filter_graph
    assert "text=''" in filter_graph
    assert "Chapter" not in filter_graph



def test_reality_render_wrapper_forwards_programme_music_offset(monkeypatch, tmp_path):
    import reality_first_3901 as reality

    wrapper = studio._render_scene
    if not getattr(wrapper, "_reality_first_wrapped", False):
        return

    captured = {}

    def fake_safe_render(
        original, asset, voice, music, sfx, duration, out, title,
        aspect_ratio, attempt, music_offset_seconds=0.0,
    ):
        captured["offset"] = music_offset_seconds
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"valid-test-artifact" * 2000)

    monkeypatch.setattr(reality, "_safe_render", fake_safe_render)
    monkeypatch.setattr(reality, "_upsert_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr(reality, "_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(reality, "_file_hash", lambda *_: "a" * 64)

    wrapper(
        {"kind": "video", "path": str(tmp_path / "source.webm")},
        tmp_path / "voice.mp3",
        tmp_path / "music.m4a",
        tmp_path / "sfx.wav",
        10.0,
        tmp_path / "scene.mp4",
        "",
        "16:9",
        music_offset_seconds=42.25,
    )

    assert captured["offset"] == 42.25



def test_creative_planner_retries_underwritten_repairs_until_the_full_rate_target_is_met():
    import json

    calls = []

    def plan_with_words(words_per_chapter):
        plan = {
            "title": "The Future of Human Creativity",
            "premise": "A grounded documentary about human creativity.",
            "chapters": [],
        }
        for chapter_index in range(1, 6):
            narration = " ".join(
                f"Chapter {chapter_index} observation {n} explains a distinct decision, constraint, cause, effect, example, and practical implication."
                for n in range(1, words_per_chapter + 1)
            )
            plan["chapters"].append({
                "heading": f"Chapter {chapter_index}",
                "narration": narration,
                "visual_query": f"documentary human creativity chapter {chapter_index}",
                "image_prompt": f"Distinct documentary visual for chapter {chapter_index}",
                "on_screen": "",
                "duration": 180,
                "proof_needed": [],
            })
        return plan

    def model_fn(payload):
        calls.append(payload)
        plans = [
            plan_with_words(2),   # Initial short outline.
            plan_with_words(12),  # Better, but still below the 125-WPM target.
            plan_with_words(22),  # Better again, but still below the target.
            plan_with_words(32),  # Complete narration.
        ]
        selected = plans[min(len(calls) - 1, len(plans) - 1)]
        return {"text": json.dumps(selected), "provider": "test-writer"}

    plan, provider = studio.creative_plan(
        {
            "title": "The Future of Human Creativity",
            "objective": "Create a grounded 15-minute documentary about human creativity.",
            "topic": "human creativity",
            "format": "long",
            "duration": 900,
            "speaking_rate_wpm": 125,
            "language": "English",
            "tone": "curious, cinematic, compassionate",
        },
        {"sources": []},
        model_fn,
    )

    spoken_words = studio._narration_word_count(plan["chapters"])
    assert len(calls) == 4
    assert spoken_words >= 1875
    assert plan["script_generation"]["status"] == "complete"
    assert plan["script_generation"]["required_spoken_words"] == 1875
    assert plan["script_generation"]["speaking_rate_wpm"] == 125
    assert provider == "test-writer"


def test_builtin_longform_outline_is_explicitly_marked_underwritten(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACEHUB_API_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    plan, provider = studio.creative_plan(
        {
            "title": "A grounded documentary about human creativity",
            "objective": "Create a 15-minute documentary about human creativity.",
            "topic": "human creativity",
            "format": "long",
            "duration": 900,
            "language": "English",
        },
        {"sources": []},
        None,
    )
    assert provider == "builtin-creative-engine"
    assert plan["script_generation"]["status"] == "underwritten"
    assert plan["script_generation"]["actual_spoken_words"] < plan["script_generation"]["required_spoken_words"]


def test_google_gemini_writer_requires_free_tier_and_data_use_acknowledgement(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK", "false")
    assert studio._gemini_free_tier_text_model() is None


def test_creative_planner_uses_acknowledged_gemini_free_tier_for_15_minute_script(monkeypatch):
    import json
    from urllib.request import Request as URLRequest

    monkeypatch.delenv("AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACEHUB_API_TOKEN", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-free-tier-key")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK", "true")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_MODEL", "gemini-3.1-flash-lite")

    calls = []

    def plan_with_words(words_per_chapter):
        chapters = []
        for chapter_index in range(1, 6):
            sentences = [
                (
                    f"Chapter {chapter_index} examines distinct production decision {sentence_index}. "
                    f"It compares the constraint, the evidence, the trade-off, and the result "
                    f"for a different part of the creative workflow."
                )
                for sentence_index in range(1, words_per_chapter + 1)
            ]
            chapters.append({
                "heading": f"Chapter {chapter_index}",
                "narration": " ".join(sentences),
                "visual_query": f"distinct documentary visual for production chapter {chapter_index}",
                "image_prompt": f"cinematic documentary shot about production chapter {chapter_index}",
                "on_screen": f"Chapter {chapter_index}",
                "duration": 180,
                "proof_needed": [],
            })
        return {
            "title": "A Real 15-Minute Production Workflow",
            "premise": "A researched, chaptered production documentary.",
            "chapters": chapters,
        }

    class FakeResponse:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "candidates": [{
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": json.dumps(self.value)}]},
                }],
                "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 2400},
            }).encode("utf-8")

    def fake_urlopen(request, timeout=0):
        assert isinstance(request, URLRequest)
        assert "gemini-3.1-flash-lite:generateContent" in request.full_url
        assert "test-free-tier-key" not in request.full_url
        body = json.loads(request.data.decode("utf-8"))
        user_text = body["contents"][0]["parts"][0]["text"]
        calls.append(user_text)
        value = plan_with_words(5) if len(calls) == 1 else plan_with_words(42)
        return FakeResponse(value)

    monkeypatch.setattr(studio, "urlopen", fake_urlopen)
    plan, provider = studio.creative_plan(
        {
            "title": "A Real 15-Minute Production Workflow",
            "objective": "Create a grounded 15-minute documentary about the actual production workflow.",
            "topic": "professional production workflow",
            "format": "long",
            "duration": 900,
            "speaking_rate_wpm": 125,
            "language": "English",
        },
        {"sources": []},
        None,
    )

    assert len(calls) == 2
    assert "TARGET SPOKEN WORD COUNT" in calls[0]
    assert "Expand the existing story" in calls[1]
    assert provider == "google-gemini-free-tier:gemini-3.1-flash-lite"
    assert plan["script_generation"]["status"] == "complete"
    assert plan["script_generation"]["required_spoken_words"] == 1875
    assert plan["script_generation"]["actual_spoken_words"] >= 1875
    assert plan["script_generation"]["repair_passes"] == 1


def test_google_gemini_free_tier_writer_sends_json_mode_and_keeps_key_out_of_url(monkeypatch):
    import json

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK", "true")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_MODEL", "gemini-3.1-flash-lite")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "candidates": [{
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": "{\"chapters\": []}"}]},
                }],
                "usageMetadata": {"totalTokenCount": 100},
            }).encode("utf-8")

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(studio, "urlopen", fake_urlopen)
    writer = studio._gemini_free_tier_text_model()
    assert callable(writer)
    result = writer({
        "messages": [
            {"role": "system", "content": "Return only JSON."},
            {"role": "user", "content": "Write a test blueprint."},
        ],
        "temperature": 0.4,
    })
    assert result["provider"] == "google-gemini-free-tier:gemini-3.1-flash-lite"
    assert result["text"] == "{\"chapters\": []}"
    assert "test-key" not in captured["url"]
    assert captured["headers"].get("X-goog-api-key") == "test-key"
    assert captured["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert captured["body"]["generationConfig"]["maxOutputTokens"] == 8192
    assert captured["timeout"] == 120
