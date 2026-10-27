import os
import shutil

os.environ.setdefault("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity-professional-test")
os.environ["AI_INFINITY_3601_BACKGROUND"] = "false"
os.environ["AI_INFINITY_FAST_MODE"] = "1"

import main
from fastapi.testclient import TestClient

client = TestClient(main.app)


def test_professional_creator_surface_is_mounted():
    r = client.get("/infinity/pro2/health")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["build"].startswith("PROFESSIONAL-CREATOR-SYSTEM")
    ffmpeg_provider = next(p for p in data["providers"] if p["name"] == "FFmpeg render/edit")
    expected_ready = bool(shutil.which("ffmpeg"))
    assert ffmpeg_provider["ready"] is expected_ready, data
    assert ffmpeg_provider["status"] == ("ready" if expected_ready else "missing"), data


def test_professional_creator_ui_is_real_route():
    r = client.get("/infinity/pro2")
    assert r.status_code == 200, r.text
    assert "Professional Creator System" in r.text
    assert "Start production" in r.text

def test_explicit_timecoded_brand_film_brief_is_preserved_by_offline_planner():
    import studio_ultimate

    objective = (
        "Create a 60-second cinematic brand film for AI Infinity. Story: "
        "0–4s an arresting night-room hook, lone creator facing a blank screen; "
        "narration “Every extraordinary world begins with something nobody else can see.” "
        "4–12s the idea opens into dramatic landscapes, a futuristic city and human emotion. "
        "12–25s real research becomes a clear narrative, script and storyboard; never invent research or UI. "
        "25–40s cinematic landscapes, meaningful transitions, atmospheric lighting and emotional progression. "
        "40–49s editable timeline, synchronized captions, balanced soundtrack and verification. "
        "49–56s the creator reveals the completed work; narration “Research it. Shape it. Bring it to life. Then make it better.” "
        "56–60s restrained AI Infinity signature; final line “AI Infinity. One idea. Infinite worlds.” "
        "Visuals: midnight blue, restrained cyan/violet, warm gold."
    )
    plan = studio_ultimate._fallback_creative_plan(
        title="ONE IDEA. INFINITE WORLDS.",
        objective=objective,
        fmt="long",
        duration=60,
        audience="global technology audience",
        tone="premium cinematic and emotionally intelligent",
        research={"sources": []},
        language="English",
        topic="AI Infinity — One Idea. Infinite Worlds.",
    )

    chapters = plan["chapters"]
    assert plan["title"] == "ONE IDEA. INFINITE WORLDS."
    assert plan["storyboard_origin"] == "explicit_timecoded_user_brief"
    assert plan["brief_fidelity"]["timecoded_scenes"] == 7
    assert [chapter["duration"] for chapter in chapters] == [4.0, 8.0, 13.0, 15.0, 9.0, 7.0, 4.0]
    assert sum(chapter["duration"] for chapter in chapters) == 60.0
    assert chapters[0]["narration"] == "Every extraordinary world begins with something nobody else can see."
    assert chapters[5]["narration"] == "Research it. Shape it. Bring it to life. Then make it better."
    assert chapters[6]["narration"] == "AI Infinity. One idea. Infinite worlds."
    assert len({chapter["visual_focus"] for chapter in chapters}) == 7
    assert "futuristic city" in chapters[1]["visual_focus"].lower()
    assert "generic Creator OS marketing copy" not in " ".join(chapter["narration"] for chapter in chapters)
    model_calls = []
    model_plan, provider = studio_ultimate.creative_plan(
        {
            "title": "ONE IDEA. INFINITE WORLDS.",
            "objective": objective,
            "format": "long",
            "duration": 60,
            "audience": "global technology audience",
            "tone": "premium cinematic and emotionally intelligent",
            "language": "English",
            "topic": "AI Infinity — One Idea. Infinite Worlds.",
        },
        {"sources": []},
        lambda payload: model_calls.append(payload) or {},
    )
    assert provider == "explicit-timecoded-user-brief"
    assert model_calls == []
    assert [chapter["duration"] for chapter in model_plan["chapters"]] == [4.0, 8.0, 13.0, 15.0, 9.0, 7.0, 4.0]

