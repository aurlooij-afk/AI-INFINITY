from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.requests import Request

import professional_creator_v2_timeline_patch as patch


def test_otio_export_recovers_empty_legacy_scaffold_via_normal_save_path(
    monkeypatch, tmp_path: Path
) -> None:
    project_id = "legacy-empty-timeline"
    root = tmp_path / project_id
    root.mkdir()
    # Simulate an old completed project with a saved-but-empty editor scaffold.
    timeline_path = root / "timeline.json"
    timeline_path.write_text(json.dumps({"project_id": project_id, "scenes": []}), encoding="utf-8")
    (root / "scene_01.mp4").write_bytes(b"verified scene master")

    project = {
        "project_id": project_id,
        "user_id": "test-user",
        "status": "completed",
        "title": "Legacy completed project",
    }
    calls: dict[str, list] = {"saved": [], "registered": [], "audits": []}

    def save_asset(pid, kind, path, media_type, metadata=None):
        calls["saved"].append((pid, kind, Path(path).name, dict(metadata or {})))
        return {"durable_storage": {"status": "not_persisted", "successful_providers": []}}

    def register_artifact(pid, path, media_type, metadata=None):
        calls["registered"].append((pid, Path(path).name, media_type, dict(metadata or {})))
        return {"asset_name": Path(path).name}

    def audit_event(pid, event, metadata=None):
        calls["audits"].append((pid, event, dict(metadata or {})))

    studio = SimpleNamespace(
        _get_user_id=lambda _request: "test-user",
        _get_project=lambda pid: project if pid == project_id else None,
        _project_dir=lambda _pid: root,
        _project_artifact_integrity=lambda _pid, _path: True,
        _rehydrate_project_asset=lambda *_args, **_kwargs: None,
        probe_duration=lambda _path: 2.0,
        _save_asset=save_asset,
        register_artifact=register_artifact,
        audit_event=audit_event,
        file_sha256=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    )
    base = SimpleNamespace(
        _timeline=lambda _studio, p: {
            "version": "test",
            "project_id": p["project_id"],
            "title": p["title"],
            "fps": 30,
            "resolution": [1920, 1080],
            "tracks": [{"id": "video", "type": "video"}],
            "duration_seconds": 2.0,
            "scenes": [{
                "source_asset": "scene_01.mp4",
                "source_index": 1,
                "heading": "Recovered scene",
                "narration": "Narration grounded in the completed scene master.",
                "image_prompt": "A recovered scene master",
                "on_screen": "Recovered scene",
                "trim_start": 0,
                "trim_end": 0,
                "duration": 2.0,
                "enabled": True,
            }],
            "truthful": True,
        },
        _safe=lambda value, maximum: str(value or "")[:maximum],
        _truth=lambda *_args: {
            "state": "REVIEW_REQUIRED",
            "verified": False,
            "publish_allowed_by_quality_gate": False,
        },
    )

    def fake_export(bundle_root, pid, project_data, timeline_data, truth):
        assert [row["source_asset"] for row in timeline_data["scenes"]] == ["scene_01.mp4"]
        interchange = root / "project.otio"
        bundle = root / "timeline-export.zip"
        interchange.write_text('{"fake":"test-only export stub"}', encoding="utf-8")
        bundle.write_bytes(b"test-only bundle")
        return {
            "timeline_path": interchange,
            "bundle_path": bundle,
            "timeline_sha256": hashlib.sha256(interchange.read_bytes()).hexdigest(),
            "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "bundle_size_bytes": bundle.stat().st_size,
            "scene_count": 1,
            "media_files": 1,
            "truth_state": "REVIEW_REQUIRED",
            "truth_verified": False,
            "human_review_status": "PENDING",
        }

    monkeypatch.setitem(__import__("sys").modules, "studio_ultimate", studio)
    monkeypatch.setitem(__import__("sys").modules, "professional_creator_v2", base)
    monkeypatch.setattr(patch, "export_otio_bundle", fake_export)

    app = FastAPI()
    patch.install(app)
    endpoint = next(
        route.endpoint for route in app.router.routes
        if getattr(route, "path", "") == "/infinity/pro2/project/{pid}/timeline/otio/export"
    )
    request = Request({
        "type": "http",
        "method": "POST",
        "path": f"/infinity/pro2/project/{project_id}/timeline/otio/export",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 12345),
        "root_path": "",
    })

    response = endpoint(project_id, request)

    saved = json.loads(timeline_path.read_text(encoding="utf-8"))
    assert saved["scenes"], "recovery must replace an empty editor scaffold with usable scenes"
    assert saved["scenes"][0]["source_asset"] == "scene_01.mp4"
    assert any(call[1] == "timeline" and call[2] == "timeline.json" for call in calls["saved"]), (
        "recovery must use the normal save path so durability metadata is recorded"
    )
    recovered_events = [event for _pid, event, _meta in calls["audits"]
                        if event == "professional_timeline_recovered_from_completed_scene_masters"]
    assert len(recovered_events) == 1
    assert recovered_events[0] == "professional_timeline_recovered_from_completed_scene_masters"
    assert response["truth_verified"] is False
    assert response["truthful"] is True

