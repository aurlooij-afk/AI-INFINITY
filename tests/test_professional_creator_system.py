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
        "Create a 60-second premium cinematic brand film for AI Infinity. "
        "**STORY AND PACING** "
        "**0–4 seconds — The hook.** Open immediately on a breathtaking night-room image. "
        "A lone creator looks at a blank screen. Voiceover: “Every extraordinary world begins with something nobody else can see.” "
        "**4–12 seconds — The spark.** A single idea appears; the creator enters dramatic landscapes, a futuristic city and human emotion. "
        "**12–25 seconds — The transformation.** Research becomes a clear story, script and storyboard; transitions must be motivated. "
        "**25–40 seconds — The cinematic experience.** Distinct compositions, deliberate camera movement, atmospheric lighting, depth and meaningful progression. "
        "**40–49 seconds — The craft.** Show a real editable timeline, synchronized captions, balanced soundtrack and verification only where genuinely supported. "
        "**49–56 seconds — The payoff.** The idea becomes a complete work. Voiceover: “Research it. Shape it. Bring it to life. Then make it better.” "
        "**56–60 seconds — The signature ending.** A restrained AI Infinity end card. Final line: “AI Infinity. One idea. Infinite worlds.” "
        "**VISUAL DIRECTION** Use a sophisticated filmic look: deep midnight blues, controlled cyan and violet highlights, warm gold practical lights, natural textures, atmospheric depth and premium typography. Avoid excessive neon, random zooms, repeated imagery and clutter. "
        "**VOICE, MUSIC AND SOUND** Use natural expressive English neural voice when a genuinely available free provider is verified; otherwise disclose the actual local voice. Music must grow from intimate to a controlled climax and resolve cleanly. "
        "**PRODUCTION REQUIREMENTS** Target 1920x1080, 16:9 and exactly 60 seconds. Preserve real source URLs, creators, licences, attribution, provenance and per-asset hashes. "
        "No invented sources, permissions, provider use or product capabilities. "
        "**QUALITY CONTROL** Probe and fully decode the MP4, verify duration, resolution, stream presence, distinct scenes, captions, audio levels, sources, rights and manifest hashes. Unknown rights remain REVIEW_REQUIRED."
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

