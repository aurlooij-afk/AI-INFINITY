#!/usr/bin/env python3
"""Run one real AI Infinity Creator Studio project for human review.

This deliberately uses the repository's real FastAPI creation, production worker,
artifact registry, truth endpoint and package export. No fixture IDs or fake assets.
"""
from __future__ import annotations
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
import requests

ROOT = Path.cwd()
EVIDENCE = Path(os.environ.get("AI_INFINITY_EVIDENCE_DIR", "/evidence")).resolve()
DATA = Path(os.environ.get("AI_INFINITY_REVIEW_DATA_DIR", "/tmp/ai-infinity-one-idea-review")).resolve()
EVIDENCE.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)
os.environ["AI_INFINITY_EVIDENCE_DIR"] = str(EVIDENCE)

BRIEF = """Create an extraordinary 60-second cinematic brand film for AI Infinity titled “ONE IDEA. INFINITE WORLDS.”

The film must feel like a premium global technology brand campaign combined with an emotional short film—not a generic AI advertisement, slideshow, stock-footage montage, or screen-recording tutorial.

CORE IDEA

A person has one powerful idea late at night. At first, it exists only in their imagination. The room transforms into a series of vivid cinematic worlds as that single idea becomes a researched story, a script, a visual experience, a professionally edited film, and a finished creative package. Reveal AI Infinity as the connected creative system behind the journey.

STORY AND PACING

0–4 seconds — The hook. Open immediately on a breathtaking cinematic image with a surprising visual transition. A lone creator looks at a blank screen. Voiceover: “Every extraordinary world begins with something nobody else can see.”

4–12 seconds — The spark. A single idea appears. The creator enters a world of possibilities: dramatic landscapes, a futuristic city, human emotion, and an ambitious vision. Each shot must tell part of the same story.

12–25 seconds — The transformation. Show the idea becoming researched, structured and written content. Move from source discovery to a clear narrative, storyboards, distinct scenes and compelling narration. Make the transitions visually motivated, not random.

25–40 seconds — The cinematic experience. Deliver the strongest sequence of the film: beautiful compositions, deliberate camera movement, diverse shot sizes, atmospheric lighting, meaningful transitions, convincing depth, and an emotionally engaging visual progression. Every scene must be relevant to the story and materially different from the previous one.

40–49 seconds — The craft. Show professional editing, a coherent timeline, synchronized captions, a balanced soundtrack, and the transformation into a finished video. Briefly communicate that real creative work should be editable, verifiable and recoverable—not just generated once.

49–56 seconds — The payoff. Bring the story and the creator together in one memorable cinematic reveal. The idea that once existed only in imagination is now a complete creative work. Voiceover: “Research it. Shape it. Bring it to life. Then make it better.”

56–60 seconds — The signature ending. A beautiful, restrained AI Infinity logo reveal against a deep cinematic background. Final line: “AI Infinity. One idea. Infinite worlds.”

VISUAL DIRECTION

Use a sophisticated filmic look: deep midnight blues, controlled cyan and violet highlights, warm gold practical lights, rich natural textures, atmospheric depth, carefully composed frames, smooth motivated camera movement, and premium typography. Build visual contrast between quiet intimate moments and expansive cinematic reveals. Avoid excessive neon, cheap particle effects, random zooms, repeated imagery, cluttered overlays, distorted text and artificial-looking transitions.

VOICE, MUSIC AND SOUND

Write original, emotionally intelligent narration that fits the actual runtime. Use a natural, expressive English neural voice when available and verify the real voice provider. Compose a coherent musical arc that begins intimate, grows progressively, reaches a powerful but controlled climax, and resolves cleanly. Use subtle sound design to connect transitions. Keep narration intelligible, programme audio balanced, and the ending clean.

PRODUCTION REQUIREMENTS

Create an actual video through the AI Infinity production workflow. Target 1920×1080, 16:9, exactly 60 seconds, with multiple distinct rendered scenes, meaningful visual continuity, readable synchronized captions, and a professionally mixed audio track.

Research and select genuinely relevant visual sources. Prefer verified public-domain or clearly licensed assets; preserve source URLs, creator information, applicable licence and attribution requirements, provenance, and per-asset integrity evidence. Do not invent research, sources, permissions, footage, AI-provider usage, or product capabilities. Use free-first execution and do not invoke paid services.

The story must reflect capabilities that AI Infinity can genuinely demonstrate. Do not fabricate UI screens or imply that a feature works merely because it appears in the film. If suitable footage, a required provider, or reliable rights evidence is unavailable, expose that limitation and retain REVIEW_REQUIRED status instead of silently substituting misleading assets.

QUALITY CONTROL BEFORE DELIVERY

Independently inspect the final MP4 with FFprobe and perform a full media decode. Verify the requested duration, resolution, video and audio streams, actual scene count, visual diversity, narration, caption timing, audio levels, source relevance, rights evidence, and manifest hashes. Inspect representative frames throughout the whole film—not just its opening.

Generate a thumbnail, final script, captions.srt, audio_master.mp3, narration-only audio where supported, sources.json, provenance records, rights manifest, production manifest, a complete downloadable package.zip, and an honest human-review packet. Preserve the editable project and timeline where supported.

Do not claim success merely because rendering finishes. If a critical requirement fails, explain the actual failure and retain the review or delivery gate. Finish with the real downloadable video and the evidence needed for a human creative review.

SUCCESS STANDARD: The first four seconds must earn attention, every scene must advance the story, the emotional payoff must feel deserved, and the final film must look and sound intentionally crafted. Make every second count."""

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def main() -> int:
    # Fail fast if story timecodes ever override the explicitly selected duration.
    regression = subprocess.run([sys.executable, str(ROOT / "tests" / "test_canonical_duration_precedence.py")],
                               capture_output=True, text=True, timeout=60)
    print((regression.stdout or "") + (regression.stderr or ""), flush=True)
    if regression.returncode != 0:
        raise RuntimeError("Explicit-duration/story-timecode regression failed.")
    # Reuse the repository's real acceptance helpers for server boot and API calls.
    helpers = runpy.run_path(str(ROOT / "tests" / "final_acceptance_suite.py"))
    start, stop = helpers["start"], helpers["stop"]
    req, names, dl, probe = helpers["req"], helpers["names"], helpers["dl"], helpers["probe"]
    process = log = None
    result = {"schema": "ai-infinity-human-review-production.v1",
              "title": "ONE IDEA. INFINITE WORLDS.",
              "engine": "real AI Infinity Creator Studio runtime",
              "human_review_status": "PENDING",
              "publish_performed": False,
              "prompt_sha256": hashlib.sha256(BRIEF.encode("utf-8")).hexdigest(),
              "requested": {"duration_seconds": 60, "resolution": [1920, 1080],
                            "aspect_ratio": "16:9", "format": "long", "language": "English"}}
    try:
        extra = {
            "AI_INFINITY_DATA_DIR": str(DATA),
            "AI_INFINITY_FAST_MODE": "0",
            "AI_INFINITY_REQUIRE_SOURCE_VISUALS": "1",
            "AI_INFINITY_REQUIRE_RESEARCH_EVIDENCE": "1",
            "AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS": "0",
            "AI_INFINITY_3601_BACKGROUND": "false",
            "AI_INFINITY_MAX_HEAVY_JOBS": "1",
        }
        process, base, log = start(DATA, 18090, extra)
        health = req(requests.Session(), base, "/health")
        result["runtime_health"] = health
        session = requests.Session()
        create_payload = {
            "command": BRIEF,
            "title": "ONE IDEA. INFINITE WORLDS.",
            "duration": 60,
            "format": "long",
            "aspect_ratio": "16:9",
            "language": "English",
            "content_type": "video",
            "idempotency_key": "human-review-one-idea-infinite-worlds-v1",
        }
        result["create_request"] = {"title": create_payload["title"],
                                    "duration": 60, "aspect_ratio": "16:9",
                                    "language": "English",
                                    "command_sha256": result["prompt_sha256"],
                                    "command_characters": len(BRIEF)}
        pid = helpers["create"](session, base, BRIEF,
                                title=create_payload["title"], duration=60,
                                format="long", aspect_ratio="16:9",
                                language="English", content_type="video",
                                idempotency_key=create_payload["idempotency_key"])
        result["project_id"] = pid
        (EVIDENCE / "project-id.txt").write_text(pid + "\n", encoding="utf-8")
        print("AI_INFINITY_PROJECT_ID=" + pid, flush=True)
        deadline = time.monotonic() + 2100
        state = {}
        terminal = {"completed", "completed_with_qc_warnings", "failed", "cancelled"}
        while time.monotonic() < deadline:
            state = req(session, base, "/infinity/studio/project/" + requests.utils.quote(pid, safe=""))
            status = str(state.get("status") or "").lower()
            print("AI_INFINITY_PROJECT_STATE=" + json.dumps({
                k: state.get(k) for k in ("project_id", "status", "state", "stage",
                                          "progress", "current_scene", "total_scenes", "error")
            }, ensure_ascii=False, default=str), flush=True)
            if status in terminal:
                break
            time.sleep(3)
        result["project_status"] = state
        (EVIDENCE / "project-state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if str(state.get("status") or "").lower() not in terminal:
            result["blockers"] = ["AI Infinity creator job did not reach a terminal state within 35 minutes."]
        # Get authoritative quality truth separately from the lifecycle status.
        try:
            truth = req(session, base, "/infinity/studio/project/" + requests.utils.quote(pid, safe="") + "/truth")
        except Exception as exc:
            truth = {"verified": False, "state": "REVIEW_REQUIRED",
                     "truthful": True, "truth_endpoint_error": type(exc).__name__ + ": " + str(exc)[:800],
                     "checks": {}, "failures": ["Truth endpoint unavailable."]}
        result["truth"] = truth
        (EVIDENCE / "production-truth.json").write_text(
            json.dumps(truth, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        available = set()
        try:
            available = names(session, base, pid)
            (EVIDENCE / "artifact-inventory.json").write_text(
                json.dumps(sorted(available), indent=2), encoding="utf-8")
        except Exception as exc:
            result.setdefault("blockers", []).append("Could not list the creator artifact inventory: " + type(exc).__name__)
        project_dir = DATA / "creator_studio" / pid
        # Preserve per-file integrity evidence without duplicating hundreds of
        # megabytes of source footage inside the human-review artifact.
        debug_dir = EVIDENCE / "debug-intermediates"
        debug_dir.mkdir(parents=True, exist_ok=True)
        captured_intermediates = []
        if project_dir.is_dir():
            allowed_patterns = (
                "scene_*.mp4", "procedural_visual_*.png", "selected_visual_*.mp4",
                "pexels_one_idea_*.mp4", "narration_*.mp3", "narration_only.mp3",
                "music.m4a", "transition_*.wav", "master.mp4",
                "master_video_only.mkv", "canonical_contract.json", "visual_rights.json",
            )
            for pattern in allowed_patterns:
                for candidate in sorted(project_dir.glob(pattern)):
                    if not candidate.is_file() or candidate.stat().st_size <= 100:
                        continue
                    row = {
                        "name": candidate.name,
                        "bytes": candidate.stat().st_size,
                        "sha256": sha(candidate),
                    }
                    if candidate.suffix.lower() in {".mp4", ".m4a", ".mp3", ".wav", ".mkv"}:
                        try:
                            media = probe(candidate)
                            row["duration_seconds"] = float((media.get("format") or {}).get("duration") or 0)
                            row["streams"] = [
                                {"type": stream.get("codec_type"), "codec": stream.get("codec_name"),
                                 "duration_seconds": float(stream.get("duration") or 0),
                                 "width": stream.get("width"), "height": stream.get("height"),
                                 "channels": stream.get("channels"), "sample_rate": stream.get("sample_rate")}
                                for stream in (media.get("streams") or [])
                            ]
                        except Exception as exc:
                            row["probe_error"] = type(exc).__name__ + ": " + str(exc)[:300]
                    captured_intermediates.append(row)
        expected_story_seconds = [4.0, 8.0, 13.0, 6.5, 8.5, 9.0, 7.0, 4.0]
        scene_media = sorted(
            [row for row in captured_intermediates
             if re.fullmatch(r"scene_\d{2}\.mp4", str(row.get("name") or ""))],
            key=lambda row: str(row["name"]),
        )
        actual_story_seconds = [float(row.get("duration_seconds") or 0.0) for row in scene_media]
        story_timing_passed = (
            len(actual_story_seconds) == len(expected_story_seconds)
            and all(abs(actual - expected) <= 0.25
                    for actual, expected in zip(actual_story_seconds, expected_story_seconds))
        )
        result["story_timing"] = {
            "passed": story_timing_passed,
            "expected_scene_seconds": expected_story_seconds,
            "actual_scene_seconds": actual_story_seconds,
            "scene_files": [row.get("name") for row in scene_media],
            "expected_boundaries_seconds": [0, 4, 12, 25, 31.5, 40, 49, 56, 60],
            "actual_total_seconds": round(sum(actual_story_seconds), 3),
            "tolerance_seconds_per_scene": 0.25,
        }
        (EVIDENCE / "intermediate-media-inventory.json").write_text(
            json.dumps(captured_intermediates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Keep the app's complete editable project package intact, but make it
        # a separate artifact so the human-review packet stays below the 512 MiB
        # download ceiling. The package's hash is recorded in the summary.
        project_package = project_dir / "package.zip"
        if project_package.is_file() and project_package.stat().st_size > 1000:
            shutil.copy2(project_package, EVIDENCE / "editable-project-package.zip")
            result["editable_project_package"] = {
                "available": True,
                "bytes": project_package.stat().st_size,
                "sha256": sha(project_package),
                "storage": "separate GitHub Actions artifact",
            }
        else:
            result["editable_project_package"] = {"available": False}
        artifact_dir = EVIDENCE / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        wanted = [
            "final.mp4", "thumbnail.jpg", "audio_master.mp3", "narration_only.mp3",
            "captions.srt", "script.md", "sources.json", "manifest.json",
            "production_manifest.json", "rights_manifest.json", "provenance.json",
            "fact_check.json", "visual_rights.json", "seo.json", "social_campaign.json",
            "accessibility.json", "timeline.json", "storyboard.json",
        ]
        retrieval = {}
        for name in wanted:
            local = project_dir / name
            target = artifact_dir / name
            if name in available:
                try:
                    downloaded = dl(session, base, pid, name, min_bytes=1)
                    shutil.copy2(downloaded, target)
                    retrieval[name] = {"downloaded": True, "bytes": target.stat().st_size,
                                       "sha256": sha(target)}
                    continue
                except Exception as exc:
                    retrieval[name] = {"download_error": type(exc).__name__ + ": " + str(exc)[:500]}
            if local.is_file():
                shutil.copy2(local, target)
                retrieval[name] = {"downloaded_from_project_store": True,
                                   "bytes": target.stat().st_size, "sha256": sha(target)}
        result["artifact_retrieval"] = retrieval
        final = artifact_dir / "final.mp4"
        if not final.is_file():
            result["blockers"] = result.get("blockers", []) + ["The real AI Infinity run did not expose a final.mp4 artifact."]
            (EVIDENCE / "production-summary.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            return 2

        media = probe(final)
        streams = media.get("streams") or []
        video = next((x for x in streams if x.get("codec_type") == "video"), {})
        audio = next((x for x in streams if x.get("codec_type") == "audio"), {})
        duration = float((media.get("format") or {}).get("duration") or 0)
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        decode = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-xerror",
                                 "-i", str(final), "-f", "null", "-"],
                                capture_output=True, text=True, timeout=900)
        subtitle = next((x for x in streams if x.get("codec_type") == "subtitle"), {})
        try:
            fps_num, fps_den = str(video.get("avg_frame_rate") or "0/1").split("/", 1)
            frame_rate_fps = float(fps_num) / max(float(fps_den), 1e-9)
        except (TypeError, ValueError, ZeroDivisionError):
            frame_rate_fps = 0.0
        result["final_media"] = {
            "path": "artifacts/final.mp4", "size_bytes": final.stat().st_size,
            "sha256": sha(final), "duration_seconds": duration,
            "resolution": [width, height], "frame_rate_fps": frame_rate_fps,
            "video_codec": video.get("codec_name"), "audio_codec": audio.get("codec_name"),
            "subtitle_codec": subtitle.get("codec_name"),
            "subtitle_duration_seconds": float(subtitle.get("duration") or 0),
            "ffprobe": media,
        }
        result["full_decode"] = {"passed": decode.returncode == 0,
                                 "exit_code": decode.returncode,
                                 "stderr": (decode.stderr or "")[-2000:]}
        def load_json(name):
            path = artifact_dir / name
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}
        src = load_json("sources.json")
        rights = load_json("rights_manifest.json")
        prod = load_json("production_manifest.json")
        manifest = load_json("manifest.json")
        captions_path = artifact_dir / "captions.srt"
        caption_text = captions_path.read_text(encoding="utf-8", errors="replace") if captions_path.exists() else ""
        cue_count = sum(1 for line in caption_text.splitlines() if "-->" in line)
        src_count = int(src.get("source_count") or len(src.get("sources") or []))
        assets = rights.get("assets") or []
        unresolved = []
        for asset in assets:
            state_value = str(asset.get("commercial_use_state") or asset.get("rights_state") or asset.get("status") or "").lower()
            if any(tag in state_value for tag in ("review_required", "unknown", "unresolved", "blocked", "denied")):
                unresolved.append({"asset_id": asset.get("asset_id") or asset.get("id") or asset.get("url"),
                                   "rights_state": state_value})
        artifact_rows = prod.get("artifacts") or manifest.get("artifacts") or manifest.get("files") or []
        expected_sha = next((str(row.get("sha256")) for row in artifact_rows
                             if str(row.get("name") or row.get("path") or "") == "final.mp4" and row.get("sha256")), "")
        script_path = artifact_dir / "script.md"
        script_text = script_path.read_text(encoding="utf-8", errors="replace") if script_path.exists() else ""
        expected_story_terms = {
            "hook": ["nobody else can see", "extraordinary world", "blank screen"],
            "idea_to_worlds": ["imagination", "world", "possibilities"],
            "research_and_story": ["research", "script", "storyboard"],
            "craft": ["editable", "captions", "timeline", "recoverable"],
            "payoff_and_signoff": ["shape it", "bring it to life", "infinite worlds"],
        }
        story_alignment = {
            label: {"matched": [term for term in terms if term in script_text.lower()],
                    "required_terms": terms}
            for label, terms in expected_story_terms.items()
        }
        result["sources"] = {"source_count": src_count, "status": src.get("status"),
                             "providers": src.get("providers"), "truthful": src.get("truthful")}
        result["rights_manifest"] = {"asset_count": len(assets),
                                     "unresolved_asset_count": len(unresolved),
                                     "unresolved_assets": unresolved,
                                     "truthful": rights.get("truthful")}
        result["production_manifest"] = {"truthful": prod.get("truthful"),
                                         "project_id": prod.get("project_id"),
                                         "artifact_count": len(artifact_rows),
                                         "manifest_final_sha256": expected_sha}
        result["captions"] = {"exists": captions_path.exists(), "cue_count": cue_count,
                              "caption_word_count": len(caption_text.split()) if caption_text else 0}
        result["creative_brief_alignment"] = story_alignment
        checks = truth.get("checks") if isinstance(truth.get("checks"), dict) else {}
        technical_gates = {
            "project_terminal_completed": str(state.get("status") or "").lower() == "completed",
            "engine_truth_verified": truth.get("verified") is True and str(truth.get("state") or "").upper() == "VERIFIED",
            "duration_60s": abs(duration - 60) <= 1,
            "resolution_1920x1080": (width, height) == (1920, 1080),
            "frame_rate_24fps_cfr": abs(frame_rate_fps - 24.0) <= 0.01
                and video.get("avg_frame_rate") == "24/1",
            "aspect_ratio_16x9": bool(width and height and abs((width / height) - (16/9)) <= 0.01),
            "h264_video": video.get("codec_name") == "h264",
            "audio_stream": bool(audio),
            "embedded_subtitle_stream": bool(subtitle) and subtitle.get("codec_name") == "mov_text"
                and float(subtitle.get("duration") or 0) >= 45,
            "full_ffmpeg_decode": decode.returncode == 0,
            "research_sources_present": src_count > 0,
            "source_visual_policy": checks.get("source_visual_policy_passed") is True,
            "zero_visual_fallbacks": int(checks.get("fallback_visual_count") or 0) == 0,
            "rights_manifest_present": bool(assets),
            "zero_unresolved_rights": len(unresolved) == 0 and checks.get("visual_rights_evidence_present") is True,
            "captions_present_and_timed": captions_path.exists() and cue_count >= 13 and checks.get("caption_sync_passed") is True,
            "manifest_sha_matches": bool(expected_sha) and expected_sha == sha(final),
            "exact_campaign_title_and_hook": bool(
                re.search(r"(?mi)^#\s+ONE IDEA\. INFINITE WORLDS\.$", script_text)
                and "Every extraordinary world begins with something nobody else can see." in script_text
                and "Research it. Shape it. Bring it to life. Then make it better." in script_text
                and "AI Infinity. One idea. Infinite worlds." in script_text
            ),
            "authored_story_timing": result.get("story_timing", {}).get("passed") is True,
            "professional_scene_coverage": checks.get("professional_scene_coverage_passed") is True,
            "professional_visual_diversity": checks.get("professional_visual_diversity_passed") is True,
            "professional_motion_coverage": checks.get("professional_motion_coverage_passed") is True,
            "no_unexpected_provider_fallbacks": int(checks.get("voice_fallback_count") or 0) == 0,
        }
        result["acceptance_gates"] = technical_gates
        result["all_technical_gates_passed"] = all(technical_gates.values())
        result["human_review_status"] = "PENDING"
        result["publish_performed"] = False
        result["blockers"] = result.get("blockers", [])
        for gate, passed in technical_gates.items():
            if not passed:
                result["blockers"].append("Failed acceptance gate: " + gate)
        result["overall_result"] = "READY_FOR_HUMAN_REVIEW" if result["all_technical_gates_passed"] else "REVIEW_REQUIRED"
        # Keep original brief and a blank, honest scorecard next to the actual output.
        (EVIDENCE / "exact-creative-brief.md").write_text(BRIEF + "\n", encoding="utf-8")
        scorecard = {
            "schema": "ai-infinity-human-editorial-scorecard.v1",
            "project_id": pid, "human_review_status": "PENDING", "reviewers": [],
            "scale": {"minimum": 1, "maximum": 5},
            "dimensions": [{"name": name, "critical": critical, "rating_1_to_5": None, "notes": ""}
                           for name, critical in [
                               ("brief_adherence_and_story_arc", True),
                               ("opening_hook_0_to_4_seconds", True),
                               ("scene_relevance_and_visual_diversity", True),
                               ("cinematography_and_transitions", False),
                               ("voice_intelligibility_and_delivery", True),
                               ("music_and_audio_mix", True),
                               ("caption_accuracy_and_timing", True),
                               ("brand_fit_and_typography", False),
                               ("facts_sources_and_rights", True),
                               ("editability_and_recovery", False)]],
            "release_policy": {"average_minimum": 4.0, "critical_minimum": 3,
                               "unresolved_fact_or_rights_issue_blocks_release": True},
            "policy_note": "Human review has not been performed. Automated checks do not replace editorial judgment."
        }
        (EVIDENCE / "human-review-scorecard.json").write_text(
            json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Generate the app's established reviewer contact sheet / packet if source + rights records exist.
        try:
            if all((artifact_dir / name).is_file() for name in ("sources.json", "production_manifest.json", "rights_manifest.json")):
                packet_info = helpers["create_review_packet"](pid,
                    {name: path for name, path in ((p.name, p) for p in artifact_dir.iterdir() if p.is_file())},
                    truth, prod, rights, media)
                result["reviewer_packet"] = packet_info
        except Exception as exc:
            result["review_packet_error"] = type(exc).__name__ + ": " + str(exc)[:1000]
        visual_audit = []
        for asset in assets:
            visual_audit.append({
                key: asset.get(key) for key in (
                    "scene", "scene_index", "asset_id", "title", "source", "provider",
                    "source_url", "downloaded_media_url", "creator", "license", "license_url",
                    "quality_tier", "asset_kind", "commercial_use_state", "rights_status",
                    "rights_evidence_method", "fallback", "fallback_reason", "selection_reason",
                    "resolution", "duration_seconds", "sha256", "asset_sha256",
                ) if key in asset
            })
        result["visual_asset_audit"] = visual_audit
        (EVIDENCE / "production-summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        # A convenience top-level copy with the promised review filename.
        shutil.copy2(final, EVIDENCE / "AI-Infinity-One-Idea-Infinite-Worlds-60s.mp4")
        # Bundle evidence and editable deliverables without pretending a blank scorecard is approval.
        bundle = EVIDENCE / "AI-Infinity-One-Idea-Infinite-Worlds-Review-Package.zip"
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as z:
            for path in sorted(EVIDENCE.rglob("*")):
                if not path.is_file() or path == bundle:
                    continue
                relative = path.relative_to(EVIDENCE)
                if relative.name == "editable-project-package.zip":
                    continue
                if relative.parts and relative.parts[0] == "debug-intermediates":
                    continue
                z.write(path, relative.as_posix(),
                        compress_type=zipfile.ZIP_STORED if path.suffix.lower() in {".mp4", ".mp3", ".zip"} else zipfile.ZIP_DEFLATED)
        print("AI_INFINITY_CREATIVE_REVIEW_RESULT=" + json.dumps({
            "project_id": pid, "overall_result": result["overall_result"],
            "all_technical_gates_passed": result["all_technical_gates_passed"],
            "failed_gates": [name for name, passed in technical_gates.items() if not passed],
            "duration_seconds": duration, "resolution": [width, height],
            "source_count": src_count, "rights_asset_count": len(assets),
            "fallback_visual_count": checks.get("fallback_visual_count"),
            "fallback_assets": [
                {"scene": row.get("scene") or row.get("scene_index"),
                 "source": row.get("source"), "title": row.get("title"),
                 "fallback": row.get("fallback"), "fallback_reason": row.get("fallback_reason")}
                for row in visual_audit if row.get("fallback")
            ],
            "embedded_subtitles": bool(subtitle) and subtitle.get("codec_name") == "mov_text",
            "review_package_bytes": bundle.stat().st_size,
            "editable_project_package_bytes": result.get("editable_project_package", {}).get("bytes"),
        }, ensure_ascii=False), flush=True)
        # The actual render is useful for human review even if strict creative truth failed;
        # fail the job only when the artifact is not a technically decodable 60-second video.
        media_minimum_pass = abs(duration - 60) <= 1 and video.get("codec_name") == "h264" and bool(audio) and decode.returncode == 0
        if not media_minimum_pass:
            print("VIDEO_MEDIA_BASELINE_FAILED; reviewer evidence retained.", flush=True)
            return 3
        return 0
    except Exception as exc:
        result.setdefault("blockers", []).append(type(exc).__name__ + ": " + str(exc)[:1600])
        (EVIDENCE / "production-summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        raise
    finally:
        if process is not None:
            stop(process, log)

if __name__ == "__main__":
    raise SystemExit(main())