def test_failed_quality_control_project_can_export_review_timeline_only_with_complete_scene_masters(tmp_path: Path) -> None:
    root = tmp_path / "qc-failed-review"
    root.mkdir()
    (root / "final.mp4").write_bytes(b"rendered review master")
    for index in range(1, 8):
        (root / f"scene_{index:02d}.mp4").write_bytes(f"scene-{index}".encode())
    failed_qc = {"status": "failed", "stage": "quality_control", "total_scenes": 7}

    assert patch._otio_export_allowed(failed_qc, root) is True

    (root / "scene_07.mp4").unlink()
    assert patch._otio_export_allowed(failed_qc, root) is False
    assert patch._otio_export_allowed(
        {"status": "failed", "stage": "rendering", "total_scenes": 6}, root
    ) is False
    assert patch._otio_export_allowed(
        {"status": "completed", "total_scenes": 7}, root
    ) is True


def test_qc_failed_generated_timeline_exports_only_real_masters_and_records_omissions(tmp_path: Path) -> None:
    root = tmp_path / "qc-review-partial"
    root.mkdir()
    (root / "final.mp4").write_bytes(b"rendered review master")
    for index in range(1, 9):
        (root / f"scene_{index:02d}.mp4").write_bytes(f"real-scene-{index}".encode())
    project = {"status": "failed", "stage": "quality_control", "total_scenes": 8}
    planned = {
        "scenes": [
            {"source_asset": f"scene_{index:02d}.mp4", "source_index": index,
             "heading": f"Scene {index}", "duration": 2.0, "enabled": True}
            for index in range(1, 10)
        ],
        "duration_seconds": 18.0,
    }

    reviewed = patch._review_only_filter_generated_missing_masters(project, root, planned)

    # Review-only filtering must not mutate a creator's saved plan in place.
    assert len(planned["scenes"]) == 9
    assert planned["scenes"][-1]["source_asset"] == "scene_09.mp4"
    assert [row["source_asset"] for row in reviewed["scenes"]] == [
        f"scene_{index:02d}.mp4" for index in range(1, 9)
    ]
    assert reviewed["review_export"]["status"] == "partial_scene_master_review_only"
    assert reviewed["review_export"]["complete"] is False
    assert reviewed["review_export"]["missing_scene_masters"] == ["scene_09.mp4"]
    assert reviewed["review_export"]["publish_allowed"] is False
    assert patch._otio_export_allowed(project, root) is True


