from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import FastAPI


def _load_storage_fabric(tmp_path: Path, monkeypatch, *, corrupt_readback: bool = False):
    root = tmp_path / "data"
    root.mkdir(parents=True, exist_ok=True)
    database = root / "storage-test.db"
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(root))
    monkeypatch.setenv("AI_INFINITY_STORAGE_PRIMARY", "backblaze_b2")
    monkeypatch.setenv("B2_ENDPOINT", "https://b2.invalid")
    monkeypatch.setenv("B2_BUCKET", "unit-test-bucket")
    monkeypatch.setenv("B2_KEY_ID", "unit-test-access")
    monkeypatch.setenv("B2_APPLICATION_KEY", "unit-test-secret")
    for key in (
        "BACKBLAZE_B2_ENDPOINT", "BACKBLAZE_B2_REGION", "BACKBLAZE_B2_BUCKET",
        "BACKBLAZE_B2_KEY_ID", "BACKBLAZE_B2_APPLICATION_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    def db():
        con = sqlite3.connect(database, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    namespace = {
        "__name__": "ai3704_storage_fabric_test",
        "app": FastAPI(),
        "db": db,
        "_db_lock": threading.RLock(),
        "now": time.time,
        "uid": lambda request: "storage-test-user",
    }
    source = Path("ai3704_storage_fabric.py").read_text(encoding="utf-8")
    exec(compile(source, "ai3704_storage_fabric.py", "exec"), namespace)

    objects: dict[tuple[str, str], dict[str, object]] = {}

    def put(provider, key, path, content_type, sha256):
        payload = path.read_bytes()
        objects[(provider, key)] = {"body": payload, "metadata_sha256": sha256, "content_type": content_type}
        return {"size_bytes": len(payload), "sha256": sha256, "etag": "test-etag"}

    def head(provider, key):
        obj = objects[(provider, key)]
        return {
            "exists": True,
            "configured": True,
            "size_bytes": len(obj["body"]),
            "sha256": obj["metadata_sha256"],
            "etag": "test-etag",
        }

    def download(provider, key, destination):
        obj = objects[(provider, key)]
        payload = obj["body"]
        if corrupt_readback:
            payload = payload + b"-mutated-on-read"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return {"size_bytes": len(payload)}

    namespace["_configured_provider_names"] = lambda: ["backblaze_b2"]
    namespace["_configured"] = lambda cfg: True
    namespace["_put_provider"] = put
    namespace["_head_provider"] = head
    namespace["_download_to"] = download
    return namespace, objects


def test_b2_artifact_storage_reuses_configured_state_credentials(monkeypatch, tmp_path: Path):
    storage, _ = _load_storage_fabric(tmp_path, monkeypatch)
    cfg = storage["_config"]("backblaze_b2")
    assert cfg["endpoint"] == "https://b2.invalid"
    assert cfg["bucket"] == "unit-test-bucket"
    assert cfg["access"] == "unit-test-access"
    assert cfg["secret"] == "unit-test-secret"


def test_b2_artifact_storage_derives_signing_region_from_endpoint(monkeypatch, tmp_path: Path):
    storage, _ = _load_storage_fabric(tmp_path, monkeypatch)
    monkeypatch.setenv("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    monkeypatch.delenv("BACKBLAZE_B2_REGION", raising=False)
    monkeypatch.delenv("B2_REGION", raising=False)

    cfg = storage["_config"]("backblaze_b2")
    assert cfg["endpoint"] == "https://s3.us-east-005.backblazeb2.com"
    assert cfg["region"] == "us-east-005", cfg


def test_explicit_b2_artifact_signing_region_remains_authoritative(monkeypatch, tmp_path: Path):
    storage, _ = _load_storage_fabric(tmp_path, monkeypatch)
    monkeypatch.setenv("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    monkeypatch.setenv("B2_REGION", "eu-central-003")
    monkeypatch.delenv("BACKBLAZE_B2_REGION", raising=False)

    cfg = storage["_config"]("backblaze_b2")
    assert cfg["region"] == "eu-central-003", cfg


def test_s3_storage_client_has_bounded_connect_read_and_retry_settings(monkeypatch, tmp_path: Path):
    storage, _ = _load_storage_fabric(tmp_path, monkeypatch)
    captured = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeBoto3:
        @staticmethod
        def client(service, **kwargs):
            captured["service"] = service
            captured.update(kwargs)
            return object()

    storage["BotoConfig"] = FakeConfig
    storage["boto3"] = FakeBoto3
    monkeypatch.setenv("AI_INFINITY_STORAGE_CONNECT_TIMEOUT", "6")
    monkeypatch.setenv("AI_INFINITY_STORAGE_READ_TIMEOUT", "17")
    monkeypatch.setenv("AI_INFINITY_STORAGE_MAX_ATTEMPTS", "2")

    storage["_client"](storage["_config"]("backblaze_b2"))

    cfg = captured["config"].kwargs
    assert captured["service"] == "s3"
    assert cfg["connect_timeout"] == 6
    assert cfg["read_timeout"] == 17
    assert cfg["retries"] == {"max_attempts": 2, "mode": "standard"}


def test_s3_storage_timeout_settings_are_clamped(monkeypatch, tmp_path: Path):
    storage, _ = _load_storage_fabric(tmp_path, monkeypatch)
    monkeypatch.setenv("AI_INFINITY_STORAGE_CONNECT_TIMEOUT", "999")
    monkeypatch.setenv("AI_INFINITY_STORAGE_READ_TIMEOUT", "0")
    monkeypatch.setenv("AI_INFINITY_STORAGE_MAX_ATTEMPTS", "99")

    assert storage["_storage_http_timeouts"]() == (30, 5)
    assert storage["_bounded_storage_int"]("AI_INFINITY_STORAGE_MAX_ATTEMPTS", 2, 1, 4) == 4


def test_failed_provider_is_circuited_for_later_artifacts_without_fake_durability(monkeypatch, tmp_path: Path):
    storage, _ = _load_storage_fabric(tmp_path, monkeypatch)
    calls = []

    def denied(provider, key, path, content_type, digest):
        calls.append((provider, key))
        raise PermissionError("HTTP 403 Forbidden")

    storage["_put_provider"] = denied
    monkeypatch.setenv("AI_INFINITY_STORAGE_FAILURE_COOLDOWN_SECONDS", "300")
    source = tmp_path / "scene.mp4"
    source.write_bytes(b"production artifact bytes" * 200)

    first = storage["_persist_path"](
        source, "storage-test-user", "scene.mp4", "video/mp4",
        {"project_id": "circuit-breaker-test", "kind": "video"},
    )
    second = storage["_persist_path"](
        source, "storage-test-user", "package.zip", "application/zip",
        {"project_id": "circuit-breaker-test", "kind": "package"},
    )

    assert first["status"] == "failed", first
    assert second["status"] == "failed", second
    assert first["successful_providers"] == []
    assert second["successful_providers"] == []
    assert len(calls) == 1, "a known failing provider must not be retried for every artifact"
    skipped = second["replica_results"]["backblaze_b2"]
    assert skipped["status"] == "failed"
    assert skipped["circuit_open"] is True
    assert "temporarily skipped" in skipped["error"]


def test_artifact_is_verified_only_after_actual_bytes_are_read_back(monkeypatch, tmp_path: Path):
    storage, objects = _load_storage_fabric(tmp_path, monkeypatch)
    source = tmp_path / "final.mp4"
    payload = (b"real-mp4-byte-proof" * 8192) + b"\x00\x00\x00\x18ftypmp42"
    source.write_bytes(payload)

    result = storage["_persist_path"](
        source, "storage-test-user", "final.mp4", "video/mp4",
        {"project_id": "disposable-storage-test", "kind": "final"},
    )
    digest = hashlib.sha256(payload).hexdigest()
    assert result["status"] == "primary_only", result
    assert result["successful_providers"] == ["backblaze_b2"], result
    replica = result["replica_results"]["backblaze_b2"]
    assert replica["status"] == "verified", replica
    assert replica["readback_verified"] is True, replica
    assert replica["verification"]["readback_sha256"] == digest
    assert replica["verification"]["readback_size_bytes"] == len(payload)
    assert objects[("backblaze_b2", result["object_key"])]["body"] == payload


def test_corrupt_object_readback_never_reports_durable_success(monkeypatch, tmp_path: Path):
    storage, _ = _load_storage_fabric(tmp_path, monkeypatch, corrupt_readback=True)
    source = tmp_path / "final.mp4"
    source.write_bytes(b"actual-upload-bytes" * 2048)

    result = storage["_persist_path"](
        source, "storage-test-user", "final.mp4", "video/mp4",
        {"project_id": "disposable-corrupt-test", "kind": "final"},
    )
    assert result["status"] == "failed", result
    assert result["successful_providers"] == [], result
    replica = result["replica_results"]["backblaze_b2"]
    assert replica["status"] == "uploaded_unverified", replica
    assert replica["readback_verified"] is False, replica
    assert "read-back" in replica["error"].lower() or "readback" in replica["error"].lower()


def test_verify_endpoint_full_mode_checks_actual_stored_bytes(monkeypatch, tmp_path: Path):
    storage, _ = _load_storage_fabric(tmp_path, monkeypatch)
    source = tmp_path / "final.mp4"
    source.write_bytes(b"independent-readback-validation" * 1024)
    result = storage["_persist_path"](
        source, "storage-test-user", "final.mp4", "video/mp4",
        {"project_id": "verify-endpoint-test", "kind": "final"},
    )
    asset = result["asset_id"]
    response = storage["storage_verify"](asset, object(), full=True)
    assert response["full_verification"] is True, response
    assert response["status"] == "primary_only", response
    assert response["results"][0]["status"] == "verified", response
    assert response["results"][0]["details"]["readback_verified"] is True, response
