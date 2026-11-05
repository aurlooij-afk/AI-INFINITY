from __future__ import annotations

import os

import pytest

import studio_ultimate as studio


def test_runtime_state_contract_is_explicit():
    expected = {
        "queued": "WAITING",
        "running": "RUNNING",
        "producing": "RUNNING",
        "completed": "COMPLETED",
        "completed_with_qc_warnings": "DEGRADED",
        "failed": "FAILED",
        "cancelled": "FAILED",
        "degraded": "DEGRADED",
        "blocked": "BLOCKED",
        "requires_connection": "REQUIRES_CONNECTION",
        "requires_compute": "REQUIRES_COMPUTE",
        "license_limited": "LICENSE_LIMITED",
        "rate_limited": "RATE_LIMITED",
    }
    for raw, state in expected.items():
        assert studio._runtime_state_for_status(raw) == state


def test_external_provider_disable_blocks_remote_research(monkeypatch):
    monkeypatch.setenv("AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS", "1")
    result = studio.research_topic("AI Infinity", limit=3)
    assert result["status"] == "DEGRADED"
    assert result["source_count"] == 0
    assert result["truthful"] is True
    assert all(
        provider.get("status") == "disabled_by_policy"
        for provider in result["providers"].values()
    )


def test_external_provider_disable_forces_local_visual_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS", "1")
    monkeypatch.setenv("AI_INFINITY_REQUIRE_SOURCE_VISUALS", "0")
    asset = studio.acquire_scene_asset(
        {
            "heading": "Local fallback",
            "visual_query": "professional technology workspace",
            "image_prompt": "professional cinematic technology workspace",
        },
        tmp_path,
        1,
        prefer_motion=True,
        duration=5,
    )
    path = tmp_path / str(asset["path"]).split("/")[-1]
    assert path.is_file()
    assert path.stat().st_size > 20_000
    assert asset.get("fallback") is True


def test_review_required_truth_preserves_native_draft_without_approval():
    from reality_first_3901 import _review_state_preservable

    assert _review_state_preservable({"verified": False, "state": "REVIEW_REQUIRED"})
    assert _review_state_preservable({"verified": False, "state": "DEGRADED"})


@pytest.mark.parametrize(
    "report",
    [
        {"verified": True, "state": "REVIEW_REQUIRED"},
        {"verified": False, "state": "FAILED"},
        {"verified": False, "state": "BLOCKED"},
        {"verified": False, "state": None},
        None,
    ],
)
def test_non_reviewable_truth_states_are_not_preserved(report):
    from reality_first_3901 import _review_state_preservable

    assert not _review_state_preservable(report)


@pytest.mark.parametrize(
    ("truth_state", "expected_status"),
    [
        ("REVIEW_REQUIRED", "completed_with_qc_warnings"),
        ("DEGRADED", "completed_with_qc_warnings"),
        ("FAILED", "failed"),
        ("BLOCKED", "failed"),
    ],
)
def test_guarded_runtime_finalizer_applies_review_state_contract(
    monkeypatch, truth_state, expected_status
):
    from types import SimpleNamespace
    import reality_first_3901 as reality

    project = {"status": "completed_with_qc_warnings", "progress": 99}
    updates = []
    fake_studio = SimpleNamespace(
        _get_project=lambda _project_id: dict(project),
        _update_project=lambda _project_id, **changes: (
            updates.append(dict(changes)),
            project.update(changes),
        ),
    )
    monkeypatch.setattr(reality, "_studio", lambda: fake_studio)
    monkeypatch.setitem(
        reality._ORIGINALS,
        "run_project",
        lambda _project_id, _model_fn: None,
    )
    monkeypatch.setattr(reality, "_event_once", lambda *args, **kwargs: None)
    monkeypatch.setattr(reality, "_register_project", lambda *args, **kwargs: None)
    monkeypatch.setattr(reality, "_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        reality,
        "reconcile_project",
        lambda _project_id: {"verified": False, "state": truth_state},
    )

    reality._guarded_run_project("test-native-project", lambda: None)

    assert updates
    assert updates[-1]["status"] == expected_status