def test_otio_export_filters_missing_master_from_qc_failed_saved_copy_without_rewriting_it(
    monkeypatch, tmp_path: Path
) -> None:
    project_id = "qc-saved-timeline-missing-last-master"
    root = tmp_path / project_id
    root.mkdir()
    (root / "final.mp4").write_bytes(b"real rendered review master")
    for index in range(1, 9):
        (root / f"scene_{index:02d}.mp4").write_bytes(f"real scene master {index}".encode())

    # This saved editor plan contains one stale scene reference. The export path
    # must omit that missing source only in its review copy, preserving the saved plan.
    timeline_path = root / "timeline.json"
    saved_rows = [
        {
            "source_asset": f"scene_{index:02d}.mp4",
            "source_index": index,
            "heading": f"Scene {index}",
            "narration": f"Narration {index}",
            "duration": 2.0,
            "trim_start": 0.0,
            "trim_end": 0.0,
            "enabled": True,
        }
        for index in range(1, 10)
    ]
    timeline_path.write_text(
        json.dumps({"project_id": project_id, "scenes": saved_rows, "truthful": True}),
        encoding="utf-8",
    )
    # This mirrors the real failure mode: the regular project lifecycle says
    # rendering completed, while canonical quality truth says REVIEW_REQUIRED.
    # OTIO export may provide a partial review-only copy without promoting truth.
    project = {
        "project_id": project_id,
        "user_id": "test-user",
        "status": "completed",
        "stage": "complete",
        "total_scenes": 8,
        "title": "QC-unverified creator review",
    }
    calls: dict[str, list] = {"saved": [], "registered": [], "audits": []}

    def save_asset(pid, kind, path, media_type, metadata=None):
        calls["saved"].append((pid, kind, Path(path).name, dict(metadata or {})))
        return {"durable_storage": {"status": "not_persisted", "successful_providers": []}}

    def register_artifact(pid, path, media_type, metadata=None):
        calls["registered"].append((pid, Path(path).name, media_type, dict(metadata or {})))
        return {"asset_name": Path(path).name}

    def audit_event(pid, event, metadata=None):
        calls["audits"].append((pid, event, dict(metadata or {})))

    studio = SimpleNamespace(
        _get_user_id=lambda _request: "test-user",
        _get_project=lambda pid: project if pid == project_id else None,
        _project_dir=lambda _pid: root,
        _project_artifact_integrity=lambda _pid, _path: True,
        _rehydrate_project_asset=lambda *_args, **_kwargs: None,
        probe_duration=lambda _path: 2.0,
        _save_asset=save_asset,
        register_artifact=register_artifact,
        audit_event=audit_event,
        file_sha256=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    )
    base = SimpleNamespace(
        _safe=lambda value, maximum: str(value or "")[:maximum],
        _truth=lambda *_args: {
            "state": "REVIEW_REQUIRED",
            "verified": False,
            "publish_allowed_by_quality_gate": False,
        },
    )

    def fake_export(bundle_root, pid, project_data, timeline_data, truth):
        assert len(timeline_data["scenes"]) == 8
        assert timeline_data["review_export"]["complete"] is False
        assert timeline_data["review_export"]["missing_scene_masters"] == ["scene_09.mp4"]
        assert timeline_data["review_export"]["publish_allowed"] is False
        interchange = root / "project.otio"
        bundle = root / "timeline-export.zip"
        interchange.write_text('{"fake":"review-only OTIO export stub"}', encoding="utf-8")
        bundle.write_bytes(b"review-only portable bundle stub")
        return {
            "timeline_path": interchange,
            "bundle_path": bundle,
            "timeline_sha256": hashlib.sha256(interchange.read_bytes()).hexdigest(),
            "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "bundle_size_bytes": bundle.stat().st_size,
            "scene_count": 8,
            "media_files": 8,
            "truth_state": "REVIEW_REQUIRED",
            "truth_verified": False,
            "human_review_status": "PENDING",
        }

    monkeypatch.setitem(__import__("sys").modules, "studio_ultimate", studio)
    monkeypatch.setitem(__import__("sys").modules, "professional_creator_v2", base)
    monkeypatch.setattr(patch, "export_otio_bundle", fake_export)

    app = FastAPI()
    patch.install(app)
    endpoint = next(
        route.endpoint for route in app.router.routes
        if getattr(route, "path", "") == "/infinity/pro2/project/{pid}/timeline/otio/export"
    )
    request = Request({
        "type": "http",
        "method": "POST",
        "path": f"/infinity/pro2/project/{project_id}/timeline/otio/export",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 12345),
        "root_path": "",
    })

    response = endpoint(project_id, request)

    saved_after = json.loads(timeline_path.read_text(encoding="utf-8"))
    assert len(saved_after["scenes"]) == 9
    assert saved_after["scenes"][-1]["source_asset"] == "scene_09.mp4"
    assert response["scene_count"] == 8
    assert response["truth_verified"] is False
    assert response["human_review_status"] == "PENDING"
    assert response["review_export"]["complete"] is False
    assert response["review_export"]["publish_allowed"] is False


