from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import production_intelligence as intelligence


def _db_fixture(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "intelligence.sqlite3"
    media_root = tmp_path / "production-jobs" / "job-test"
    media_root.mkdir(parents=True)
    media = media_root / "candidate-test.mp4"
    media.write_bytes(b"generated video bytes " * 100)

    pg = SimpleNamespace(
        dbpath=lambda: db_path,
        candidate_local_path=lambda _pid, _cid, _uid: media,
        tech=lambda _path: {
            "decision": "technical_pass",
            "score": 95,
            "checks": {"video": True, "full_decode": True, "hd_resolution": True},
            "creative_approval": "NOT_EVALUATED",
        },
    )
    monkeypatch.setattr(intelligence, "pg", lambda: pg)
    monkeypatch.setattr(
        intelligence,
        "studio",
        lambda: SimpleNamespace(_external_providers_disabled=lambda: False),
    )
    with intelligence.db() as conn:
        conn.executescript(
            """
            CREATE TABLE pg_shots(
                shot_id TEXT PRIMARY KEY, project_id TEXT, purpose TEXT, duration REAL,
                narration TEXT, visual_prompt TEXT, metadata TEXT, status TEXT,
                winner_candidate_id TEXT
            );
            CREATE TABLE pg_candidates(
                candidate_id TEXT PRIMARY KEY, shot_id TEXT, project_id TEXT,
                status TEXT, asset_path TEXT, critic_json TEXT DEFAULT '{}',
                score REAL, created_at REAL, updated_at REAL
            );
            CREATE TABLE pg_reviews(
                review_id TEXT PRIMARY KEY, project_id TEXT, shot_id TEXT,
                candidate_id TEXT, reviewer TEXT, score REAL, decision TEXT,
                report_json TEXT, created_at REAL
            );
            INSERT INTO pg_shots VALUES(
                'shot-test','project-test','Opening shot',5.0,'A clear voice line',
                'A cinematic opening','{}','planned',NULL
            );
            INSERT INTO pg_candidates(
                candidate_id,shot_id,project_id,status,asset_path,critic_json,score,created_at,updated_at
            ) VALUES(
                'candidate-test','shot-test','project-test','ready_for_creative_review',
                'PLACEHOLDER','{}',95,1.0,1.0
            );
            """
        )
        conn.execute(
            "UPDATE pg_candidates SET asset_path=? WHERE candidate_id=?",
            (str(media), "candidate-test"),
        )
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    return db_path, media


def test_openai_vision_does_not_call_paid_api_when_owner_ack_is_missing(monkeypatch, tmp_path: Path):
    media = tmp_path / "candidate.mp4"
    media.write_bytes(b"video bytes")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("AI_INFINITY_OPENAI_VISION_PAID_USAGE_ACK", raising=False)
    monkeypatch.setenv("AI_INFINITY_VISION_MODEL", "test-vision-model")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("paid vision API or frame extraction must not run without explicit acknowledgement")

    monkeypatch.setattr(intelligence.requests, "post", forbidden)
    monkeypatch.setattr(intelligence, "frame_image", forbidden)
    monkeypatch.setattr(
        intelligence,
        "studio",
        lambda: SimpleNamespace(_external_providers_disabled=lambda: False),
    )

    result = intelligence.openai_vision(media, {})

    assert result["status"] == "paid_provider_authorization_required"
    assert result["api_request_sent"] is False


def test_openai_vision_requires_explicit_model_name_even_after_ack(monkeypatch, tmp_path: Path):
    media = tmp_path / "candidate.mp4"
    media.write_bytes(b"video bytes")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_INFINITY_OPENAI_VISION_PAID_USAGE_ACK", "true")
    monkeypatch.delenv("AI_INFINITY_VISION_MODEL", raising=False)
    monkeypatch.setattr(
        intelligence,
        "studio",
        lambda: SimpleNamespace(_external_providers_disabled=lambda: False),
    )

    result = intelligence.openai_vision(media, {})

    assert result["status"] == "model_not_configured"
    assert result["api_request_sent"] is False


def test_missing_vision_provider_never_promotes_technical_score_to_creative_approval(
    monkeypatch, tmp_path: Path
):
    _db_path, _media = _db_fixture(tmp_path, monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AI_INFINITY_OPENAI_VISION_PAID_USAGE_ACK", raising=False)
    monkeypatch.delenv("AI_INFINITY_VISION_MODEL", raising=False)

    result = intelligence.vision_review("project-test", "candidate-test", "user-test")

    with intelligence.db() as conn:
        candidate = conn.execute(
            "SELECT status,score,critic_json FROM pg_candidates WHERE candidate_id=?",
            ("candidate-test",),
        ).fetchone()
        review = conn.execute(
            "SELECT reviewer,decision,score,report_json FROM pg_reviews WHERE candidate_id=?",
            ("candidate-test",),
        ).fetchone()

    assert result["score"] is None
    assert result["decision"] == "vision_unavailable"
    assert result["creative_approval"] == "PENDING"
    assert result["human_review_status"] == "PENDING"
    assert candidate["status"] == "ready_for_creative_review"
    assert candidate["score"] == 95
    saved = json.loads(candidate["critic_json"])
    assert saved["vision"]["status"] == "not_configured"
    assert saved["creative_approval"] == "PENDING"
    assert review["reviewer"] == "vision-qc-unavailable"
    assert review["score"] is None
    assert review["decision"] == "vision_vision_unavailable"


def test_autoselect_returns_vision_recommendation_without_mutating_project(
    monkeypatch, tmp_path: Path
):
    _db_path, _media = _db_fixture(tmp_path, monkeypatch)
    report = {
        "vision_score": 92.0,
        "creative_recommendation": "recommended_accept",
        "vision": {"status": "completed"},
    }
    with intelligence.db() as conn:
        conn.execute(
            "UPDATE pg_candidates SET critic_json=? WHERE candidate_id=?",
            (json.dumps(report), "candidate-test"),
        )

    result = intelligence.autoselect("project-test", "user-test", threshold=80)

    with intelligence.db() as conn:
        candidate = conn.execute(
            "SELECT status FROM pg_candidates WHERE candidate_id=?",
            ("candidate-test",),
        ).fetchone()
        shot = conn.execute(
            "SELECT status,winner_candidate_id FROM pg_shots WHERE shot_id=?",
            ("shot-test",),
        ).fetchone()

    assert result["mutated_project"] is False
    assert result["shots"][0]["suggested_candidate_id"] == "candidate-test"
    assert result["shots"][0]["creator_selection_required"] is True
    assert candidate["status"] == "ready_for_creative_review"
    assert shot["status"] == "planned"
    assert shot["winner_candidate_id"] is None


def test_provider_status_does_not_advertise_vision_as_active_from_key_presence_alone(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("AI_INFINITY_OPENAI_VISION_PAID_USAGE_ACK", raising=False)
    monkeypatch.setenv("AI_INFINITY_VISION_MODEL", "test-model")
    monkeypatch.setattr(intelligence, "studio", lambda: SimpleNamespace(_external_providers_disabled=lambda: False))
    monkeypatch.setattr(intelligence.api_anything_connector, "status", lambda: {"status": "not_configured"})

    vision = intelligence.provider_status()["vision"]

    assert vision["configured"] is True
    assert vision["enabled"] is False
    assert vision["status"] == "paid_usage_not_authorized"
    assert vision["pricing_class"] == "paid_metered"


def test_positive_vision_recommendation_does_not_select_or_approve_candidate(
    monkeypatch, tmp_path: Path
):
    _db_path, _media = _db_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        intelligence,
        "openai_vision",
        lambda _path, _shot: {
            "status": "completed",
            "provider": "openai",
            "model": "test-model",
            "narration_alignment": 94,
            "composition": 91,
            "visual_quality": 93,
            "cinematography": 90,
            "continuity": 88,
            "brand_match": 92,
            "artifact_free": 95,
            "specific_issues": [],
            "revision_prompt": "",
            "decision": "accept",
            "truthful": True,
        },
    )

    result = intelligence.vision_review("project-test", "candidate-test", "user-test")

    with intelligence.db() as conn:
        candidate = conn.execute(
            "SELECT status,score,critic_json FROM pg_candidates WHERE candidate_id=?",
            ("candidate-test",),
        ).fetchone()
        shot = conn.execute(
            "SELECT status,winner_candidate_id FROM pg_shots WHERE shot_id=?",
            ("shot-test",),
        ).fetchone()

    assert result["score"] == 91.9
    assert result["decision"] == "recommended_accept"
    assert result["creative_approval"] == "PENDING"
    assert result["human_review_status"] == "PENDING"
    assert candidate["status"] == "ready_for_creative_review"
    assert candidate["score"] == 95
    assert shot["status"] == "planned"
    assert shot["winner_candidate_id"] is None
    saved = json.loads(candidate["critic_json"])
    assert saved["creative_recommendation"] == "recommended_accept"
    assert saved["rights_status"] == "REVIEW_REQUIRED"
