from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import studio_ultimate as studio


@pytest.mark.parametrize("corrupt_remote_bytes", [False, True])
def test_forced_timeline_rehydration_requires_actual_size_and_sha256(
    monkeypatch, tmp_path: Path, corrupt_remote_bytes: bool
) -> None:
    """Restore edited timeline state from an object replica only after byte verification."""
    database = tmp_path / "creator-state.db"
    project_id = "project-rehydration-test"
    user_id = "rehydration-test-user"
    project_root = tmp_path / "projects" / project_id
    project_root.mkdir(parents=True)
    target = project_root / "timeline.json"
    corrupt_local = b'{"scenes":[{"heading":"local file is corrupt"}]}'
    canonical_payload = (
        b'{"project_id":"project-rehydration-test","scenes":'
        b'[{"source_asset":"scene_01.mp4","heading":"verified restored timeline"}]}'
    )
    expected_sha = hashlib.sha256(canonical_payload).hexdigest()
    target.write_bytes(corrupt_local)

    def connect():
        db = sqlite3.connect(database, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    with connect() as db:
        db.execute(
            """
            CREATE TABLE studio_assets_3610(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                media_type TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        db.execute(
            "INSERT INTO studio_assets_3610(project_id,kind,path,media_type,metadata_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                project_id,
                "timeline",
                str(target),
                "application/json",
                json.dumps({"durable_storage": {
                    "asset_id": "ast_verified_timeline",
                    "status": "primary_only",
                    "successful_providers": ["backblaze_b2"],
                    "sha256": expected_sha,
                    "size_bytes": len(canonical_payload),
                }}),
                1.0,
            ),
        )

    downloads = []

    def download_to(provider: str, object_key: str, destination: Path):
        downloads.append((provider, object_key))
        payload = canonical_payload + (b"-tampered" if corrupt_remote_bytes else b"")
        destination.write_bytes(payload)
        return {"size_bytes": len(payload)}

    storage_stub = SimpleNamespace(
        _asset_row=lambda asset_id, uid: {
            "asset_id": asset_id,
            "status": "primary_only",
            "object_key": "ai-infinity/user/ast_verified_timeline/timeline.json",
            "size_bytes": len(canonical_payload),
            "sha256": expected_sha,
            "primary_provider": "backblaze_b2",
            "replicas": [{"provider": "backblaze_b2", "status": "verified"}],
        },
        _download_to=download_to,
    )
    monkeypatch.setitem(sys.modules, "ai3704_storage_fabric", storage_stub)
    monkeypatch.setattr(studio, "_connect", connect)
    monkeypatch.setattr(studio, "_project_dir", lambda _project_id: project_root)

    restored = studio._rehydrate_project_asset(
        project_id, user_id, "timeline.json", force=True
    )

    assert downloads == [
        ("backblaze_b2", "ai-infinity/user/ast_verified_timeline/timeline.json")
    ]
    if corrupt_remote_bytes:
        # A corrupt object may not replace the existing local file.
        assert restored is None
        assert target.read_bytes() == corrupt_local
    else:
        assert restored == target
        assert target.read_bytes() == canonical_payload
        assert hashlib.sha256(target.read_bytes()).hexdigest() == expected_sha



def test_rehydration_never_rolls_back_to_an_older_durable_same_name_revision(
    monkeypatch, tmp_path: Path
) -> None:
    database = tmp_path / "creator-state.db"
    project_id = "project-no-silent-rollback"
    user_id = "rehydration-test-user"
    project_root = tmp_path / "projects" / project_id
    project_root.mkdir(parents=True)
    target = project_root / "timeline.json"
    corrupt_local = b'{"scenes":[{"heading":"latest edit is corrupt"}]}'
    target.write_bytes(corrupt_local)

    def connect():
        db = sqlite3.connect(database, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    with connect() as db:
        db.execute(
            """
            CREATE TABLE studio_assets_3610(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                media_type TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        # Older revision was durable, but the newest user edit explicitly
        # failed storage verification. Restoring the older object would conceal
        # that the latest edit was lost, so recovery must fail closed.
        db.execute(
            "INSERT INTO studio_assets_3610(project_id,kind,path,media_type,metadata_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                project_id,
                "timeline",
                str(target),
                "application/json",
                json.dumps({"durable_storage": {
                    "asset_id": "ast_older_timeline",
                    "status": "primary_only",
                    "successful_providers": ["backblaze_b2"],
                    "sha256": "a" * 64,
                    "size_bytes": 100,
                }}),
                1.0,
            ),
        )
        db.execute(
            "INSERT INTO studio_assets_3610(project_id,kind,path,media_type,metadata_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                project_id,
                "timeline",
                str(target),
                "application/json",
                json.dumps({"durable_storage": {
                    "status": "not_persisted",
                    "successful_providers": [],
                    "truthful": True,
                }}),
                2.0,
            ),
        )

    remote_downloads = []
    storage_stub = SimpleNamespace(
        _asset_row=lambda asset_id, uid: remote_downloads.append(asset_id) or {
            "asset_id": asset_id,
            "status": "primary_only",
            "object_key": "ai-infinity/user/older/timeline.json",
            "size_bytes": 100,
            "sha256": "a" * 64,
            "primary_provider": "backblaze_b2",
            "replicas": [{"provider": "backblaze_b2", "status": "verified"}],
        },
        _download_to=lambda provider, key, destination: destination.write_bytes(b"x" * 100),
    )
    monkeypatch.setitem(sys.modules, "ai3704_storage_fabric", storage_stub)
    monkeypatch.setattr(studio, "_connect", connect)
    monkeypatch.setattr(studio, "_project_dir", lambda _project_id: project_root)

    restored = studio._rehydrate_project_asset(
        project_id, user_id, "timeline.json", force=True
    )

    assert restored is None
    assert remote_downloads == []
    assert target.read_bytes() == corrupt_local
