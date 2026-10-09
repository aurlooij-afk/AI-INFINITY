from __future__ import annotations

import hashlib
import importlib
import io
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


class _MemoryB2:
    def __init__(self, corrupt_reads: bool = False):
        self.objects = {}
        self.corrupt_reads = corrupt_reads

    def put_object(self, Bucket, Key, Body, ContentType, Metadata):
        payload = Body.read() if hasattr(Body, "read") else bytes(Body)
        self.objects[(Bucket, Key)] = {"body": payload, "metadata": dict(Metadata)}
        return {}

    def head_object(self, Bucket, Key):
        item = self.objects[(Bucket, Key)]
        return {"ContentLength": len(item["body"]), "Metadata": item["metadata"]}

    def get_object(self, Bucket, Key):
        item = self.objects[(Bucket, Key)]
        payload = item["body"] + b"corrupt-readback" if self.corrupt_reads else item["body"]
        return {"Body": io.BytesIO(payload)}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        return {}


def _b2_env(monkeypatch):
    monkeypatch.setenv("AI_INFINITY_PERSISTENCE_MODE", "durable")
    monkeypatch.setenv("AI_INFINITY_PROJECT_STATE_DURABLE", "true")
    monkeypatch.setenv("AI_INFINITY_PROJECT_STATE_BACKEND", "backblaze_b2")
    monkeypatch.setenv("B2_BUCKET", "unit-test-bucket")
    monkeypatch.setenv("B2_ENDPOINT", "https://b2.invalid")
    monkeypatch.setenv("B2_KEY_ID", "test-key-id")
    monkeypatch.setenv("B2_APPLICATION_KEY", "test-key-secret")


def test_b2_canary_requires_exact_write_readback(monkeypatch):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)
    good = _MemoryB2()
    monkeypatch.setattr(backend, "_client", lambda: good)
    assert backend._verify_roundtrip() is True
    assert good.objects == {}

    corrupt = _MemoryB2(corrupt_reads=True)
    monkeypatch.setattr(backend, "_client", lambda: corrupt)
    assert backend._verify_roundtrip() is False
    assert corrupt.objects == {}


def test_snapshot_requires_readback_and_marks_verified_only_on_exact_bytes(monkeypatch, tmp_path: Path):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)
    db_path = tmp_path / "state.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE projects(id INTEGER PRIMARY KEY, title TEXT)")
    con.execute("INSERT INTO projects(title) VALUES(?)", ("roundtrip",))
    con.commit()
    con.close()

    good = _MemoryB2()
    monkeypatch.setattr(backend, "_client", lambda: good)
    monkeypatch.setattr(backend, "_DB_PATH", db_path)
    monkeypatch.setattr(backend, "_STATUS", {
        "configured": True, "active": True, "verified": True,
        "snapshot_verified": False, "backend": "backblaze_b2",
        "last_sync_at": None, "last_sha256": None, "last_error": None,
    })
    monkeypatch.setattr(backend, "_CHANGE_GENERATION", 0)
    result = backend.snapshot_now()
    assert result["status"] == "verified", result
    assert backend.status()["snapshot_verified"] is True
    assert backend.status()["last_sha256"] == result["sha256"]
    assert hashlib.sha256(good.objects[("unit-test-bucket", backend.object_key())]["body"]).hexdigest() == result["sha256"]

    bad = _MemoryB2(corrupt_reads=True)
    monkeypatch.setattr(backend, "_client", lambda: bad)
    backend._STATUS["snapshot_verified"] = False
    failed = backend.snapshot_now()
    assert failed["status"] == "failed", failed
    assert backend.status()["snapshot_verified"] is False


def test_b2_backend_is_not_persistent_until_a_real_snapshot_is_verified(monkeypatch, tmp_path: Path):
    _b2_env(monkeypatch)
    truth = importlib.import_module("ai_infinity.persistence_truth")
    backend = importlib.import_module("ai_infinity.project_state_backend")
    status = {
        "configured": True, "active": True, "verified": True,
        "snapshot_verified": False, "last_sync_at": None,
        "last_sha256": None, "contract_enabled": True, "truthful": True,
    }
    monkeypatch.setattr(backend, "status", lambda: dict(status))
    state = truth.project_state_durability(tmp_path)
    assert state["persistent"] is False
    assert state["reason"] == "durable_mode_requested_but_backend_not_verified"

    status.update(snapshot_verified=True, last_sync_at="2026-10-09T00:00:00Z", last_sha256="a" * 64)
    monkeypatch.setattr(backend, "status", lambda: dict(status))
    state = truth.project_state_durability(tmp_path)
    assert state["persistent"] is True
    assert state["reason"] == "verified_durable_state_backend"
