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
        "BACKBLAZE_B2_ENDPOINT",
        "BACKBLAZE_B2_BUCKET",
        "BACKBLAZE_B2_KEY_ID",
        "BACKBLAZE_B2_APPLICATION_KEY",
        "BACKBLAZE_B2_REGION",
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


def test_durable_connection_schedules_schema_only_commit(monkeypatch, tmp_path: Path):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    calls = []
    monkeypatch.setattr(backend, "schedule_sync", lambda *args, **kwargs: calls.append(True))

    con = sqlite3.connect(
        tmp_path / "schema-only.db",
        factory=backend.DurableConnection,
        check_same_thread=False,
    )
    with con:
        con.execute("CREATE TABLE schema_change_only (value TEXT)")
    assert calls == [True], "schema-only DDL must trigger a durable checkpoint"
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
    for key in (
        "BACKBLAZE_B2_ENDPOINT",
        "BACKBLAZE_B2_BUCKET",
        "BACKBLAZE_B2_KEY_ID",
        "BACKBLAZE_B2_APPLICATION_KEY",
        "BACKBLAZE_B2_REGION",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("B2_BUCKET", "unit-test-bucket")
    monkeypatch.setenv("B2_ENDPOINT", "https://b2.invalid")
    monkeypatch.setenv("B2_KEY_ID", "test-key-id")
    monkeypatch.setenv("B2_APPLICATION_KEY", "test-key-secret")



def test_project_state_prefers_shared_backblaze_storage_credentials(monkeypatch):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)
    monkeypatch.setenv("BACKBLAZE_B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    monkeypatch.setenv("BACKBLAZE_B2_REGION", "us-east-005")
    monkeypatch.setenv("BACKBLAZE_B2_BUCKET", "shared-artifact-bucket")
    monkeypatch.setenv("BACKBLAZE_B2_KEY_ID", "shared-key-id")
    monkeypatch.setenv("BACKBLAZE_B2_APPLICATION_KEY", "shared-application-key")

    cfg = backend._cfg()
    assert cfg["endpoint"] == "https://s3.us-east-005.backblazeb2.com"
    assert cfg["region"] == "us-east-005"
    assert cfg["bucket"] == "shared-artifact-bucket"
    assert cfg["access"] == "shared-key-id"
    assert cfg["secret"] == "shared-application-key"


def test_project_state_derives_endpoint_from_shared_b2_region(monkeypatch):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)
    monkeypatch.delenv("BACKBLAZE_B2_ENDPOINT", raising=False)
    monkeypatch.delenv("B2_ENDPOINT", raising=False)
    monkeypatch.setenv("BACKBLAZE_B2_REGION", "eu-central-003")

    cfg = backend._cfg()
    assert cfg["endpoint"] == "https://s3.eu-central-003.backblazeb2.com"
    assert cfg["region"] == "eu-central-003"


def test_project_state_derives_signing_region_from_b2_endpoint_when_region_is_unset(monkeypatch):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)
    monkeypatch.setenv("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    monkeypatch.delenv("BACKBLAZE_B2_REGION", raising=False)
    monkeypatch.delenv("B2_REGION", raising=False)

    cfg = backend._cfg()

    assert cfg["endpoint"] == "https://s3.us-east-005.backblazeb2.com"
    assert cfg["region"] == "us-east-005", cfg


def test_explicit_project_state_region_overrides_endpoint_inference(monkeypatch):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)
    monkeypatch.setenv("BACKBLAZE_B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    monkeypatch.setenv("BACKBLAZE_B2_REGION", "eu-central-003")

    cfg = backend._cfg()

    assert cfg["endpoint"] == "https://s3.us-east-005.backblazeb2.com"
    assert cfg["region"] == "eu-central-003", cfg


def test_configure_retries_transient_startup_canary_error(monkeypatch, tmp_path: Path):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)
    calls = []
    delays = []

    def transient_canary():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("temporary B2 service error")
        return True

    monkeypatch.setattr(backend, "_verify_roundtrip", transient_canary)
    monkeypatch.setattr(
        backend,
        "restore_on_startup",
        lambda: {"restored": False, "reason": "local_database_present", "truthful": True},
    )
    monkeypatch.setattr(backend.time, "sleep", lambda delay: delays.append(delay))

    result = backend.configure(tmp_path / "state.db")

    assert len(calls) == 2, result
    assert delays == [1.0]
    assert result["configured"] is True
    assert result["active"] is True
    assert result["verified"] is True
    assert result["last_error"] is None
    assert result["snapshot_verified"] is False, "canary verification alone is not a saved snapshot"


