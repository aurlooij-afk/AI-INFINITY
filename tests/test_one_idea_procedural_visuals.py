"""Regression tests for story-specific original vector fallback art.

These tests do not promote illustrative assets to documentary footage or pass the
professional source-media gate. They ensure the explicitly labelled fallback is
scene-specific, distinct, and traceable when free providers are unavailable.
"""
import hashlib
from pathlib import Path
import tempfile

import studio_ultimate


def test_procedural_art_extracts_visual_focus_and_renders_distinct_story_beats():
    prompts = [
        ("A WORLD BEGINS UNSEEN",
         "Premium editorial documentary image about extraordinary 60-second cinematic brand; visual focus: intimate cinematic close-up of a lone creator awake late at night, facing a blank monitor in a quiet dark room; soft cyan screen reflection, warm gold lamp, restrained composition."),
        ("ONE IDEA, DRAWN INTO LIFE",
         "Premium editorial documentary image about extraordinary 60-second cinematic brand; visual focus: a handwritten idea on paper beneath a midnight desk lamp; creator lifts the page beside pencil storyboard panels."),
        ("FROM PENCIL TO HORIZON",
         "Premium editorial documentary image about extraordinary 60-second cinematic brand; visual focus: a pencil line drawing a mountain ridge match-cuts into a real expansive mountain landscape."),
        ("FIRST LIGHT, NEW WORLDS",
         "Premium editorial documentary image about extraordinary 60-second cinematic brand; visual focus: a sweeping wide view of mountains catching first light over a still lake."),
        ("A CITY BECOMES A POSSIBILITY",
         "Premium editorial documentary image about extraordinary 60-second cinematic brand; visual focus: a sweeping wide view of a futuristic city at blue hour, warm gold windows against midnight-blue architecture."),
        ("EVERY CUT HAS A PURPOSE",
         "Premium editorial documentary image about extraordinary 60-second cinematic brand; visual focus: over-shoulder view of a real editor refining a multi-track video timeline with distinct footage clips, captions and audio tracks."),
        ("THE IDEA BECOMES REAL",
         "Premium editorial documentary image about extraordinary 60-second cinematic brand; visual focus: the same creator watches the finished film in the studio; mountain sunrise and city lights play on the monitor."),
        ("ONE IDEA. INFINITE WORLDS.",
         "Premium editorial documentary image about extraordinary 60-second cinematic brand; visual focus: minimal midnight-blue end frame with a restrained cyan-to-violet infinity-like light trail and negative space for AI Infinity. One idea. Infinite worlds."),
    ]
    with tempfile.TemporaryDirectory(prefix="aii-one-idea-art-") as tmp:
        root = Path(tmp)
        assets = []
        hashes = []
        for index, (expected_label, prompt) in enumerate(prompts, 1):
            asset = studio_ultimate._procedural_image(prompt, root, index, width=640, height=360)
            assert asset and Path(asset["path"]).is_file(), expected_label
            assert asset.get("quality_tier") == "original_vector_editorial_fallback"
            assert asset.get("asset_kind") == "illustrative_graphic_not_photograph"
            assert asset.get("commercial_use_state") == "owner_attested_original_work"
            assert asset.get("rights_evidence_method") == "in_app_procedural_vector_renderer"
            assert asset.get("visual_focus"), expected_label
            assets.append(asset)
            hashes.append(hashlib.sha256(Path(asset["path"]).read_bytes()).hexdigest())
        assert len(set(hashes)) == len(prompts), hashes
        focus = [asset["visual_focus"].lower() for asset in assets]
        assert "blank monitor" in focus[0]
        assert "storyboard" in focus[1]
        assert "mountain ridge" in focus[2]
        assert "mountains catching first light" in focus[3]
        assert "futuristic city" in focus[4]
        assert "video timeline" in focus[5]
        assert "same creator watches" in focus[6]
        assert "midnight-blue end frame" in focus[7]


if __name__ == "__main__":
    test_procedural_art_extracts_visual_focus_and_renders_distinct_story_beats()
    print("PASS: 8 original fallback art scenes are distinct, labelled, and focus-grounded.")
