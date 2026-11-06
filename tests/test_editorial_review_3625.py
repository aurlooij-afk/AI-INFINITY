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


def test_content_fingerprint_changes_when_canonical_current_version_changes(monkeypatch, tmp_path):
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
    video.write_bytes(b"same original media bytes")
    monkeypatch.setattr(editorial, "_studio", lambda: FakeStudio())
    project = {"project_id": "canonical-fingerprint-test", "request_json": {"objective": "brief"}}
    monkeypatch.setattr(editorial, "_canonical_current_version_id", lambda _pid: "version-1")
    first = editorial._fingerprint(project, {"final.mp4": video})
    monkeypatch.setattr(editorial, "_canonical_current_version_id", lambda _pid: "version-2")
    second = editorial._fingerprint(project, {"final.mp4": video})
    assert first != second

def test_undo_fingerprint_ignores_noncurrent_historical_version_artifacts(monkeypatch, tmp_path):
    class FakeStudio:
        @staticmethod
        def _project_dir(project_id):
            return tmp_path

        @staticmethod
        def file_sha256(path):
            h = hashlib.sha256()
            with Path(path).open('rb') as stream:
                for chunk in iter(lambda: stream.read(65536), b''):
                    h.update(chunk)
            return h.hexdigest()

    monkeypatch.setattr(editorial, '_studio', lambda: FakeStudio())
    monkeypatch.setattr(editorial, '_canonical_current_version_id', lambda _pid: 'version-original')
    project = {'project_id': 'undo-fingerprint-test', 'request_json': {'objective': 'brief'}}
    final = tmp_path / 'final.mp4'
    historical = tmp_path / 'version-edited.mp4'
    final.write_bytes(b'original artifact bytes')
    before = editorial._fingerprint(project, {'final.mp4': final})
    historical.write_bytes(b'edited historical bytes')
    after_undo = editorial._fingerprint(project, {'final.mp4': final, 'version-edited.mp4': historical})
    assert before == after_undo

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


def test_connected_publish_gate_requires_human_approval_for_current_fingerprint(monkeypatch, tmp_path):
    current_project = {
        "project_id": "publish-gate-test",
        "user_id": "creator-1",
        "updated_at": 100,
        "created_at": 90,
    }

    class FakeStudio:
        @staticmethod
        def _get_project(project_id):
            return current_project if project_id == "publish-gate-test" else None

    monkeypatch.setattr(editorial, "_studio", lambda: FakeStudio())
    monkeypatch.setattr(editorial, "_project_files", lambda project_id: {})
    monkeypatch.setattr(editorial, "_fingerprint", lambda project, files: "fingerprint-current")
    monkeypatch.setattr(editorial, "_read_json", lambda path: {
        "version": editorial.VERSION,
        "content_fingerprint": "fingerprint-current",
        "release_gate": {"eligible_for_human_approval": True},
    })

    monkeypatch.setattr(editorial, "_review_rows", lambda project_id, user_id: [])
    pending = editorial._editorial_publish_status("publish-gate-test", "creator-1")
    assert pending["approved"] is False
    assert pending["state"] == "pending"

    monkeypatch.setattr(editorial, "_review_rows", lambda project_id, user_id: [{
        "review_id": "review-1",
        "content_fingerprint": "old-fingerprint",
        "decision": "approve",
        "created_at": 1,
    }])
    stale = editorial._editorial_publish_status("publish-gate-test", "creator-1")
    assert stale["approved"] is False
    assert stale["state"] == "stale"

    monkeypatch.setattr(editorial, "_review_rows", lambda project_id, user_id: [{
        "review_id": "review-2",
        "content_fingerprint": "fingerprint-current",
        "decision": "approve",
        "created_at": 2,
    }])
    approved = editorial._editorial_publish_status("publish-gate-test", "creator-1")
    assert approved["approved"] is True
    assert approved["state"] == "approved"


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

def _image_review_fixture(tmp_path):
    import json
    from PIL import Image

    image_path = tmp_path / "image.png"
    Image.new("RGB", (1280, 720), (24, 48, 72)).save(image_path, format="PNG")
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    project_id = "image-review-fixture"
    image_manifest = {
        "project_id": project_id,
        "language": "English",
        "output": {"file": "image.png", "sha256": digest, "width": 1280, "height": 720, "format": "PNG"},
        "generation": {"mode": "local_editorial_illustration_fallback", "fallback_used": True},
        "accessibility": {
            "alt_text_draft": "A solar research station in a desert landscape.",
            "status": "DRAFT_REVIEW_REQUIRED",
            "human_review_required": True,
        },
    }
    rights_manifest = {
        "project_id": project_id,
        "status": "REVIEW_REQUIRED",
        "rights_status": "REVIEW_REQUIRED",
        "redistribution_authorized": False,
        "assets": [{"asset": "image.png", "sha256": digest, "rights_status": "REVIEW_REQUIRED", "declared_license": "Original local procedural illustration; review required"}],
    }
    primary = {
        "project_id": project_id,
        "status": "PASS",
        "primary_delivery_verified": True,
        "artifacts": [{"name": "image.png", "sha256": digest}],
        "checks": {"image_exists": True, "image_png_signature": True, "image_sha256_matches": True},
    }
    production = {
        "project_id": project_id,
        "content_type": "image",
        "primary_deliverable": {"file": "image.png", "sha256": digest},
    }
    paths = {}
    for name, value in (
        ("image_manifest.json", image_manifest),
        ("rights_manifest.json", rights_manifest),
        ("primary_content_delivery.json", primary),
        ("production_manifest.json", production),
    ):
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
    paths["image.png"] = image_path
    return project_id, digest, paths


