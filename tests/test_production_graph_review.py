from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import production_graph


def _seed_project(tmp_path: Path, monkeypatch, *, status: str = "ready") -> tuple[Path, Path]:
    db_path = tmp_path / "production-graph.sqlite3"
    monkeypatch.setattr(production_graph, "dbpath", lambda: db_path)
    production_graph.ensure()

    media_root = tmp_path / "production-jobs" / "job_test"
    media_root.mkdir(parents=True)
    media = media_root / "candidate.mp4"
    media.write_bytes(b"candidate video bytes " * 100)

    with production_graph.db() as conn:
        conn.execute(
            "INSERT INTO pg_projects(project_id,user_id,brief_sha,status,stage,progress,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("project-test", "user-test", "brief", "completed", "complete", 100, 1.0),
        )
        conn.execute(
            "INSERT INTO pg_shots(shot_id,project_id,seq,purpose,duration,narration,visual_prompt,status,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("shot-test", "project-test", 1, "Opening shot", 5.0, "Narration", "Visual", "planned", 1.0),
        )
        conn.execute(
            "INSERT INTO pg_candidates(candidate_id,shot_id,project_id,provider,model,status,asset_path,created_at,updated_at,storage_status,storage_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("candidate-test", "shot-test", "project-test", "runway", "gen4.5", status, str(media), 1.0, 1.0,
             "primary_only", json.dumps({"status": "primary_only", "successful_providers": ["test-object-store"]})),
        )
    return db_path, media


def test_video_only_hd_candidate_passes_technical_checks_without_audio_requirement(monkeypatch, tmp_path: Path):
    media = tmp_path / "silent-video.mp4"
    media.write_bytes(b"video bytes " * 200)
    probe = {
        "format": {"duration": "7.5"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}
        ],
    }
    monkeypatch.setattr(
        production_graph.shutil, "which",
        lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else "/usr/bin/ffmpeg",
    )
    def fake_run(args, **kwargs):
        if args[0] == "/usr/bin/ffprobe":
            return SimpleNamespace(returncode=0, stdout=json.dumps(probe), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(production_graph.subprocess, "run", fake_run)

    result = production_graph.tech(media)

    assert result["decision"] == "technical_pass"
    assert result["checks"]["video"] is True
    assert result["checks"]["audio"] is False
    assert result["checks"]["audio_optional"] is True
    assert result["creative_approval"] == "NOT_EVALUATED"


def test_technical_pass_does_not_automatically_approve_candidate_creatively(
    monkeypatch, tmp_path: Path
):
    _db_path, media = _seed_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        production_graph,
        "tech",
        lambda _path: {
            "score": 95,
            "decision": "technical_pass",
            "checks": {"video": True, "audio": False, "hd_resolution": True},
            "creative_approval": "NOT_EVALUATED",
        },
    )

    result = production_graph.review("candidate-test", "user-test")

    with production_graph.db() as conn:
        candidate = conn.execute(
            "SELECT status FROM pg_candidates WHERE candidate_id=?",
            ("candidate-test",),
        ).fetchone()
        shot = conn.execute(
            "SELECT status,winner_candidate_id FROM pg_shots WHERE shot_id=?",
            ("shot-test",),
        ).fetchone()

    assert result["decision"] == "technical_passed"
    assert result["candidate_status"] == "ready_for_creative_review"
    assert result["creative_approval"] == "PENDING"
    assert candidate["status"] == "ready_for_creative_review"
    assert shot["status"] == "planned"
    assert shot["winner_candidate_id"] is None


def test_creator_selection_is_explicit_and_does_not_claim_timeline_insertion(
    monkeypatch, tmp_path: Path
):
    _db_path, _media = _seed_project(tmp_path, monkeypatch, status="ready_for_creative_review")
    audit = []
    monkeypatch.setattr(
        production_graph,
        "s",
        lambda: SimpleNamespace(
            audit_event=lambda *args, **kwargs: audit.append((args, kwargs))
        ),
    )

    result = production_graph.select_for_editor(
        "candidate-test", "user-test", "project-test"
    )

    with production_graph.db() as conn:
        candidate = conn.execute(
            "SELECT status FROM pg_candidates WHERE candidate_id=?",
            ("candidate-test",),
        ).fetchone()
        shot = conn.execute(
            "SELECT status,winner_candidate_id FROM pg_shots WHERE shot_id=?",
            ("shot-test",),
        ).fetchone()

    assert result["status"] == "selected_for_editor"
    assert result["creative_approval"] == "PENDING"
    assert result["editor_insertion"] == "READY_FOR_CANONICAL_RERENDER"
    assert candidate["status"] == "selected_for_editor"
    assert shot["status"] == "selected_for_editor"
    assert shot["winner_candidate_id"] == "candidate-test"
    assert audit


