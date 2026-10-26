"""Regression checks for the requested AI Infinity cinematic campaign plan."""
import studio_ultimate


BRIEF = """Create an extraordinary 60-second cinematic brand film for AI Infinity titled
ONE IDEA. INFINITE WORLDS. A lone creator looks at a blank screen at night.
The film opens with: Every extraordinary world begins with something nobody else can see.
Research gives the story roots; notes become a storyboard; a script becomes a sequence
of distinct cinematic worlds, including mountains at first light and a futuristic city.
At 40 seconds show real editing, narration, music, captions and an editable timeline.
At 49 seconds the payoff voiceover is: Research it. Shape it. Bring it to life.
Then make it better. At 56 seconds close with: AI Infinity. One idea. Infinite worlds.
Use real sources and preserve provenance and rights evidence."""


def test_creative_plan_uses_the_exact_named_campaign_without_model_rewriting():
    calls = []
    def model_fn(_payload):
        calls.append(True)
        return {"provider": "generic-model", "text": '{"title":"Generic AI Ad","chapters":[]}'}
    plan, provider = studio_ultimate.creative_plan({
        "title": "ONE IDEA. INFINITE WORLDS.",
        "objective": BRIEF,
        "format": "long",
        "duration": 60,
        "aspect_ratio": "16:9",
        "language": "English",
        "content_type": "video",
    }, {"sources": []}, model_fn)
    assert calls == [], "the exact named campaign must not be silently rewritten by a model"
    assert provider == "builtin-creative-engine:one-idea-infinite-worlds", provider
    assert plan["title"] == "ONE IDEA. INFINITE WORLDS.", plan["title"]
    assert plan["hook"] == "Every extraordinary world begins with something nobody else can see.", plan["hook"]
    assert plan["cta"] == "Research it. Shape it. Bring it to life. Then make it better.", plan["cta"]
    assert len(plan["chapters"]) == 8


def test_allocator_preserves_explicit_scene_timecodes_that_sum_to_target():
    authored = [4, 8, 13, 6.5, 8.5, 9, 7, 4]
    chapters = [{"heading": f"Scene {i}", "duration": seconds} for i, seconds in enumerate(authored, 1)]
    allocated = studio_ultimate._allocate_scene_durations(chapters, 60.0)
    actual = [float(chapter["duration"]) for chapter in allocated]
    assert actual == authored, (authored, actual)
    assert abs(sum(actual) - 60.0) <= 0.001


def test_campaign_plan_preserves_requested_story_and_distinct_visual_beats():
    plan = studio_ultimate._fallback_creative_plan(
        title="ONE IDEA. INFINITE WORLDS.",
        objective=BRIEF,
        fmt="long",
        duration=60,
        audience="global creative audience",
        tone="premium, emotional, cinematic",
        research={"sources": []},
        language="English",
        topic="ONE IDEA. INFINITE WORLDS.",
    )
    assert plan["title"] == "ONE IDEA. INFINITE WORLDS.", plan["title"]
    assert plan["hook"] == "Every extraordinary world begins with something nobody else can see.", plan["hook"]
    chapters = plan["chapters"]
    assert len(chapters) == 8, [x.get("heading") for x in chapters]
    assert [x.get("heading") for x in chapters] == [
        "The Unseen Idea", "The Spark", "A Story Takes Shape", "From Sketch to World",
        "The City of Tomorrow", "The Craft", "The Payoff", "The Signature",
    ]
    assert all(x.get("campaign_key") == "one_idea_infinite_worlds_v1" for x in chapters)
    assert [x.get("campaign_scene_index") for x in chapters] == list(range(1, 9))
    narration = [x["narration"] for x in chapters]
    combined = " ".join(narration).lower()
    assert narration[0].startswith("Every extraordinary world begins with something nobody else can see.")
    assert "research gives it roots" in combined
    assert "mountains catch first light" in combined
    assert "research it. shape it. bring it to life. then make it better." in combined
    assert narration[6].lower().endswith(
        "research it. shape it. bring it to life. then make it better."
    ), narration[6]
    assert narration[-1] == "AI Infinity. One idea. Infinite worlds."
    durations = [float(chapter.get("duration") or 0) for chapter in chapters]
    assert abs(sum(durations) - 60) <= 1.0, durations
    focuses = [str(x.get("visual_focus") or "") for x in chapters]
    assert len(set(focuses)) == 8, focuses
    assert any("laptop" in x.lower() and "night" in x.lower() for x in focuses)
    assert any("notebook" in x.lower() and "writing" in x.lower() for x in focuses)
    assert any("mountain" in x.lower() for x in focuses)
    assert any("timeline" in x.lower() for x in focuses)
    assert any("futuristic city skyline" in x.lower() and "spacecraft" in x.lower() for x in focuses)
    assert plan.get("source_context", {}).get("source_claims_invented") is False


if __name__ == "__main__":
    test_creative_plan_uses_the_exact_named_campaign_without_model_rewriting()
    test_allocator_preserves_explicit_scene_timecodes_that_sum_to_target()
    test_campaign_plan_preserves_requested_story_and_distinct_visual_beats()
    print("PASS: exact campaign plan, hook, 8 cinematic beats and scene metadata.")
