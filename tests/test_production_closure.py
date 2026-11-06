import json
import os
from pathlib import Path

os.environ["AI_INFINITY_DATA_DIR"] = "/tmp/ai-infinity-closure-test"
os.environ["AI_INFINITY_3601_BACKGROUND"] = "false"
os.environ["AI_INFINITY_FAST_MODE"] = "1"

import main
import production_closure_3624 as closure
import studio_ultimate as studio
from fastapi.testclient import TestClient

client = TestClient(main.app)



def test_explicit_launch_preserves_all_nine_authored_story_beats():
    assert studio._max_scene_count(
        is_short=False, fast_mode=True, smoke=False,
        explicitly_single_scene=False, explicit_launch=True,
    ) == 9
    assert studio._max_scene_count(
        is_short=False, fast_mode=True, smoke=False,
        explicitly_single_scene=False,
    ) == 8
    assert studio._max_scene_count(
        is_short=True, fast_mode=True, smoke=False,
        explicitly_single_scene=False, explicit_launch=True,
    ) == 5
    assert studio._max_scene_count(
        is_short=False, fast_mode=False, smoke=True,
        explicitly_single_scene=False, explicit_launch=True,
    ) == 1

def test_production_manifest_inventory_hashes_final_package_member_bytes(tmp_path):
    manifest = tmp_path / "manifest.json"
    production_manifest = tmp_path / "production_manifest.json"
    rights_manifest = tmp_path / "rights_manifest.json"
    music = tmp_path / "music.m4a"
    music_source = tmp_path / "music_source.json"

    manifest.write_text('{"revision":"preliminary"}', encoding="utf-8")
    rights_manifest.write_text('{"assets":[]}', encoding="utf-8")
    music.write_bytes(b"verified-audio-master")
    music_source.write_text(
        json.dumps({
            "method": "synthetic_procedural_chord_bed_fallback",
            "rights_status": "original_procedural_audio",
            "human_rights_review": "PENDING",
            "output_sha256": studio.file_sha256(music),
            "tracks": [],
            "truthful": True,
        }),
        encoding="utf-8",
    )
    production_manifest.write_text(
        json.dumps({
            "project_id": "manifest-regression",
            "truthful": True,
            "artifacts": [{
                "name": "manifest.json",
                "sha256": studio.file_sha256(manifest),
                "bytes": manifest.stat().st_size,
            }],
        }),
        encoding="utf-8",
    )

    # This is the final metadata write performed after the preliminary manifest
    # has already been recorded by the earlier production-manifest pass.
    manifest.write_text(
        json.dumps({"revision": "final", "quality": {"passed": True}, "details": "final"}),
        encoding="utf-8",
    )
    finalized = studio._finalize_production_manifest_inventory(
        production_manifest,
        [manifest, production_manifest, rights_manifest, music, music_source],
    )
    rows = {row["name"]: row for row in finalized["artifacts"]}

    assert rows["manifest.json"]["sha256"] == studio.file_sha256(manifest)
    assert rows["manifest.json"]["bytes"] == manifest.stat().st_size
    assert rows["rights_manifest.json"]["sha256"] == studio.file_sha256(rights_manifest)
    assert rows["music.m4a"]["sha256"] == studio.file_sha256(music)
    assert rows["music_source.json"]["sha256"] == studio.file_sha256(music_source)
    assert "production_manifest.json" not in rows
    assert finalized["artifact_inventory"]["complete"] is True
    assert finalized["artifact_inventory"]["artifact_count"] == len(rows)