def test_configure_fails_closed_after_bounded_startup_canary_errors(monkeypatch, tmp_path: Path):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)
    calls = []
    delays = []

    def persistent_canary_failure():
        calls.append(True)
        raise RuntimeError("B2 permissions or service failure")

    monkeypatch.setattr(backend, "_verify_roundtrip", persistent_canary_failure)
    monkeypatch.setattr(backend.time, "sleep", lambda delay: delays.append(delay))

    result = backend.configure(tmp_path / "state.db")

    assert len(calls) == backend._MAX_CANARY_VERIFY_ATTEMPTS, result
    assert delays == [1.0, 2.0]
    assert result["configured"] is True
    assert result["active"] is False
    assert result["verified"] is False
    assert "B2 permissions or service failure" in str(result["last_error"])


def test_configure_reports_verified_startup_restore(monkeypatch, tmp_path: Path):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)

    source_db = tmp_path / "remote-seed.db"
    with sqlite3.connect(source_db) as con:
        con.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT)")
        con.execute("INSERT INTO projects(title) VALUES(?)", ("restored project",))
    payload = source_db.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()

    client = _MemoryB2()
    client.objects[("unit-test-bucket", backend.object_key())] = {
        "body": payload,
        "metadata": {
            "ai-infinity-sha256": digest,
            "ai-infinity-snapshot-at": "1792756000.0",
            "ai-infinity-schema": "studio-state-v1",
        },
    }
    monkeypatch.setattr(backend, "_client", lambda: client)

    target = tmp_path / "fresh-instance" / "state.db"
    result = backend.configure(target)

    assert result["active"] is True, result
    assert result["snapshot_verified"] is True, result
    restore = result["last_restore"]
    assert restore["attempted"] is True, restore
    assert restore["restored"] is True, restore
    assert restore["status"] == "restored", restore
    assert restore["sha256"] == digest, restore
    assert restore["size_bytes"] == len(payload), restore
    with sqlite3.connect(target) as con:
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert con.execute("SELECT title FROM projects").fetchone()[0] == "restored project"


def test_default_b2_canary_uses_the_configured_snapshot_key_prefix(monkeypatch):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)
    monkeypatch.setenv(
        "AI_INFINITY_PROJECT_STATE_OBJECT_KEY",
        "tenant/project-state/current.snapshot",
    )
    monkeypatch.delenv("AI_INFINITY_PROJECT_STATE_CANARY_KEY", raising=False)

    class TrackingMemoryB2(_MemoryB2):
        allowed_prefix = "tenant/project-state/current.snapshot"
        last_put_key = None

        def _require_allowed_prefix(self, key):
            if not key.startswith(self.allowed_prefix):
                raise PermissionError("outside the test B2 key-name prefix")

        def put_object(self, Bucket, Key, Body, ContentType, Metadata):
            self._require_allowed_prefix(Key)
            self.last_put_key = Key
            return super().put_object(Bucket, Key, Body, ContentType, Metadata)

        def head_object(self, Bucket, Key):
            self._require_allowed_prefix(Key)
            return super().head_object(Bucket, Key)

        def get_object(self, Bucket, Key):
            self._require_allowed_prefix(Key)
            return super().get_object(Bucket, Key)

        def delete_object(self, Bucket, Key):
            self._require_allowed_prefix(Key)
            return super().delete_object(Bucket, Key)

    client = TrackingMemoryB2()
    monkeypatch.setattr(backend, "_client", lambda: client)

    assert backend._verify_roundtrip() is True
    assert client.last_put_key.startswith(
        "tenant/project-state/current.snapshot.canary."
    )
    assert client.objects == {}, "successful canary verification should clean up its unique object"


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


