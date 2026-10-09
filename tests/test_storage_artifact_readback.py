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
