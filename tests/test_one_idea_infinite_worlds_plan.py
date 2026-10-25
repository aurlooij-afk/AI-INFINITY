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
    chapters = plan["chapters"]
    assert len(chapters) == 8, [x.get("heading") for x in chapters]
    narration = [x["narration"] for x in chapters]
    combined = " ".join(narration).lower()
    assert narration[0] == "Every extraordinary world begins with something nobody else can see."
    assert "research gives it roots" in combined
    assert "mountains catch first light" in combined
    assert "research it. shape it. bring it to life. then make it better." in combined
    assert narration[-1] == "AI Infinity. One idea. Infinite worlds."
    focuses = [str(x.get("visual_focus") or "") for x in chapters]
    assert len(set(focuses)) == 8, focuses
    assert any("blank monitor" in x.lower() for x in focuses)
    assert any("storyboard" in x.lower() for x in focuses)
    assert any("mountain" in x.lower() for x in focuses)
    assert any("timeline" in x.lower() for x in focuses)
    assert any("negative space" in x.lower() for x in focuses)
    assert plan.get("source_context", {}).get("source_claims_invented") is False


if __name__ == "__main__":
    test_campaign_plan_preserves_requested_story_and_distinct_visual_beats()
    print("PASS: campaign fallback preserves eight distinct cinematic story beats.")
