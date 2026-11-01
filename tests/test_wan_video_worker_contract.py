from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

import production_graph
import wan_video_worker as worker


def _model_fixture(tmp_path: Path, monkeypatch):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    files = []
    for name in worker.REQUIRED_MODEL_FILES:
        path = model_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == "README.md":
            data = (
                "---\nlicense: apache-2.0\n---\n\n"
                "## License Agreement\n\nThe models are licensed under the Apache 2.0 License.\n"
            ).encode()
        else:
            data = ("verified test model payload: " + name).encode()
        path.write_bytes(data)
        files.append({
            "path": name,
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    readme = (model_dir / "README.md").read_text(encoding="utf-8")
    manifest = {
        "model_id": worker.MODEL_ID,
        "revision": "a" * 40,
        "license": "apache-2.0",
        "model_card_sha256": hashlib.sha256(readme.encode("utf-8")).hexdigest(),
        "files": files,
    }
    (model_dir / worker.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(worker, "MODEL_DIR", model_dir)
    worker.MODEL_CACHE.update({"fingerprint": None, "result": None})
    monkeypatch.setenv("AI_INFINITY_WAN_MODEL_TERMS_ACKNOWLEDGED", "true")
    return model_dir, manifest


def test_wan_frame_count_obeys_4n_plus_1_and_duration_bounds():
    assert worker._frame_count(2.0) == 49
    assert worker._frame_count(5.0) == 121
    for duration in (2.0, 2.2, 3.0, 4.1, 5.0):
        frames = worker._frame_count(duration)
        assert frames >= 49
        assert frames <= 121
        assert (frames - 1) % 4 == 0
        assert frames / 24.0 >= duration


def test_model_manifest_verifies_revision_license_and_every_weight_hash(tmp_path, monkeypatch):
    model_dir, _manifest = _model_fixture(tmp_path, monkeypatch)

    verified = worker._model_state(force=True)

    assert verified["verified"] is True
    assert verified["license_verified"] is True
    assert verified["license"] == "apache-2.0"
    assert verified["revision"] == "a" * 40
    assert verified["verified_file_count"] == len(worker.REQUIRED_MODEL_FILES)

    (model_dir / "diffusion_pytorch_model-00001-of-00003.safetensors").write_bytes(b"tampered weights")
    tampered = worker._model_state(force=True)
    assert tampered["verified"] is False
    assert "SHA-256 mismatch" in tampered["reason"]


def test_model_terms_acknowledgement_invalidates_cached_verification(tmp_path, monkeypatch):
    _model_dir, _manifest = _model_fixture(tmp_path, monkeypatch)
    first = worker._model_state(force=True)
    assert first["verified"] is True

    monkeypatch.setenv("AI_INFINITY_WAN_MODEL_TERMS_ACKNOWLEDGED", "false")
    second = worker._model_state(force=False)
    assert second["verified"] is False
    assert "acknowledge" in second["reason"]


def test_worker_rejects_missing_or_wrong_bearer(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_INFINITY_WAN_WORKER_TOKEN", "correct-token")
    with pytest.raises(HTTPException) as exc:
        worker.capabilities(authorization="Bearer wrong-token")
    assert exc.value.status_code == 401


def test_worker_job_enqueue_is_idempotent_and_persisted(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(worker, "DATA_DIR", data)
    monkeypatch.setattr(worker, "DB_PATH", data / "jobs.sqlite3")
    monkeypatch.setattr(worker, "OUTPUT_DIR", data / "artifacts")
    monkeypatch.setattr(worker, "LOG_DIR", data / "logs")
    monkeypatch.setattr(worker, "_capabilities", lambda: {"execution_ready": True})
    monkeypatch.setenv("AI_INFINITY_WAN_WORKER_TOKEN", "test-token")
    worker._ensure()

    request = worker.GenerateRequest(
        prompt="A wide cinematic shot of a storm clearing over the mountains",
        duration_seconds=5,
        aspect_ratio="16:9",
        seed=42,
        idempotency_key="candidate-identity-0001",
    )
    first = worker.create_job(request, authorization="Bearer test-token")
    second = worker.create_job(request, authorization="Bearer test-token")

    assert first["job_id"] == second["job_id"]
    assert first["status"] == "queued"
    assert first["artifact"] is None

    changed = worker.GenerateRequest(
        prompt="A completely different shot with a different scene",
        duration_seconds=5,
        aspect_ratio="16:9",
        seed=42,
        idempotency_key="candidate-identity-0001",
    )
    with pytest.raises(HTTPException) as exc:
        worker.create_job(changed, authorization="Bearer test-token")
    assert exc.value.status_code == 409

    row = worker._get_job(first["job_id"])
    assert row is not None
    assert row["status"] == "queued"
    assert row["idempotency_key"] == "candidate-identity-0001"


def test_worker_capability_endpoint_fails_closed_without_process_lock(monkeypatch):
    monkeypatch.setattr(worker, "WORKER_PROCESS_LOCKED", False)
    monkeypatch.setattr(worker, "_source_state", lambda: {"verified": True, "revision": "b" * 40})
    monkeypatch.setattr(worker, "_model_state", lambda force=False: {
        "verified": True, "license_verified": True, "model_id": worker.MODEL_ID,
        "license": "apache-2.0", "terms_acknowledged": True, "revision": "a" * 40,
    })
    monkeypatch.setattr(worker, "_gpu_state", lambda force=False: {
        "available": True, "total_vram_mb": 24576, "free_vram_mb": 23000, "name": "test GPU",
    })
    monkeypatch.setattr(worker, "_storage_state", lambda: {"persistent": True, "free_bytes": 20_000_000_000})
    monkeypatch.setattr(worker.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setenv("AI_INFINITY_WAN_WORKER_TOKEN", "test-token")

    result = worker._capabilities(force=True)

    assert result["execution_ready"] is False
    assert any("single-GPU worker lock" in reason for reason in result["reasons"])


def test_remote_worker_configuration_requires_https_and_secret(monkeypatch):
    monkeypatch.delenv("AI_INFINITY_WAN_WORKER_URL", raising=False)
    monkeypatch.delenv("AI_INFINITY_WAN_WORKER_TOKEN", raising=False)
    assert production_graph.wan_worker_configuration()["configured"] is False

    monkeypatch.setenv("AI_INFINITY_WAN_WORKER_URL", "http://gpu.example.com")
    monkeypatch.setenv("AI_INFINITY_WAN_WORKER_TOKEN", "secret")
    assert production_graph.wan_worker_configuration()["configured"] is False

    monkeypatch.setenv("AI_INFINITY_WAN_WORKER_URL", "https://gpu.example.com")
    assert production_graph.wan_worker_configuration()["configured"] is True


def test_remote_worker_is_not_ready_without_explicit_model_license_proof(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {
                "provider": "wan2.2-ti2v-5b",
                "execution_ready": True,
                "model": {
                    "verified": True, "license_verified": False,
                    "model_id": "Wan-AI/Wan2.2-TI2V-5B",
                    "license": "apache-2.0", "terms_acknowledged": True,
                    "revision": "a" * 40,
                },
                "source": {"verified": True},
                "hardware": {"available": True, "total_vram_mb": 24576},
                "storage": {"persistent": True},
                "ffmpeg": True, "ffprobe": True,
                "reasons": [],
            }

    monkeypatch.setenv("AI_INFINITY_WAN_WORKER_URL", "https://gpu.example.com")
    monkeypatch.setenv("AI_INFINITY_WAN_WORKER_TOKEN", "secret")
    monkeypatch.setattr(production_graph.requests, "get", lambda *args, **kwargs: Response())
    production_graph.WAN_CAP_CACHE.update({"at": 0.0, "value": None})

    result = production_graph.wan_worker_capabilities(force=True)

    assert result["enabled"] is False
    assert result["status"] == "worker_not_ready"