def test_image_scorecard_uses_actual_png_hash_and_keeps_rights_review_open(monkeypatch, tmp_path):
    project_id, digest, paths = _image_review_fixture(tmp_path)

    class FakeStudio:
        @staticmethod
        def file_sha256(path):
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    monkeypatch.setattr(editorial, "_studio", lambda: FakeStudio())
    monkeypatch.setattr(editorial, "_canonical_current_version_id", lambda _pid: "image-version-1")
    project = {
        "project_id": project_id,
        "title": "Solar research station",
        "status": "completed_with_qc_warnings",
        "updated_at": 100,
        "request_json": {
            "title": "Solar research station",
            "content_type": "image",
            "objective": "Create a cinematic image of a solar research station in the desert",
            "language": "English",
            "visual_style": "cinematic editorial",
        },
        "blueprint_json": {},
    }

    card = editorial.build_scorecard(project)

    assert card["content_type"] == "image"
    assert card["technical_truth"]["verified"] is True
    assert card["technical_truth"]["scope"] == "native_primary_image_integrity_only"
    assert card["media"]["image_sha256"] == digest
    assert card["release_gate"]["eligible_for_human_approval"] is True
    assert card["release_gate"]["rights_review_required"] is True
    assert card["release_gate"]["explicit_rights_approval_required"] is True
    assert card["release_gate"]["publish_ready"] is False
    assert card["human_approval"]["state"] == "pending"
    assert all(row["applicable"] for row in card["dimensions"])
    assert "final_mp4_present" not in card["media"]


def test_image_scorecard_blocks_manifest_or_asset_hash_mismatch(monkeypatch, tmp_path):
    import json

    project_id, digest, paths = _image_review_fixture(tmp_path)
    manifest = json.loads(paths["image_manifest.json"].read_text())
    manifest["output"]["sha256"] = "0" * 64
    paths["image_manifest.json"].write_text(json.dumps(manifest), encoding="utf-8")

    class FakeStudio:
        @staticmethod
        def file_sha256(path):
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    monkeypatch.setattr(editorial, "_studio", lambda: FakeStudio())
    monkeypatch.setattr(editorial, "_canonical_current_version_id", lambda _pid: "image-version-1")
    project = {
        "project_id": project_id,
        "title": "Solar research station",
        "status": "completed_with_qc_warnings",
        "request_json": {"title": "Solar research station", "content_type": "image", "objective": "Cinematic solar research station image"},
        "blueprint_json": {},
    }
    card = editorial.build_scorecard(project)
    assert card["technical_truth"]["verified"] is False
    assert "image_manifest_sha256_matches" in card["release_gate"]["critical_failures"]
    assert card["release_gate"]["eligible_for_human_approval"] is False
    assert card["release_gate"]["publish_ready"] is False


def test_image_human_review_requires_six_role_scores_and_explicit_rights_attestation():
    valid = {
        "note": "I inspected the actual image, the visual result and its exact source record.",
        "dimension_scores": {
            "art_direction": 5,
            "visual_design": 5,
            "composition": 4,
            "color": 5,
            "accessibility": 4,
            "rights": 5,
        },
        "rights_approval": True,
        "rights_notes": "Reviewed the local generation method and exact image hash; redistribution is approved for this asset.",
    }
    ok, scores, error = editorial._image_role_review_validation(valid)
    assert ok is True
    assert not error
    assert len(scores) == 6

    no_rights = dict(valid, rights_approval=False)
    ok, _, error = editorial._image_role_review_validation(no_rights)
    assert ok is False and "explicit image rights approval" in error

    weak_average = dict(valid, dimension_scores={key: 4 for key in valid["dimension_scores"]})
    ok, _, error = editorial._image_role_review_validation(weak_average)
    assert ok is False and "average" in error


def test_image_review_state_is_exact_fingerprint_bound_and_not_publish_ready():
    scores = {
        "art_direction": 5,
        "visual_design": 5,
        "composition": 4,
        "color": 5,
        "accessibility": 4,
        "rights": 5,
    }
    rows = [{
        "review_id": "image-review-1",
        "content_fingerprint": "image-fingerprint-current",
        "decision": "approve",
        "dimension_scores_json": json.dumps(scores),
        "rights_approval": 1,
        "rights_notes": "Reviewed the exact image source, generation method and rights record.",
        "created_at": 2,
    }]
    state = editorial._review_state(rows, "image-fingerprint-current")
    assert state["state"] == "approved"
    assert state["review_complete"] is True
    assert state["rights_approved_for_exact_asset"] is True
    assert state["publish_ready"] is False

    stale = editorial._review_state(rows, "different-image-fingerprint")
    assert stale["state"] == "stale"
    assert stale["publish_ready"] is False

    incomplete_rows = [dict(rows[0], rights_approval=0)]
    incomplete = editorial._review_state(incomplete_rows, "image-fingerprint-current")
    assert incomplete["state"] == "incomplete"
    assert incomplete["publish_ready"] is False