def test_snapshot_timer_retries_a_transient_object_store_failure(monkeypatch):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    monkeypatch.setattr(backend, "_STATUS", {
        "configured": True, "active": True, "verified": True,
        "snapshot_verified": False, "backend": "backblaze_b2",
        "last_sync_at": None, "last_sha256": None, "last_error": None,
    })
    monkeypatch.setattr(backend, "_CHANGE_GENERATION", 0)
    monkeypatch.setattr(backend, "_SYNC_DIRTY", False)
    monkeypatch.setattr(backend, "_SYNC_FAILURES", 0)
    monkeypatch.setattr(backend, "_SYNC_TIMER", None)

    scheduled = []

    class FakeTimer:
        def __init__(self, interval, callback, *args, **kwargs):
            self.interval = interval
            self.callback = callback
            self.args = args
            self.kwargs = kwargs
            self.daemon = False
            self.started = False

        def start(self):
            self.started = True
            scheduled.append(self)

        def is_alive(self):
            return self.started

    monkeypatch.setattr(backend.threading, "Timer", FakeTimer)
    calls = []

    def transient_then_verified():
        calls.append(True)
        if len(calls) == 1:
            backend._STATUS["snapshot_verified"] = False
            backend._STATUS["last_error"] = "ClientError: temporary B2 failure"
            return {"status": "failed", "truthful": True}
        backend._STATUS["snapshot_verified"] = True
        backend._STATUS["last_sync_at"] = "2026-10-23T14:00:00Z"
        backend._STATUS["last_sha256"] = "b" * 64
        backend._STATUS["last_error"] = None
        return {"status": "verified", "truthful": True}

    monkeypatch.setattr(backend, "snapshot_now", transient_then_verified)
    backend._snapshot_timer_job()

    assert len(calls) == 1
    assert backend.status()["snapshot_verified"] is False
    assert backend._SYNC_FAILURES == 1
    assert len(scheduled) == 1
    retry = scheduled.pop(0)
    assert retry.interval == 1.0
    retry.callback()

    assert len(calls) == 2
    assert backend.status()["snapshot_verified"] is True
    assert backend._SYNC_FAILURES == 0
    assert backend._SYNC_TIMER is None


def test_snapshot_timer_stops_after_bounded_transient_failures(monkeypatch):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    monkeypatch.setattr(backend, "_STATUS", {
        "configured": True, "active": True, "verified": True,
        "snapshot_verified": False, "backend": "backblaze_b2",
        "last_sync_at": None, "last_sha256": None, "last_error": None,
    })
    monkeypatch.setattr(backend, "_CHANGE_GENERATION", 0)
    monkeypatch.setattr(backend, "_SYNC_DIRTY", False)
    monkeypatch.setattr(backend, "_SYNC_FAILURES", 0)
    monkeypatch.setattr(backend, "_SYNC_TIMER", None)

    scheduled = []
    delays = []

    class FakeTimer:
        def __init__(self, interval, callback, *args, **kwargs):
            self.interval = interval
            self.callback = callback
            self.args = args
            self.kwargs = kwargs
            self.daemon = False
            self.started = False

        def start(self):
            self.started = True
            scheduled.append(self)
            delays.append(self.interval)

        def is_alive(self):
            return self.started

    monkeypatch.setattr(backend.threading, "Timer", FakeTimer)
    calls = []

    def always_fails():
        calls.append(True)
        backend._STATUS["snapshot_verified"] = False
        backend._STATUS["last_error"] = "ClientError: persistent B2 failure"
        return {"status": "failed", "truthful": True}

    monkeypatch.setattr(backend, "snapshot_now", always_fails)
    backend._snapshot_timer_job()
    while scheduled:
        timer = scheduled.pop(0)
        timer.callback()

    assert len(calls) == backend._MAX_SNAPSHOT_RETRIES + 1
    assert delays == [1.0, 2.0, 4.0]
    assert backend._SYNC_FAILURES == backend._MAX_SNAPSHOT_RETRIES
    assert backend._SYNC_TIMER is None
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


