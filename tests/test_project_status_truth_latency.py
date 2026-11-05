from __future__ import annotations

import reality_first_3901 as kernel
import studio_ultimate as studio


def _project(status: str = "completed") -> dict:
    return {
        "project_id": "status-latency-test",
        "title": "Status latency regression",
        "status": status,
        "state": "COMPLETED",
        "progress": 100.0,
        "stage": "quality_control",
        "current_scene": 5,
        "total_scenes": 5,
        "attempt": 1,
        "error": None,
        "created_at": 1.0,
        "updated_at": 2.0,
        "result_json": "{}",
        "blueprint_json": "{}",
    }


def test_completed_status_poll_never_runs_deep_truth_reconciliation(monkeypatch):
    monkeypatch.setattr(studio, "_asset_rows", lambda project_id: [])
    monkeypatch.setattr(kernel, "cached_truth_for_project", lambda project_id: None)

    def forbidden_deep_reconciliation(project_id):
        raise AssertionError("status polling must not execute media/hash reconciliation")

    monkeypatch.setattr(kernel, "truth_for_project", forbidden_deep_reconciliation)

    result = studio._project_public(_project())

    assert result["status"] == "producing"
    assert result["state"] == "RUNNING"
    assert result["runtime_state"] == "RUNNING"
    assert result["stage"] == "quality_control"
    assert result["progress"] == 99.0
    assert result["truthful"] is True


def test_completed_status_is_exposed_only_for_fresh_verified_cached_truth(monkeypatch):
    monkeypatch.setattr(studio, "_asset_rows", lambda project_id: [])
    monkeypatch.setattr(kernel, "cached_truth_for_project", lambda project_id: {
        "project_id": project_id,
        "state": "VERIFIED",
        "verified": True,
        "qc_passed": True,
        "truthful": True,
    })

    def forbidden_deep_reconciliation(project_id):
        raise AssertionError("status polling must reuse finalized truth, not recompute it")

    monkeypatch.setattr(kernel, "truth_for_project", forbidden_deep_reconciliation)

    result = studio._project_public(_project())

    assert result["status"] == "completed"
    assert result["state"] == "COMPLETED"
    assert result["runtime_state"] == "COMPLETED"
    assert result["truthful"] is True


def test_cached_degraded_truth_cannot_be_promoted_to_completed(monkeypatch):
    monkeypatch.setattr(studio, "_asset_rows", lambda project_id: [])
    monkeypatch.setattr(kernel, "cached_truth_for_project", lambda project_id: {
        "project_id": project_id,
        "state": "DEGRADED",
        "verified": False,
        "qc_passed": False,
        "error": "Visual rights review remains open.",
        "truthful": True,
    })

    result = studio._project_public(_project())

    assert result["status"] == "completed_with_qc_warnings"
    assert result["state"] == "DEGRADED"
    assert result["runtime_state"] == "DEGRADED"
    assert "rights review" in result["error"]

def test_terminal_guard_skips_deep_reinspection_when_cached_truth_is_fresh(monkeypatch):
    fresh = {
        "project_id": "status-latency-test",
        "state": "VERIFIED",
        "verified": True,
        "truthful": True,
    }
    monkeypatch.setattr(kernel, "cached_truth_for_project", lambda project_id: fresh)

    def forbidden_reconciliation(project_id):
        raise AssertionError("unchanged terminal projects must not be re-probed every 20 seconds")

    monkeypatch.setattr(kernel, "reconcile_project", forbidden_reconciliation)

    result = kernel._reconcile_if_stale("status-latency-test")

    assert result["skipped"] is True
    assert result["verified"] is True
    assert result["state"] == "VERIFIED"


def test_terminal_guard_reconciles_when_cached_truth_is_stale(monkeypatch):
    monkeypatch.setattr(kernel, "cached_truth_for_project", lambda project_id: None)
    expected = {
        "project_id": "status-latency-test",
        "state": "DEGRADED",
        "verified": False,
        "truthful": True,
    }
    calls = []
    monkeypatch.setattr(kernel, "reconcile_project", lambda project_id: calls.append(project_id) or expected)

    result = kernel._reconcile_if_stale("status-latency-test")

    assert calls == ["status-latency-test"]
    assert result is expected

