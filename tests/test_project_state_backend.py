from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path


def test_backend_is_truthful_when_unconfigured(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AI_INFINITY_PERSISTENCE_MODE", "durable")
    monkeypatch.setenv("AI_INFINITY_PROJECT_STATE_DURABLE", "true")
    monkeypatch.setenv("AI_INFINITY_PROJECT_STATE_BACKEND", "backblaze_b2")
    for key in (
        "B2_ENDPOINT",
        "B2_BUCKET",
        "B2_KEY_ID",
        "B2_APPLICATION_KEY",
        "B2_REGION",
    ):
        monkeypatch.delenv(key, raising=False)

    backend = importlib.import_module("ai_infinity.project_state_backend")
    backend.configure(tmp_path / "state.db")
    state = backend.status()

    assert state["contract_enabled"] is True
    assert state["configured"] is False
    assert state["active"] is False
    assert state["verified"] is False
    assert state["truthful"] is True


def test_durable_connection_schedules_only_after_committed_write(monkeypatch, tmp_path: Path):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    calls = []
    monkeypatch.setattr(backend, "schedule_sync", lambda *args, **kwargs: calls.append(True))

    con = sqlite3.connect(
        tmp_path / "state.db",
        factory=backend.DurableConnection,
        check_same_thread=False,
    )
    with con:
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO t(value) VALUES(?)", ("ok",))
    assert calls == [True]

    calls.clear()
    with con:
        con.execute("SELECT * FROM t").fetchall()
    assert calls == []
    con.close()


def test_durable_connection_does_not_schedule_after_rollback(monkeypatch, tmp_path: Path):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    calls = []
    monkeypatch.setattr(backend, "schedule_sync", lambda *args, **kwargs: calls.append(True))

    con = sqlite3.connect(
        tmp_path / "rollback.db",
        factory=backend.DurableConnection,
        check_same_thread=False,
    )
    with con:
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)")
    calls.clear()

    try:
        with con:
            con.execute("INSERT INTO t(value) VALUES(?)", ("rolled-back",))
            raise RuntimeError("rollback")
    except RuntimeError:
        pass

    assert calls == []
    con.close()