def test_restore_verifies_checksum_and_sqlite_before_replacing_target(monkeypatch, tmp_path: Path):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)
    source = tmp_path / "source.db"
    con = sqlite3.connect(source)
    con.execute("CREATE TABLE durable_state(id INTEGER PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO durable_state(value) VALUES(?)", ("must-survive-restart",))
    con.commit()
    con.close()
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()

    target_dir = tmp_path / "restored"
    target_dir.mkdir()
    target = target_dir / "state.db"
    good = _MemoryB2()
    good.objects[("unit-test-bucket", backend.object_key())] = {
        "body": payload,
        "metadata": {"ai-infinity-sha256": digest, "ai-infinity-snapshot-at": "2026-10-09T00:00:00Z"},
    }
    monkeypatch.setattr(backend, "_client", lambda: good)
    monkeypatch.setattr(backend, "_DB_PATH", target)
    monkeypatch.setattr(backend, "_STATUS", {
        "configured": True, "active": True, "verified": True,
        "snapshot_verified": False, "backend": "backblaze_b2",
        "last_sync_at": None, "last_sha256": None, "last_error": None,
    })
    monkeypatch.setattr(backend, "_CHANGE_GENERATION", 3)
    monkeypatch.setattr(backend, "_SYNC_DIRTY", True)

    result = backend.restore_on_startup()
    assert result["restored"] is True, result
    assert target.is_file()
    check_db = sqlite3.connect(target)
    try:
        assert check_db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert check_db.execute("SELECT value FROM durable_state").fetchone()[0] == "must-survive-restart"
    finally:
        check_db.close()
    assert backend.status()["snapshot_verified"] is True
    assert backend.status()["last_sha256"] == digest
    assert backend._CHANGE_GENERATION == 0
    assert backend._SYNC_DIRTY is False


def test_restore_refuses_mismatched_checksum_and_corrupt_sqlite(monkeypatch, tmp_path: Path):
    backend = importlib.import_module("ai_infinity.project_state_backend")
    _b2_env(monkeypatch)
    valid_source = tmp_path / "source.db"
    con = sqlite3.connect(valid_source)
    con.execute("CREATE TABLE t(value TEXT)")
    con.execute("INSERT INTO t(value) VALUES('ok')")
    con.commit()
    con.close()
    valid_bytes = valid_source.read_bytes()

    cases = [
        ("wrong-checksum", valid_bytes, "0" * 64),
        ("corrupt-sqlite", b"this is not a SQLite database", hashlib.sha256(b"this is not a SQLite database").hexdigest()),
    ]
    for label, payload, metadata_sha in cases:
        target_dir = tmp_path / label
        target_dir.mkdir()
        target = target_dir / "state.db"
        fake = _MemoryB2()
        fake.objects[("unit-test-bucket", backend.object_key())] = {
            "body": payload,
            "metadata": {"ai-infinity-sha256": metadata_sha, "ai-infinity-snapshot-at": "2026-10-09T00:00:00Z"},
        }
        monkeypatch.setattr(backend, "_client", lambda store=fake: store)
        monkeypatch.setattr(backend, "_DB_PATH", target)
        monkeypatch.setattr(backend, "_STATUS", {
            "configured": True, "active": True, "verified": True,
            "snapshot_verified": False, "backend": "backblaze_b2",
            "last_sync_at": None, "last_sha256": None, "last_error": None,
        })
        result = backend.restore_on_startup()
        assert result["restored"] is False, (label, result)
        assert not target.exists(), f"{label}: unverified bytes replaced the target database"
        assert backend.status()["snapshot_verified"] is False

def test_storage_error_diagnostics_expose_provider_code_without_secrets():
    backend = importlib.import_module("ai_infinity.project_state_backend")

    class FakeProviderError(Exception):
        operation_name = "HeadObject"
        response = {
            "Error": {
                "Code": "AccessDenied",
                "Message": "Access Denied",
            },
            "ResponseMetadata": {"HTTPStatusCode": 403, "RequestId": "request-123"},
        }

    detail = backend._describe_storage_error(FakeProviderError("sensitive=never-log"))
    assert "code=AccessDenied" in detail
    assert "http_status=403" in detail
    assert "operation=HeadObject" in detail
    assert "never-log" not in detail

    scrubbed = backend._safe_error_text(
        "ClientError: https://example.invalid/object?X-Amz-Signature=secret "
        "application_key=do-not-log authorization=BearerSecret"
    )
    assert "https://example.invalid" not in scrubbed
    assert "secret" not in scrubbed
    assert "do-not-log" not in scrubbed
    assert "BearerSecret" not in scrubbed

def test_render_blueprint_does_not_override_b2_endpoint_signing_region():
    """A hard-coded blueprint region must not conflict with the operator's B2 endpoint."""
    root = Path(__file__).resolve().parents[1]
    blueprint = (root / "render.yaml").read_text(encoding="utf-8")
    assert '      - key: B2_REGION\\n        value: ""' in blueprint
    assert '      - key: B2_REGION\\n        value: "us-west-004"' not in blueprint
