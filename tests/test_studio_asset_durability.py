from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import studio_ultimate as studio


@pytest.mark.parametrize(
    "kind",
    [
        "final", "video", "package", "thumbnail", "audio", "audio_master",
        "caption", "captions", "script", "transcript", "article", "seo",
        "social", "social_campaign", "social_campaign_markdown", "sources",
        "provenance", "manifest", "production_manifest", "rights_manifest",
        "rights", "fact_check", "accessibility", "platform_manifest",
        "feature_execution", "short", "version", "edit", "editorial_scorecard",
    ],
)
def test_user_facing_deliverable_kinds_enter_real_durable_storage(monkeypatch, tmp_path: Path, kind: str):
    """Guard the mapping between actual emitted asset kinds and B2/object persistence."""
    database = tmp_path / "creator-state.db"

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

    calls = []

    def persist(path, user_id, filename, media_type, metadata=None):
        calls.append({
            "path": Path(path),
            "user_id": user_id,
            "filename": filename,
            "media_type": media_type,
            "metadata": dict(metadata or {}),
        })
        return {
            "status": "primary_only",
            "asset_id": "ast_test_master_asset",
            "object_key": "ai-infinity/test/master",
            "primary_provider": "backblaze_b2",
            "sha256": "a" * 64,
            "size_bytes": Path(path).stat().st_size,
            "configured_providers": ["backblaze_b2"],
            "successful_providers": ["backblaze_b2"],
            "truthful": True,
        }

    storage_stub = SimpleNamespace(_persist_path=persist)
    monkeypatch.setitem(sys.modules, "ai3704_storage_fabric", storage_stub)
    monkeypatch.setattr(studio, "_connect", connect)
    monkeypatch.setattr(studio, "_get_project", lambda project_id: {
        "project_id": project_id,
        "user_id": "durability-test-user",
    })

    artifact = tmp_path / ("final.mp4" if kind in {"video", "final", "edit", "version"} else f"deliverable-{kind}.bin")
    artifact.write_bytes((f"real-deliverable-kind:{kind}\n".encode("utf-8")) * 256)

    studio._save_asset(
        "durability-test-project",
        kind,
        artifact,
        "video/mp4" if kind in {"video", "final", "edit", "version", "short"} else "application/octet-stream",
        {"title": "durability regression"},
    )

    assert len(calls) == 1, f"asset kind {kind!r} was registered locally but never sent to durable object storage"
    assert calls[0]["metadata"]["project_id"] == "durability-test-project"
    assert calls[0]["metadata"]["kind"] == kind

    with connect() as db:
        row = db.execute(
            "SELECT kind,metadata_json FROM studio_assets_3610 WHERE project_id=? ORDER BY id DESC LIMIT 1",
            ("durability-test-project",),
        ).fetchone()
    assert row is not None
    assert row["kind"] == kind
    metadata = json.loads(row["metadata_json"])
    assert metadata["durable_storage"]["asset_id"] == "ast_test_master_asset"
    assert metadata["durable_storage"]["status"] == "primary_only"
    assert metadata["durable_storage"]["truthful"] is True