def test_audio_rights_evidence_binds_music_master_to_source_manifest(tmp_path):
    music = tmp_path / "music.m4a"
    source_manifest = tmp_path / "music_source.json"
    music.write_bytes(b"verified-audio-master")
    source_manifest.write_text(
        json.dumps({
            "method": "openverse_openly_licensed_tracks_crossfaded_and_arranged",
            "rights_status": "license_metadata_present",
            "human_rights_review": "PENDING",
            "output_sha256": studio.file_sha256(music),
            "tracks": [{
                "source": "Openverse",
                "source_id": "track-1",
                "source_url": "https://example.test/track-1",
                "title": "Licensed track",
                "creator": "Creator",
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "commercial_use_state": "commercial_use_allowed_with_attribution",
                "rights_status": "license_metadata_present",
            }],
            "truthful": True,
        }),
        encoding="utf-8",
    )

    evidence = studio._audio_rights_evidence(music, source_manifest)

    assert evidence["asset_sha256"] == studio.file_sha256(music)
    assert evidence["source_manifest_matches_audio"] is True
    assert evidence["rights_state"] == "licensed"
    assert evidence["human_rights_review"] == "PENDING"
    assert evidence["tracks"][0]["creator"] == "Creator"
    assert "path" not in evidence["tracks"][0]


def test_closure_routes_and_truthful_readiness():
    r = client.get("/infinity/studio/production/contract")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == "TARGET-2050.3624"
    assert data["contract"]["real_artifacts_only"] is True
    assert data["contract"]["no_fake_completion"] is True
    assert data["production_policy"]["strict_visuals"] is True
    assert data["production_policy"]["strict_research"] is True


def test_share_urls_are_host_neutral():
    # The closure deliberately emits a relative URL so stale Render/Blitz
    # domains cannot become embedded in stored project metadata.
    url = studio.share_url("proj_12345678", "final.mp4", "u_123456789012")
    assert url.startswith("/infinity/studio/share/")
    assert "onrender.com" not in url
    assert "blitz.cloud" not in url


def test_root_ui_contains_professional_truth_gate():
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "aii-professional-closure-3624" in body
    assert "Production truth gate" in body


def test_alternate_asgi_entrypoint_is_also_wired():
    import ai_infinity_app
    assert any(r.path == "/infinity/studio/system/readiness" for r in ai_infinity_app.app.routes)

