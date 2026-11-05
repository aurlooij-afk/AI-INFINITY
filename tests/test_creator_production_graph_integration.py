from __future__ import annotations

import production_graph
import studio_ultimate


def test_creator_studio_syncs_existing_project_into_production_graph(monkeypatch):
    calls = []
    events = []

    snapshot = {
        "version": "test-graph-v1",
        "project": {"project_id": "studio_existing_123"},
        "summary": {"shots": 7, "candidates": 2},
    }

    def fake_sync(project_id):
        calls.append(project_id)
        return snapshot

    monkeypatch.setattr(production_graph, "sync", fake_sync)
    monkeypatch.setattr(
        studio_ultimate,
        "audit_event",
        lambda project_id, event, payload: events.append((project_id, event, payload)),
    )

    result = studio_ultimate._sync_production_graph("studio_existing_123")

    assert calls == ["studio_existing_123"]
    assert result == {
        "status": "synced",
        "project_id": "studio_existing_123",
        "graph_version": "test-graph-v1",
        "shot_count": 7,
        "candidate_count": 2,
        "truthful": True,
    }
    assert events == [
        (
            "studio_existing_123",
            "production_graph_synchronized",
            result,
        )
    ]


def test_creator_studio_rejects_graph_snapshot_for_different_project(monkeypatch):
    monkeypatch.setattr(
        production_graph,
        "sync",
        lambda _project_id: {
            "project": {"project_id": "another-project"},
            "summary": {"shots": 1, "candidates": 0},
        },
    )

    import pytest

    with pytest.raises(RuntimeError, match="canonical Creator Studio project"):
        studio_ultimate._sync_production_graph("studio_existing_123")
