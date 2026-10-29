"""Regression tests for duration-aware long-form storyboard expansion."""

import os

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
