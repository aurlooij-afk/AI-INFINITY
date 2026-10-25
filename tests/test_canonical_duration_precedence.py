"""Regression for explicit duration precedence over scene timecodes."""
from ai_infinity_canonical import compile_intent


def test_explicit_duration_wins_over_storyboard_timing_markers():
    command = (
        "Create an extraordinary 60-second film. "
        "0–4 seconds — The hook. 4–12 seconds — The spark. "
        "12–25 seconds — The transformation. 25–40 seconds — The cinematic sequence. "
        "49–56 seconds — The payoff. 56–60 seconds — The signature ending."
    )
    intent = compile_intent(command, {
        "duration": 60,
        "format": "long",
        "aspect_ratio": "16:9",
        "content_type": "video",
    })
    assert intent["constraints"]["duration_seconds"] == 60


def test_duration_is_still_inferred_when_no_explicit_control_is_selected():
    intent = compile_intent("Create a creative video that lasts 45 seconds.", {
        "format": "long",
        "aspect_ratio": "16:9",
        "content_type": "video",
    })
    assert intent["constraints"]["duration_seconds"] == 45


if __name__ == "__main__":
    test_explicit_duration_wins_over_storyboard_timing_markers()
    test_duration_is_still_inferred_when_no_explicit_control_is_selected()
    print("PASS: explicit duration wins; natural-language inference remains available.")