def test_missing_master_filter_never_rewrites_a_completed_project_timeline(tmp_path: Path) -> None:
    root = tmp_path / "completed-project"
    root.mkdir()
    (root / "scene_01.mp4").write_bytes(b"scene 1")
    project = {"status": "completed", "stage": "complete", "total_scenes": 1}
    source = {"scenes": [
        {"source_asset": "scene_01.mp4"}, {"source_asset": "scene_02.mp4"}
    ]}

    assert patch._review_only_filter_generated_missing_masters(project, root, source) is source


def test_ci_controlled_qc_state_mutations_forward_stdin_to_container() -> None:
    """Docker exec must keep stdin attached or Python silently ignores heredocs."""
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "creator-runtime-verify.yml"
    ).read_text(encoding="utf-8")
    assert 'docker exec -i ai-infinity-ci python - "$PROJECT_ID" <<\'PY\'' in workflow
    assert (
        'docker exec -i ai-infinity-ci python - "$PROJECT_ID" '
        '"$original_status" "$original_stage" <<\'PY\''
    ) in workflow
    assert 'docker exec ai-infinity-ci python - "$PROJECT_ID" <<\'PY\'' not in workflow


def test_creator_runtime_full_decode_verifier_imports_subprocess() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "creator-runtime-verify.yml"
    ).read_text(encoding="utf-8")
    marker = 'print("FINAL_MEDIA_DURATION_STREAMS_ASPECT_PASS"'
    marker_index = workflow.index(marker)
    block_start = workflow.rfind("python - <<'PY'", 0, marker_index)
    block_end = workflow.index("\n          PY", marker_index)
    block = workflow[block_start:block_end]
    assert "import subprocess" in block
    assert block.index("import subprocess") < block.index("subprocess.run")


def test_creator_runtime_evidence_uploads_are_split_for_reliable_review() -> None:
    """Keep the default review artifact small; preserve large deliverables separately."""
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "creator-runtime-verify.yml"
    ).read_text(encoding="utf-8")

    review_zip = "zip -qr /tmp/AI_Infinity_ONE_IDEA_INFINITE_WORLDS_human_review.zip . -x 'final.mp4' 'master.mp4' 'selected_visual_*' 'procedural_visual_*' 'pexels_launch_video_*.mp4' 'package.zip' 'named_creator_package.zip' 'timeline-export*.zip'"
    assert review_zip in workflow
    assert "/tmp/ai-infinity-brand-film-review/**" not in workflow

    for artifact_name in (
        "ai-infinity-one-idea-film-deliverables",
        "ai-infinity-creator-package",
        "ai-infinity-editable-timeline-bundle",
        "ai-infinity-source-media-hash-evidence",
    ):
        assert f"name: {artifact_name}" in workflow

    assert "/tmp/ai-infinity-brand-film-review/final.mp4" in workflow
    assert "/tmp/ai-infinity-brand-film-review/package.zip" in workflow
    assert "/tmp/ai-infinity-brand-film-review/timeline-export.zip" in workflow
    assert "/tmp/ai-infinity-brand-film-review/selected_visual_*" in workflow