def test_intact_required_artifacts_with_open_professional_qc_are_degraded_not_failed(monkeypatch, tmp_path):
    """Creative QC remains review-required; intact customer artifacts are not corruption."""
    import threading

    class Cursor:
        def fetchall(self):
            return []

        def fetchone(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return Cursor()

    class FakeStudio:
        VERSION = "test-studio"
        BUILD = "test-build"
        DB_LOCK = threading.Lock()

        @staticmethod
        def _connect():
            return Connection()

    project = _project("completed")
    project["result_json"] = {"status": "completed", "quality": {"passed": False}}
    record = {
        "project": project,
        "request": {"content_type": "presentation"},
        "plan": {},
        "intent_sha256": "intent",
        "request_sha256": "request",
        "plan_sha256": "plan",
    }
    monkeypatch.setattr(kernel, "_studio", lambda: FakeStudio())
    monkeypatch.setattr(kernel, "_project_record", lambda _project_id: record)
    monkeypatch.setattr(kernel, "_register_project", lambda _project_id: None)
    monkeypatch.setattr(kernel, "_project_path", lambda _project_id: tmp_path)
    monkeypatch.setattr(
        kernel,
        "_verify_required",
        lambda _project_id, _request: (True, {"required_artifact": True}, []),
    )
    monkeypatch.setattr(kernel, "_ensure_schema", lambda: None)
    monkeypatch.setattr(kernel, "_set_truth", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel, "_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel, "_event_once", lambda *_args, **_kwargs: None)

    report = kernel.reconcile_project(project["project_id"])

    assert report["verified"] is False
    assert report["qc_passed"] is False
    assert report["state"] == "DEGRADED"
    assert report["checks"]["required_artifact"] is True
    assert report["missing_or_invalid"] == []
    assert "review-required" in report["error"].lower()
    assert (tmp_path / "reality_proof.json").is_file()


def test_missing_required_artifact_remains_failed_negative_control(monkeypatch, tmp_path):
    """The review-state correction must not turn missing/corrupt artifacts into drafts."""
    import threading

    class Cursor:
        def fetchall(self):
            return []

        def fetchone(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return Cursor()

    class FakeStudio:
        VERSION = "test-studio"
        BUILD = "test-build"
        DB_LOCK = threading.Lock()

        @staticmethod
        def _connect():
            return Connection()

    project = _project("completed")
    project["result_json"] = {"status": "completed", "quality": {"passed": False}}
    record = {
        "project": project,
        "request": {"content_type": "presentation"},
        "plan": {},
        "intent_sha256": "intent",
        "request_sha256": "request",
        "plan_sha256": "plan",
    }
    monkeypatch.setattr(kernel, "_studio", lambda: FakeStudio())
    monkeypatch.setattr(kernel, "_project_record", lambda _project_id: record)
    monkeypatch.setattr(kernel, "_register_project", lambda _project_id: None)
    monkeypatch.setattr(kernel, "_project_path", lambda _project_id: tmp_path)
    monkeypatch.setattr(
        kernel,
        "_verify_required",
        lambda _project_id, _request: (False, {"required_artifact": False}, []),
    )
    monkeypatch.setattr(kernel, "_ensure_schema", lambda: None)
    monkeypatch.setattr(kernel, "_set_truth", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel, "_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel, "_event_once", lambda *_args, **_kwargs: None)

    report = kernel.reconcile_project(project["project_id"])

    assert report["verified"] is False
    assert report["state"] == "FAILED"
    assert report["checks"]["required_artifact"] is False
    assert report["missing_or_invalid"] == ["required_artifact"]


def test_guarded_finalizer_preserves_degraded_review_state_without_approving(monkeypatch):
    project = _project("completed")
    updates = []

    def update(_project_id, **fields):
        project.update(fields)
        updates.append(dict(fields))

    monkeypatch.setattr(kernel, "_studio", lambda: studio)
    monkeypatch.setitem(kernel._ORIGINALS, "run_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel, "_event_once", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel, "_register_project", lambda _project_id: None)
    monkeypatch.setattr(kernel, "reconcile_project", lambda _project_id: {
        "state": "DEGRADED",
        "verified": False,
        "qc_passed": False,
        "error": "Visual review remains required.",
        "truthful": True,
    })
    monkeypatch.setattr(studio, "_get_project", lambda _project_id: project)
    monkeypatch.setattr(studio, "_update_project", update)

    kernel._guarded_run_project(project["project_id"], None)

    assert project["status"] == "completed_with_qc_warnings"
    assert project["stage"] == "quality_review_required"
    assert project["progress"] == 99.0
    assert "review remains required" in project["error"].lower()
    assert updates
    assert not any(item.get("status") == "completed" for item in updates)


def test_guarded_finalizer_still_fails_independent_artifact_proof_negative_control(monkeypatch):
    project = _project("completed")
    updates = []

    def update(_project_id, **fields):
        project.update(fields)
        updates.append(dict(fields))

    monkeypatch.setattr(kernel, "_studio", lambda: studio)
    monkeypatch.setitem(kernel._ORIGINALS, "run_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel, "_event_once", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel, "_register_project", lambda _project_id: None)
    monkeypatch.setattr(kernel, "reconcile_project", lambda _project_id: {
        "state": "FAILED",
        "verified": False,
        "qc_passed": False,
        "error": "Required artifact hash did not match.",
        "truthful": True,
    })
    monkeypatch.setattr(studio, "_get_project", lambda _project_id: project)
    monkeypatch.setattr(studio, "_update_project", update)

    kernel._guarded_run_project(project["project_id"], None)

    assert project["status"] == "failed"
    assert project["stage"] == "quality_control"
    assert "required-artifact proof failed" in project["error"].lower()
    assert any(item.get("status") == "failed" for item in updates)

