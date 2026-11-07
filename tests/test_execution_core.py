from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import ai_infinity.execution_core as execution_core


def _fake_canonical(tmp_path, monkeypatch, *, route="research", result=None):
    db_path = tmp_path / "execution-ledger.sqlite3"
    events = []
    calls = []

    def connect():
        connection = sqlite3.connect(str(db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    fake = SimpleNamespace(
        studio=lambda: SimpleNamespace(DB_LOCK=RLock()),
        db=connect,
        classify_command=lambda command, payload: {"route": route},
        dispatch_command=lambda command, user_id, payload, request=None: (
            calls.append((command, user_id, dict(payload), request))
            or dict(result or {
                "status": "completed", "route": route, "executed": True,
                "result": {"source_count": 2},
            })
        ),
        event=lambda project_id, user_id, name, data: events.append(
            (project_id, user_id, name, dict(data))
        ),
        current_user=lambda request, response=None: "user-1",
    )
    monkeypatch.setattr(execution_core, "_canonical", lambda: fake)
    return fake, events, calls, db_path


def test_registered_canonical_dispatch_persists_execution_and_scoped_readback(tmp_path, monkeypatch):
    fake, events, calls, db_path = _fake_canonical(tmp_path, monkeypatch)
    result = execution_core.dispatch(
        "Research reliable sources about accessibility",
        "user-1",
        {"limit": 5},
    )

    execution = result["execution"]
    assert execution["capability_id"] == "knowledge.research"
    assert execution["state"] == "completed"
    assert execution["verification"].startswith("delegated_to_domain_evidence")
    assert execution["retry_policy"] == "never_auto_replay"
    assert len(calls) == 1
    assert [item[2] for item in events] == ["execution_started", "execution_settled"]

    stored = execution_core.get_execution(execution["execution_id"], "user-1")
    assert stored["state"] == "completed"
    assert stored["result"]["status"] == "completed"
    assert stored["request_sha256"] == execution["request_sha256"]
    with pytest.raises(HTTPException) as exc:
        execution_core.get_execution(execution["execution_id"], "another-user")
    assert exc.value.status_code == 404


def test_registry_contains_only_bound_routes_and_keeps_guards_non_executable(tmp_path, monkeypatch):
    _fake_canonical(tmp_path, monkeypatch)
    snapshot = execution_core.registry_snapshot()

    assert snapshot["schema"] == "ai-infinity-executable-capability-registry.v1"
    assert snapshot["registry_count"] == len(execution_core.CAPABILITIES)
    assert snapshot["executable_count"] == 14
    assert snapshot["guard_count"] == 5
    assert len({item["id"] for item in snapshot["capabilities"]}) == snapshot["registry_count"]
    assert all(item["handler_bound"] for item in snapshot["capabilities"])
    assert all(item["executable"] is False for item in snapshot["capabilities"] if item["mode"] == "guard")
    assert snapshot["retry_policy"] == "never_auto_replay"


def test_unregistered_route_fails_closed_without_calling_dispatcher(tmp_path, monkeypatch):
    fake, events, calls, db_path = _fake_canonical(tmp_path, monkeypatch, route="future_unregistered_route")

    result = execution_core.dispatch("run a new unregistered operation", "user-1", {})

    assert result["status"] == "unsupported_or_incomplete"
    assert result["executed"] is False
    assert calls == []
    assert events == []
    assert not db_path.exists()


def test_authorization_gate_is_recorded_as_blocked_not_executed(tmp_path, monkeypatch):
    _fake_canonical(
        tmp_path, monkeypatch,
        route="authorization_required",
        result={"status": "authorization_required", "route": "authorization_required", "executed": False},
    )

    result = execution_core.dispatch("publish the project", "user-1", {})

    assert result["execution"]["capability_id"] == "control.authorization_gate"
    assert result["execution"]["state"] == "blocked"
    assert result["executed"] is False


def test_canonical_command_and_batch_paths_share_the_execution_core():
    root = Path(__file__).resolve().parents[1]
    canonical_source = (root / "ai_infinity_canonical.py").read_text(encoding="utf-8")
    batch_source = (root / "universal_command_batch.py").read_text(encoding="utf-8")
    assert "from ai_infinity.execution_core import dispatch as execute_registered" in canonical_source
    assert "dispatch=execution_core.dispatch" in batch_source



def test_batch_dispatch_preserves_request_context_for_operator_authorized_capabilities():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "universal_command_batch.py").read_text(encoding="utf-8")
    assert "def dispatch_registered(command: str, owner_id: str, command_payload: dict[str, Any]):" in source
    assert "command, owner_id, command_payload, request=request," in source
    assert "dispatch=dispatch_registered" in source