def test_short_master_uses_real_media_loop_for_requested_duration(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    out = tmp_path / "out.mp4"
    source.write_bytes(b"real-media-source")
    calls = []

    def fake_ffmpeg(*args, **kwargs):
        calls.append((args, kwargs))
        out.write_bytes(b"real-looped-media" * 1000)

    durations = iter([36.4, 60.0])
    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(studio, "probe_duration", lambda _path: next(durations))

    result = studio._normalize_delivery_duration(source, 60, out)

    assert result == out
    assert out.stat().st_size > 10000
    assert any("-stream_loop" in args and "-1" in args for args, _ in calls)
    assert any("-t" in args and "60.000" in args for args, _ in calls)


def test_strict_visual_gate_keeps_qualified_illustrative_fallback_review_required(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_INFINITY_REQUIRE_SOURCE_VISUALS", "1")
    monkeypatch.setattr(studio, "_hf_video", lambda *a, **k: None)
    monkeypatch.setattr(studio, "_hf_image", lambda *a, **k: None)

    def no_remote(*args, **kwargs):
        return []

    for name in (
        "_nasa_images", "_openverse_images", "_commons_media", "_pexels", "_pixabay",
        "_pexels_photos", "_pixabay_images", "_loc_images",
        "_curated_pexels_launch_video", "_curated_pexels_editorial_photo",
    ):
        monkeypatch.setattr(studio, name, no_remote)

    result = studio.acquire_scene_asset(
        {
            "heading": "Gate test",
            "visual_query": "professional technology workspace",
            "image_prompt": "professional editorial technology workspace",
        },
        Path(tmp_path), 1, prefer_motion=False, duration=5,
    )

    # A qualified original illustration may be retained as a degraded draft,
    # but is never relabelled as professional source footage.
    import production_closure_3624 as closure

    assert result.get("fallback") is True
    assert result.get("quality_tier") == "original_vector_editorial_fallback"
    assert result.get("asset_kind") == "illustrative_graphic_not_photograph"
    assert result.get("fallback_record", {}).get("execution_state") == "DEGRADED"
    assert result.get("fallback_record", {}).get("truthful") is True
    assert closure._is_qualified_illustrative_fallback(result) is True

def _native_primary_fixture(tmp_path, kind):
    import hashlib
    import json

    project_id = "native-primary-" + kind
    required = {
        "article": {"article.md", "seo.json", "fact_check.json"},
        "podcast": {"audio_master.mp3", "podcast_rss.xml"},
        "social": {"social_campaign.json"},
        "website": {"website.zip", "website_manifest.json", "website_README.md"},
        "document": {"document.docx", "document_manifest.json"},
        "presentation": {"presentation.pptx", "presentation_manifest.json"},
    }[kind]
    names = sorted(required | {"sources.json", "rights_manifest.json", "visual_rights.json"})
    files = {}
    rows = []
    metadata = {
        "sources.json": {"truthful": True, "source_count": 2, "sources": [{"url": "https://example.org/source"}]},
        "rights_manifest.json": {
            "project_id": project_id, "truthful": True,
            "assets": [{"asset": "primary-text-output", "rights_state": "review_required"}],
        },
        "visual_rights.json": {
            "project_id": project_id, "truthful": True,
            "assets": [
                {"asset": "scene_01.mp4", "rights_state": "blocked_fallback", "truthful": True},
                {"asset": "scene_02.mp4", "rights_state": "review_required", "truthful": True},
            ],
        },
        "fact_check.json": {"claims": [{"status": "supported_candidate"}]},
        "provenance.json": {"truthful": True, "sources": [{"id": "source-1"}]},
    }
    # Write final metadata bytes before hashing artifacts; the manifest must
    # never carry a digest for pre-metadata placeholder bytes.
    for name, value in metadata.items():
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        files[name] = path
    for name in names:
        path = files.get(name) or (tmp_path / name)
        if name not in files:
            path.write_bytes(("verified primary artifact:" + name).encode("utf-8"))
            files[name] = path
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({"name": name, "role": name, "size_bytes": path.stat().st_size, "sha256": digest})
    primary = {
        "schema": "ai-infinity-primary-content-delivery.v1",
        "project_id": project_id,
        "content_type": kind,
        "status": "PASS",
        "primary_delivery_verified": True,
        "primary_scope_only": True,
        "truthful": True,
        "checks": {"native_artifact_verified": True, "source_and_rights_bound": True},
        "artifacts": rows,
        "research_source_count": 2,
        "companion_video": {"status": "REVIEW_REQUIRED", "fallback_visual_count": 2},
        "human_review_status": "PENDING",
        "rights_review_status": "PENDING",
        "external_publication_status": "NOT_PUBLISHED",
    }
    primary_path = tmp_path / "primary_content_delivery.json"
    primary_path.write_text(json.dumps(primary), encoding="utf-8")
    files[primary_path.name] = primary_path
    primary_sha = hashlib.sha256(primary_path.read_bytes()).hexdigest()
    production_path = tmp_path / "production_manifest.json"
    production_path.write_text(json.dumps({
        "project_id": project_id,
        "truthful": True,
        "primary_content_delivery": {
            "status": "PASS",
            "manifest": "primary_content_delivery.json",
            "sha256": primary_sha,
            "size_bytes": primary_path.stat().st_size,
            "scope": "primary native output only; companion video is gated independently",
        },
    }), encoding="utf-8")
    files[production_path.name] = production_path
    return project_id, files, rows


def test_native_primary_outputs_complete_as_review_required_without_approving_companion_video(monkeypatch, tmp_path):
    import hashlib
    import pytest

    for kind in ("article", "podcast", "social", "website", "document", "presentation"):
        root = tmp_path / kind
        root.mkdir()
        project_id, files, rows = _native_primary_fixture(root, kind)
        registry = {}
        for name, path in files.items():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            registry[name] = {"sha256": digest, "size_bytes": path.stat().st_size}
        monkeypatch.setattr(closure, "_artifact_registry", lambda _pid, value=registry: value)
        checks = {
            # The native deliverable remains eligible even if its optional
            # companion video has failed or is not professionally verified.
            "project_status_completed": False,
            "qualified_illustrative_fallback": False,
            "fallback_visual_count": 2,
            # Companion visuals are explicitly review-required, not cleared.
            # Their open status must block video publication, not erase the
            # separate non-published native draft.
            "visual_rights_evidence_present": False,
            "visual_rights_unknown_count": 0,
            "visual_rights_review_required_count": 2,
            "visual_rights_unresolved_count": 2,
            "research_required_and_present": True,
            "fresh_evidence_ok": True,
            "fact_check_present": True,
            "provenance_present": True,
            "required_artifacts_present": True,
            "artifact_registry_consistent": True,
        }
        assert closure._native_primary_delivery_eligible(
            project_id, {"content_type": kind}, files, checks, []
        ) is True, kind

        assert checks["visual_rights_evidence_present"] is False
        assert primary["rights_review_status"] == "PENDING"
        assert primary["human_review_status"] == "PENDING"
        assert primary["external_publication_status"] == "NOT_PUBLISHED"

        # Open companion-video rights are intentionally not a native-draft
        # approval. An unclassified/malformed rights state must still fail.
        visual_path = files["visual_rights.json"]
        visual_payload = json.loads(visual_path.read_text(encoding="utf-8"))
        visual_payload["assets"][0]["rights_state"] = "unclassified"
        visual_path.write_text(json.dumps(visual_payload), encoding="utf-8")
        visual_digest = hashlib.sha256(visual_path.read_bytes()).hexdigest()
        registry["visual_rights.json"] = {
            "sha256": visual_digest,
            "size_bytes": visual_path.stat().st_size,
        }
        assert closure._native_primary_delivery_eligible(
            project_id, {"content_type": kind}, files, checks, []
        ) is False, kind + " accepted an unclassified rights state"
        visual_payload["assets"][0]["rights_state"] = "blocked_fallback"
        visual_path.write_text(json.dumps(visual_payload), encoding="utf-8")
        registry["visual_rights.json"] = {
            "sha256": hashlib.sha256(visual_path.read_bytes()).hexdigest(),
            "size_bytes": visual_path.stat().st_size,
        }

        # A changed primary artifact still fails closed.
        first_required = next(iter(sorted({row["name"] for row in rows} - {"sources.json", "rights_manifest.json", "visual_rights.json"})))
        files[first_required].write_bytes(b"tampered after primary verification")
        assert closure._native_primary_delivery_eligible(
            project_id, {"content_type": kind}, files, checks, []
        ) is False, kind + " accepted tampered bytes"
        files[first_required].write_bytes(("verified primary artifact:" + first_required).encode("utf-8"))
        checks["visual_rights_unresolved_count"] = 2
        assert closure._native_primary_delivery_eligible(
            project_id, {"content_type": kind}, files, checks, []
        ) is True, kind + " incorrectly coupled native review to companion-video rights"
        checks["visual_rights_unresolved_count"] = 0
        checks["research_required_and_present"] = False
        assert closure._native_primary_delivery_eligible(
            project_id, {"content_type": kind}, files, checks, []
        ) is False, kind + " accepted missing research"


def test_final_audio_master_pads_early_ended_audio_stream_to_video_duration(tmp_path):
    """A 4s video with 2s audio must be mastered with aligned audio, not delivered short."""
    import shutil
    import subprocess

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        import pytest
        pytest.skip("Real FFmpeg stream regression runs only on media-enabled workers")
    source = tmp_path / "short_audio_source.mp4"
    mastered = tmp_path / "duration_aligned.mp4"
    subprocess.run(
        [
            ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=navy:s=320x180:r=24:d=4",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-map", "0:v:0", "-map", "1:a:0", "-t", "4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", str(source),
        ],
        check=True,
        timeout=60,
    )

    source_probe = studio.ffprobe_json(source)
    source_duration = float((source_probe.get("format") or {}).get("duration") or 0)
    source_audio = next(row for row in source_probe["streams"] if row.get("codec_type") == "audio")
    source_audio_duration = float(source_audio.get("duration") or 0)
    assert 3.8 <= source_duration <= 4.2, source_probe
    assert 0 < source_audio_duration < 3.0, source_probe

    result = studio._normalize_delivery_loudness(source, mastered, fast_mode=True)

    output_probe = studio.ffprobe_json(mastered)
    output_duration = float((output_probe.get("format") or {}).get("duration") or 0)
    output_audio = next(row for row in output_probe["streams"] if row.get("codec_type") == "audio")
    audio_duration = float(result["audio_duration_seconds"])
    assert 3.8 <= output_duration <= 4.2, output_probe
    assert abs(audio_duration - output_duration) <= 0.75, (audio_duration, output_duration, output_probe)
    assert result["audio_video_duration_match"] is True
    subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-i", str(mastered), "-f", "null", "-"],
        check=True,
        timeout=60,
    )

def test_final_delivery_loudness_master_applies_post_normalization_sample_peak_ceiling(monkeypatch, tmp_path):
    source = tmp_path / "programme.mp4"
    output = tmp_path / "mastered.mp4"
    source.write_bytes(b"source-programme-bytes" * 1000)
    captured = {}

    probe = {
        "format": {"duration": "20.0"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "duration": "20.0"},
            {"codec_type": "audio", "codec_name": "aac", "duration": "20.0", "sample_rate": "48000"},
        ],
    }

    def fake_ffmpeg(*args, **kwargs):
        captured["args"] = [str(value) for value in args]
        output.write_bytes(b"normalized-and-peak-limited-delivery" * 500)

    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(studio, "ffprobe_json", lambda _path: probe)
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 20.0)

    result = studio._normalize_delivery_loudness(source, output, fast_mode=True)
    args = captured["args"]
    audio_filter = args[args.index("-af") + 1]

    # Final mastering must heal timestamp gaps before sample-based PTS rebuild;
    # otherwise an already-padded AAC stream can still be muxed ~1 second short.
    assert audio_filter.startswith("aresample=48000:async=1:first_pts=0,asetpts=N/SR/TB,")
    assert audio_filter.index("asetpts=N/SR/TB") < audio_filter.index("atrim=duration=")
    assert "loudnorm=I=-16.0:TP=-1.5:LRA=11.0" in audio_filter
    limiter = "alimiter=limit=0.80:attack=5:release=50:level=false"
    assert limiter in audio_filter
    assert audio_filter.index("loudnorm=") < audio_filter.index(limiter), (
        "the hard sample-peak ceiling must run after loudness normalization"
    )
    assert result["status"] == "completed"

