from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import editorial_review_3625 as editorial


def test_scorecard_has_all_twelve_dimensions_and_never_auto_approves(monkeypatch, tmp_path):
    class FakeStudio:
        @staticmethod
        def _project_dir(project_id):
            return tmp_path

    fake_closure = SimpleNamespace(
        _professional_truth=lambda project, reconcile=True: {
            "verified": False,
            "checks": {},
            "failures": ["technical truth is unavailable in this fixture"],
            "warnings": [],
        },
        _source_assets=lambda project: [],
        _visual_diversity_metrics=lambda rows, scene_count: {
            "unique_visual_hashes": 0,
            "required_unique_visual_hashes": 1,
            "visual_diversity_ok": False,
        },
    )
    monkeypatch.setattr(editorial, "_studio", lambda: FakeStudio())
    monkeypatch.setattr(editorial, "_closure", lambda: fake_closure)
    monkeypatch.setattr(editorial, "_project_files", lambda project_id: {})

    result = editorial.build_scorecard({
        "project_id": "fixture-project",
        "title": "Fixture",
        "status": "completed",
        "request_json": {
            "title": "Fixture", "objective": "An educational explainer about energy",
            "duration": 20, "aspect_ratio": "16:9", "language": "English",
            "content_type": "video",
        },
        "blueprint_json": {"plan": {"chapters": []}},
        "result_json": {},
    })

    assert result["schema"] == "ai-infinity.editorial-scorecard.v1"
    assert len(result["dimensions"]) == 12
    assert {x["id"] for x in result["dimensions"]} == {x[0] for x in editorial.DIMENSIONS}
    assert result["release_gate"]["technical_truth_passed"] is False
    assert result["release_gate"]["eligible_for_human_approval"] is False
    assert result["release_gate"]["publish_ready"] is False
    assert result["human_approval"]["required"] is True
    assert result["scoring_policy"]["automatic_publish_approval"] is False
    assert all("evidence" in dimension and "method" in dimension and "limitation" in dimension for dimension in result["dimensions"])


def test_srt_parser_accepts_ordered_cues_and_rejects_overlap():
    good = (
        "1\n00:00:00,000 --> 00:00:01,500\nOpening line\n\n"
        "2\n00:00:01,500 --> 00:00:02,000\nClosing line\n"
    )
    accepted = editorial._srt_metrics(good, 3.0)
    assert accepted["passed"] is True
    assert accepted["cue_count"] == 2

    overlapping = (
        "1\n00:00:00,000 --> 00:00:01,500\nFirst line\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nOverlapping line\n"
    )
    rejected = editorial._srt_metrics(overlapping, 3.0)
    assert rejected["passed"] is False
    assert any("overlap" in err for err in rejected["errors"])


def test_content_fingerprint_changes_when_delivered_media_changes(monkeypatch, tmp_path):
    class FakeStudio:
        @staticmethod
        def _project_dir(project_id):
            return tmp_path

        @staticmethod
        def file_sha256(path):
            h = hashlib.sha256()
            with Path(path).open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()

    video = tmp_path / "final.mp4"
    video.write_bytes(b"original media bytes")
    monkeypatch.setattr(editorial, "_studio", lambda: FakeStudio())
    project = {"project_id": "fingerprint-test", "request_json": {"objective": "brief"}}
    original = editorial._fingerprint(project, {"final.mp4": video})
    video.write_bytes(b"edited media bytes")
    changed = editorial._fingerprint(project, {"final.mp4": video})
    assert original != changed


def test_human_review_state_is_bound_to_the_exact_current_fingerprint():
    rows = [{
        "review_id": "review-1", "content_fingerprint": "old", "decision": "approve",
        "created_at": 1,
    }]
    state = editorial._review_state(rows, "new")
    assert state["state"] == "stale"
    assert state["publish_ready"] is False

    approved = editorial._review_state([{
        "review_id": "review-2", "content_fingerprint": "current",
        "decision": "approve", "created_at": 2,
    }], "current")
    assert approved["state"] == "approved"
    assert approved["publish_ready"] is True


def test_production_app_exposes_editorial_review_and_keeps_approval_explicit():
    import main
    from fastapi.testclient import TestClient

    paths = {getattr(route, "path", "") for route in main.app.router.routes}
    assert "/infinity/studio/project/{project_id}/editorial-scorecard" in paths
    assert "/infinity/studio/project/{project_id}/editorial-review" in paths
    assert "/infinity/studio/project/{project_id}/editorial-reviews" in paths

    client = TestClient(main.app)
    root = client.get("/")
    assert root.status_code == 200
    assert "aii-editorial-review-3625" in root.text
    missing = client.get("/infinity/studio/project/not-a-real-project/editorial-scorecard")
    assert missing.status_code == 404
