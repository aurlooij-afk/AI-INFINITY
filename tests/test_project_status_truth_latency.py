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