def test_music_generation_exception_uses_truthful_emergency_fallback(monkeypatch, tmp_path):
    import hashlib
    import json
    from pathlib import Path

    def broken_music(*args, **kwargs):
        raise RuntimeError("simulated music provider and normal renderer failure")

    def fake_ffmpeg(*args, **kwargs):
        Path(args[-1]).write_bytes(b"verified fake encoded audio bytes " * 80)

    events = []
    monkeypatch.setattr(studio, "make_music", broken_music)
    monkeypatch.setattr(studio, "ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(studio, "probe_duration", lambda _path: 20.0)
    monkeypatch.setattr(
        studio, "audit_event",
        lambda project_id, event, payload: events.append((project_id, event, payload)),
    )

    result = studio._resolve_music_bed(
        tmp_path, 20.0, "cinematic test underscore", "project-music-fallback-test"
    )

    assert result == tmp_path / "music.m4a"
    assert result.is_file() and result.stat().st_size > 1024
    manifest = json.loads((tmp_path / "music_source.json").read_text(encoding="utf-8"))
    assert manifest["method"] == "emergency_procedural_tone_bed_after_music_generation_failure"
    assert manifest["fallback"] is True
    assert manifest["truthful"] is True
    assert manifest["human_rights_review"] == "PENDING"
    assert "not a professionally composed soundtrack" in manifest["quality_notice"]
    assert manifest["reason"] == "simulated music provider and normal renderer failure"
    assert manifest["output_sha256"] == hashlib.sha256(result.read_bytes()).hexdigest()
    assert events[0][0] == "project-music-fallback-test"
    assert events[0][1] == "music_fallback_failed"
    assert any(
        event == "music_fallback" and payload["quality_state"] == "DEGRADED"
        and payload["professional_score_verified"] is False
        for _, event, payload in events
    )


def test_emergency_music_fallback_failure_is_reported_without_name_error(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setattr(
        studio, "make_music",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("normal music failed")),
    )
    monkeypatch.setattr(
        studio, "_emergency_music_fallback",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ffmpeg fallback failed")),
    )
    monkeypatch.setattr(studio, "audit_event", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="emergency procedural audio fallback could not be verified"):
        studio._resolve_music_bed(tmp_path, 20.0, "test", "project-music-fallback-failure")

def test_silent_voice_recovery_helper_creates_measured_audio_track(tmp_path):
    # This path is used by reality_first_3901._safe_render after the primary
    # renderer fails and the scene's voice file is absent. The output is only a
    # silence track to recover the video render; it must not masquerade as speech.
    voice = studio._make_silent_voice(tmp_path, 9, 1.25)

    assert voice.is_file()
    assert voice.name.startswith("silent_voice_09_")
    metadata = studio.ffprobe_json(voice)
    assert any(
        item.get("codec_type") == "audio"
        for item in (metadata.get("streams") or [])
    )
    actual_duration = float((metadata.get("format") or {}).get("duration") or 0.0)
    assert abs(actual_duration - 1.25) <= 0.25

def test_visual_rights_evidence_survives_artifact_registry_read_failure(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    root = tmp_path / "rights-first-test"
    root.mkdir()
    fake_studio = SimpleNamespace(
        _project_dir=lambda _project_id: root,
        _get_project=lambda project_id: {"project_id": project_id},
        _timeline_rows=lambda _project_id: [],
        _asset_rows=lambda _project_id: [],
    )
    monkeypatch.setattr(closure, "_studio", lambda: fake_studio)
    monkeypatch.setattr(
        closure,
        "_artifact_registry",
        lambda _project_id: (_ for _ in ()).throw(RuntimeError("simulated database busy")),
    )

    closure._write_production_evidence("rights-first-test")

    rights = json.loads((root / "visual_rights.json").read_text(encoding="utf-8"))
    registry = json.loads((root / "asset_registry.json").read_text(encoding="utf-8"))
    assert rights["project_id"] == "rights-first-test"
    assert rights["assets"] == []
    assert registry["project_id"] == "rights-first-test"
    assert registry["registry_read_status"] == "unavailable"
    assert "simulated database busy" in registry["registry_read_error"]

def test_studio_health_does_not_treat_a_provider_token_as_video_inference_proof(monkeypatch):
    import production_graph

    monkeypatch.setenv("HF_TOKEN", "configured-but-not-a-live-inference-worker")
    monkeypatch.setattr(
        production_graph,
        "wan_worker_capabilities",
        lambda: {
            "enabled": False,
            "status": "not_configured",
            "reason": "no independently verified GPU worker",
            "truthful": True,
        },
    )

    response = client.get("/infinity/studio/health")

    assert response.status_code == 200, response.text
    state = response.json()
    assert state["ai_video_generation_configured"] is True
    assert state["ai_video_generation"] is False
    assert state["ai_video_generation_provider_available"] is False
    assert state["real_motion_video"] is False
    assert state["native_video_generation"]["status"] == "not_configured"


def test_readiness_does_not_claim_persistent_queue_without_verified_project_storage(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace
    import ai_infinity.persistence_truth as persistence_truth

    monkeypatch.setattr(
        persistence_truth,
        "project_state_durability",
        lambda _root: {
            "persistent": False,
            "reason": "durable_mode_requested_but_backend_not_verified",
            "truthful": True,
        },
    )
    monkeypatch.setattr(
        closure,
        "_studio",
        lambda: SimpleNamespace(_connections=lambda _user_id: []),
    )
    monkeypatch.setattr(closure, "_data_dir", lambda: tmp_path / "runtime-local")

    request = SimpleNamespace(base_url="https://example.test/")
    state = closure._readiness("readiness-test-user", request)

    assert state["storage"]["persistent_runtime"] is False
    assert state["local_capabilities"]["persistent_queue"] is False


def test_workspace_database_catalog_does_not_label_instance_local_sqlite_as_persistent():
    row = next(
        item for item in studio.CLOUD_INFRASTRUCTURE_3621
        if item["id"] == "persistent-sqlite"
    )

    assert row["status"] == "runtime-local"
    assert "restart-safe recovery requires" in row["detail"]