def test_decode_failure_cannot_pass_technical_qc(monkeypatch, tmp_path: Path):
    media = tmp_path / "corrupt-video.mp4"
    media.write_bytes(b"candidate container bytes " * 100)
    probe = {
        "format": {"duration": "7.5"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}
        ],
    }
    monkeypatch.setattr(
        production_graph.shutil, "which",
        lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else "/usr/bin/ffmpeg",
    )
    def fake_run(args, **kwargs):
        if args[0] == "/usr/bin/ffprobe":
            return SimpleNamespace(returncode=0, stdout=json.dumps(probe), stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="corrupt frame")
    monkeypatch.setattr(production_graph.subprocess, "run", fake_run)

    result = production_graph.tech(media)

    assert result["decision"] == "technical_review"
    assert result["checks"]["full_decode"] is False
    assert result["creative_approval"] == "NOT_EVALUATED"


def test_selected_native_candidate_enters_renderer_only_when_clip_covers_scene_duration(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    _db_path, media = _seed_project(tmp_path, monkeypatch, status="selected_for_editor")
    with production_graph.db() as conn:
        conn.execute(
            "UPDATE pg_shots SET winner_candidate_id=?,status=? WHERE shot_id=?",
            ("candidate-test", "selected_for_editor", "shot-test"),
        )
    monkeypatch.setattr(
        production_graph, "tech",
        lambda _path: {
            "decision": "technical_pass",
            "score": 95,
            "checks": {"duration_seconds": 5.0, "full_decode": True, "hd_resolution": True},
            "creative_approval": "NOT_EVALUATED",
        },
    )

    selected = production_graph.selected_candidate_asset("project-test", 1, 5.0)

    assert selected is not None
    assert selected["kind"] == "video"
    assert selected["path"] == str(media.resolve())
    assert selected["generation_method"] == "native_ai_video_generation"
    assert selected["rights_status"] == "REVIEW_REQUIRED"
    assert selected["creative_approval"] == "PENDING"

    with __import__("pytest").raises(RuntimeError, match="shorter than planned scene"):
        production_graph.selected_candidate_asset("project-test", 1, 6.0)


def test_selected_native_candidate_rejects_media_outside_job_asset_root(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    _db_path, media = _seed_project(tmp_path, monkeypatch, status="selected_for_editor")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(media.read_bytes())
    with production_graph.db() as conn:
        conn.execute(
            "UPDATE pg_shots SET winner_candidate_id=?,status=? WHERE shot_id=?",
            ("candidate-test", "selected_for_editor", "shot-test"),
        )
        conn.execute(
            "UPDATE pg_candidates SET asset_path=? WHERE candidate_id=?",
            (str(outside), "candidate-test"),
        )

    with __import__("pytest").raises(RuntimeError, match="outside its verified project/job asset directory"):
        production_graph.selected_candidate_asset("project-test", 1, 5.0)


def test_candidate_without_verified_object_storage_cannot_be_selected(
    monkeypatch, tmp_path: Path
):
    _db_path, _media = _seed_project(tmp_path, monkeypatch, status="ready_for_creative_review")
    with production_graph.db() as conn:
        conn.execute(
            "UPDATE pg_candidates SET storage_status=?,storage_json=? WHERE candidate_id=?",
            ("unconfigured", json.dumps({"status": "unconfigured", "successful_providers": []}), "candidate-test"),
        )

    with __import__("pytest").raises(
        __import__("fastapi").HTTPException,
        match="verified object storage is required",
    ):
        production_graph.select_for_editor("candidate-test", "user-test", "project-test")


def test_selected_candidate_counts_only_when_durably_stored(monkeypatch, tmp_path: Path):
    _db_path, _media = _seed_project(tmp_path, monkeypatch, status="selected_for_editor")
    with production_graph.db() as conn:
        conn.execute(
            "UPDATE pg_shots SET winner_candidate_id=?,status=? WHERE shot_id=?",
            ("candidate-test", "selected_for_editor", "shot-test"),
        )

    assert production_graph.has_selected_candidates("project-test") is True

    with production_graph.db() as conn:
        conn.execute(
            "UPDATE pg_candidates SET storage_status=?,storage_json=? WHERE candidate_id=?",
            ("unconfigured", "{}", "candidate-test"),
        )

    assert production_graph.has_selected_candidates("project-test") is False


def test_selected_candidate_identity_is_bound_to_the_correct_scene(
    monkeypatch, tmp_path: Path
):
    _db_path, _media = _seed_project(tmp_path, monkeypatch, status="selected_for_editor")
    with production_graph.db() as conn:
        conn.execute(
            "UPDATE pg_shots SET winner_candidate_id=?,status=? WHERE shot_id=?",
            ("candidate-test", "selected_for_editor", "shot-test"),
        )

    assert production_graph.selected_candidate_id_for_scene("project-test", 1) == "candidate-test"
    assert production_graph.selected_candidate_id_for_scene("project-test", 2) == ""


def test_runway_candidate_worker_records_verified_storage_and_unique_asset_name(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "production-worker.sqlite3"
    monkeypatch.setattr(production_graph, "dbpath", lambda: db_path)
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_INFINITY_RUNWAY_PAID_USAGE_ACK", "true")
    production_graph.ensure()

    project_id = "project-worker"
    candidate_id = "cand_" + "a" * 32
    job_id = "pgjob_" + "b" * 32
    payload = {"candidate_id": candidate_id, "model": "gen4.5", "prompt": "test shot", "duration": 5}
    with production_graph.db() as conn:
        conn.execute(
            "INSERT INTO pg_projects(project_id,user_id,brief_sha,status,stage,progress,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (project_id, "user-worker", "brief", "completed", "complete", 100, 1.0),
        )
        conn.execute(
            "INSERT INTO pg_shots(shot_id,project_id,seq,purpose,duration,narration,visual_prompt,status,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("shot-worker", project_id, 1, "Shot", 5.0, "Voice", "Visual", "planned", 1.0),
        )
        conn.execute(
            "INSERT INTO pg_candidates(candidate_id,shot_id,project_id,provider,model,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (candidate_id, "shot-worker", project_id, "runway", "gen4.5", "queued", 1.0, 1.0),
        )
        conn.execute(
            "INSERT INTO pg_jobs(job_id,user_id,project_id,shot_id,kind,provider,status,payload_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job_id, "user-worker", project_id, "shot-worker", "video", "runway", "queued",
             json.dumps(payload), 1.0, 1.0),
        )

    monkeypatch.setattr(production_graph, "runway_create", lambda _data: {"id": "remote-task"})
    monkeypatch.setattr(
        production_graph, "runway_poll",
        lambda _task, _limit: {"status": "SUCCEEDED", "output": ["https://provider.example/generated.mp4"]},
    )
    def fake_download(_url, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"generated video candidate " * 100)
        return output
    monkeypatch.setattr(production_graph, "download", fake_download)
    saved = []
    monkeypatch.setattr(
        production_graph,
        "s",
        lambda: SimpleNamespace(
            _save_asset=lambda pid, kind, path, media_type, metadata: (
                saved.append((pid, kind, Path(path), media_type, metadata)) or {
                    "durable_storage": {
                        "status": "primary_only",
                        "asset_id": "ast_verified",
                        "sha256": "a" * 64,
                        "size_bytes": Path(path).stat().st_size,
                        "successful_providers": ["backblaze_b2"],
                        "truthful": True,
                    }
                }
            )
        ),
    )

    production_graph.process({
        "job_id": job_id,
        "project_id": project_id,
        "provider": "runway",
        "kind": "video",
        "payload_json": json.dumps(payload),
    })

    with production_graph.db() as conn:
        candidate = conn.execute(
            "SELECT status,asset_path,storage_status,storage_json FROM pg_candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        job = conn.execute("SELECT status,result_json FROM pg_jobs WHERE job_id=?", (job_id,)).fetchone()

    assert len(saved) == 1
    assert saved[0][1] == "candidate"
    assert saved[0][2].name == candidate_id + ".mp4"
    assert candidate["status"] == "ready"
    assert candidate["storage_status"] == "primary_only"
    assert json.loads(candidate["storage_json"])["successful_providers"] == ["backblaze_b2"]
    assert Path(candidate["asset_path"]).is_file()
    assert job["status"] == "completed"
    assert json.loads(job["result_json"])["storage_status"] == "primary_only"


def test_install_invalidates_only_the_selected_scene_render_cache(monkeypatch, tmp_path: Path):
    import hashlib
    import threading

    db_path = tmp_path / "production-signature.sqlite3"
    monkeypatch.setattr(production_graph, "dbpath", lambda: db_path)
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_INFINITY_PRODUCTION_GRAPH_WORKER", "false")
    monkeypatch.setattr(production_graph, "PATCHED", False)

    production_graph.ensure()
    project_id = "project-signature"
    candidate_id = "cand_" + "c" * 32
    with production_graph.db() as conn:
        conn.execute(
            "INSERT INTO pg_projects(project_id,user_id,brief_sha,status,stage,progress,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (project_id, "user", "brief", "completed", "complete", 100, 1.0),
        )
        conn.execute(
            "INSERT INTO pg_shots(shot_id,project_id,seq,purpose,duration,narration,visual_prompt,status,winner_candidate_id,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("shot-1", project_id, 1, "Shot", 5, "Voice", "Visual", "selected_for_editor", candidate_id, 1.0),
        )

    active = threading.local()
    active.project_id = project_id
    fake_studio = SimpleNamespace(
        _stage=lambda *_args, **_kwargs: None,
        _scene_render_signature=lambda _chapter, _request, index: f"base-{index}",
        _ci_test_visual=lambda *_args, **_kwargs: None,
        acquire_scene_asset=lambda *args, **kwargs: {"kind": "image", "path": "fallback.png"},
        ACTIVE_PROJECT=active,
        _get_project=lambda _pid: {"user_id": "user", "request_json": "{}"},
        audit_event=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(production_graph, "s", lambda: fake_studio)

    production_graph.install()
    selected_signature = fake_studio._scene_render_signature({}, {}, 1)
    untouched_signature = fake_studio._scene_render_signature({}, {}, 2)

    assert selected_signature == hashlib.sha256(
        ("base-1|selected-native-candidate|" + candidate_id).encode()
    ).hexdigest()
    assert untouched_signature == "base-2"
