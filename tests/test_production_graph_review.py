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
            "INSERT INTO pg_candidates(candidate_id,shot_id,project_id,provider,model,status,asset_path,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("candidate-test", "shot-test", "project-test", "runway", "gen4.5", status, str(media), 1.0, 1.0),
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
    monkeypatch.setattr(production_graph.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        production_graph.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(probe), stderr=""
        ),
    )

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
    assert result["editor_insertion"] == "NOT_IMPLEMENTED"
    assert candidate["status"] == "selected_for_editor"
    assert shot["status"] == "selected_for_editor"
    assert shot["winner_candidate_id"] == "candidate-test"
    assert audit
