from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

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

    weights = model_dir / "diffusion_pytorch_model-00001-of-00003.safetensors"
    original = weights.read_bytes()
    weights.write_bytes(b"X" + original[1:])
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
                "hardware": {"available": True, "total_vram_mb": 24576, "free_vram_mb": 23000},
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



def test_worker_restart_requeues_running_job_without_published_artifact(tmp_path, monkeypatch):
    data = tmp_path / "data"
    output = data / "artifacts"
    monkeypatch.setattr(worker, "DATA_DIR", data)
    monkeypatch.setattr(worker, "DB_PATH", data / "jobs.sqlite3")
    monkeypatch.setattr(worker, "OUTPUT_DIR", output)
    monkeypatch.setattr(worker, "LOG_DIR", data / "logs")
    worker._ensure()
    request_data = {
        "prompt": "A cinematic establishing shot above the mountains",
        "duration_seconds": 5.0,
        "aspect_ratio": "16:9",
        "seed": 12,
    }
    with worker._connect() as c:
        c.execute(
            "INSERT INTO wan_jobs(job_id,idempotency_key,request_sha256,request_json,status,attempt,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("wan_" + "a" * 32, "candidate-recovery-0001", "b" * 64,
             json.dumps(request_data), "running", 1, 1.0, 1.0),
        )

    worker._recover_jobs()

    row = worker._get_job("wan_" + "a" * 32)
    assert row["status"] == "queued"
    assert "Recovered after worker restart" in row["error"]


def test_worker_restart_recovers_completed_artifact_with_verified_model_provenance(tmp_path, monkeypatch):
    data = tmp_path / "data"
    output = data / "artifacts"
    output.mkdir(parents=True)
    monkeypatch.setattr(worker, "DATA_DIR", data)
    monkeypatch.setattr(worker, "DB_PATH", data / "jobs.sqlite3")
    monkeypatch.setattr(worker, "OUTPUT_DIR", output)
    monkeypatch.setattr(worker, "LOG_DIR", data / "logs")
    worker._ensure()
    job_id = "wan_" + "c" * 32
    request_data = {
        "prompt": "A cinematic establishing shot above the mountains",
        "duration_seconds": 5.0,
        "aspect_ratio": "16:9",
        "seed": 12,
    }
    artifact = output / (job_id + ".mp4")
    artifact.write_bytes(b"verified recovered video bytes " * 5000)
    with worker._connect() as c:
        c.execute(
            "INSERT INTO wan_jobs(job_id,idempotency_key,request_sha256,request_json,status,attempt,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (job_id, "candidate-recovery-0002", "c" * 64,
             json.dumps(request_data), "running", 1, 1.0, 1.0),
        )
    monkeypatch.setattr(worker, "_probe_artifact", lambda *_args: {
        "duration_seconds": 5.04, "width": 1280, "height": 720, "fps": 24,
        "size_bytes": artifact.stat().st_size, "full_decode": True,
    })
    monkeypatch.setattr(worker, "_model_state", lambda force=False: {
        "verified": True, "license_verified": True, "revision": "a" * 40,
    })
    monkeypatch.setattr(worker, "_source_state", lambda: {
        "verified": True, "revision": worker.PINNED_SOURCE_REVISION,
    })

    worker._recover_jobs()

    row = worker._get_job(job_id)
    metadata = json.loads(row["metadata_json"])
    assert row["status"] == "succeeded"
    assert row["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert metadata["license_verified"] is True
    assert metadata["license"] == "apache-2.0"
    assert metadata["model_id"] == worker.MODEL_ID
    assert metadata["recovered_after_restart"] is True


def test_remote_worker_is_ready_only_with_pinned_model_gpu_and_persistence_proof(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {
                "provider": "wan2.2-ti2v-5b",
                "execution_ready": True,
                "model": {
                    "verified": True, "license_verified": True,
                    "model_id": "Wan-AI/Wan2.2-TI2V-5B",
                    "license": "apache-2.0", "terms_acknowledged": True,
                    "revision": "a" * 40,
                },
                "source": {
                    "verified": True,
                    "revision": production_graph.WAN_SOURCE_REVISION,
                },
                "hardware": {
                    "available": True, "total_vram_mb": 24576,
                    "free_vram_mb": 23000, "name": "test 24 GiB GPU",
                },
                "storage": {"persistent": True},
                "ffmpeg": True, "ffprobe": True,
                "reasons": [],
            }

    monkeypatch.setenv("AI_INFINITY_WAN_WORKER_URL", "https://gpu.example.com")
    monkeypatch.setenv("AI_INFINITY_WAN_WORKER_TOKEN", "secret")
    monkeypatch.setattr(production_graph.requests, "get", lambda *args, **kwargs: Response())
    production_graph.WAN_CAP_CACHE.update({"at": 0.0, "value": None})

    result = production_graph.wan_worker_capabilities(force=True)

    assert result["enabled"] is True
    assert result["status"] == "ready"
    assert result["license_verified"] is True
    assert result["persistent_storage"] is True
    assert result["compute_cost_state"] == "operator_hosted_compute_cost_not_included"



def test_creator_graph_reuses_persisted_remote_worker_job_after_app_restart(tmp_path, monkeypatch):
    data_dir = tmp_path / "ai-infinity"
    db_path = tmp_path / "production.sqlite3"
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(data_dir))
    monkeypatch.setattr(production_graph, "dbpath", lambda: db_path)
    production_graph.ensure()

    project_id = "studio-wan-recovery"
    shot_id = "shot-wan-recovery"
    candidate_id = "cand_" + "d" * 32
    local_job_id = "pgjob_" + "e" * 32
    remote_job_id = "wan_" + "f" * 32
    artifact_bytes = b"native Wan test bytes " * 10000
    artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()

    with production_graph.db() as c:
        c.execute(
            "INSERT INTO pg_projects(project_id,user_id,brief_sha,status,stage,progress,updated_at) VALUES(?,?,?,?,?,?,?)",
            (project_id, "creator-1", "brief", "producing", "visuals", 40, 1.0),
        )
        c.execute(
            "INSERT INTO pg_shots(shot_id,project_id,seq,purpose,duration,narration,visual_prompt,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (shot_id, project_id, 1, "Mountain reveal", 5.0, "A world opens", "A sweeping mountain reveal", "planned", 1.0),
        )
        c.execute(
            "INSERT INTO pg_candidates(candidate_id,shot_id,project_id,provider,model,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (candidate_id, shot_id, project_id, production_graph.WAN_PROVIDER_ID, "Wan2.2-TI2V-5B", "queued", 1.0, 1.0),
        )
        c.execute(
            "INSERT INTO pg_jobs(job_id,user_id,project_id,shot_id,kind,provider,status,payload_json,provider_job_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (local_job_id, "creator-1", project_id, shot_id, "video", production_graph.WAN_PROVIDER_ID,
             "running", json.dumps({"candidate_id": candidate_id, "prompt": "A sweeping mountain reveal", "duration": 5.0}),
             remote_job_id, 1.0, 1.0),
        )

    post_calls = []
    monkeypatch.setattr(
        production_graph, "wan_worker_capabilities",
        lambda force=False: {"enabled": True, "status": "ready"},
    )
    monkeypatch.setattr(
        production_graph, "wan_worker_configuration",
        lambda: {"configured": True, "base_url": "https://gpu.example.com", "token": "worker-secret"},
    )
    monkeypatch.setattr(
        production_graph.requests, "post",
        lambda *args, **kwargs: (post_calls.append((args, kwargs)) or (_ for _ in ()).throw(AssertionError("persisted remote job must be reused"))),
    )

    class Response:
        status_code = 200
        text = ""
        def json(self):
            return {
                "status": "succeeded",
                "job_id": remote_job_id,
                "artifact": {
                    "sha256": artifact_sha,
                    "size_bytes": len(artifact_bytes),
                    "url": "/v1/jobs/" + remote_job_id + "/artifact",
                },
                "metadata": {
                    "model_id": "Wan-AI/Wan2.2-TI2V-5B",
                    "model_revision": "a" * 40,
                    "license": "apache-2.0",
                    "license_verified": True,
                    "source_revision": production_graph.WAN_SOURCE_REVISION,
                },
            }

    monkeypatch.setattr(production_graph.requests, "get", lambda *args, **kwargs: Response())
    out_dir = data_dir / "production-jobs" / local_job_id
    out_dir.mkdir(parents=True)
    local_artifact = out_dir / (candidate_id + ".mp4")

    def fake_download(_url, _headers, output, expected_sha, expected_size):
        output.write_bytes(artifact_bytes)
        assert hashlib.sha256(output.read_bytes()).hexdigest() == expected_sha
        assert output.stat().st_size == expected_size
        return output

    monkeypatch.setattr(production_graph, "_download_verified_worker_artifact", fake_download)
    monkeypatch.setattr(production_graph, "tech", lambda _path: {
        "decision": "technical_pass",
        "score": 100,
        "checks": {"video": True, "hd_resolution": True, "full_decode": True, "duration_seconds": 5.0},
        "creative_approval": "NOT_EVALUATED",
    })
    saved = []
    monkeypatch.setattr(
        production_graph, "s",
        lambda: SimpleNamespace(
            _save_asset=lambda _pid, kind, path, media_type, metadata: (
                saved.append((kind, Path(path), media_type, metadata)) or {
                    "durable_storage": {
                        "status": "primary_only",
                        "sha256": artifact_sha,
                        "size_bytes": len(artifact_bytes),
                        "successful_providers": ["test-object-store"],
                        "asset_id": "asset-wan-verified",
                    }
                }
            )
        ),
    )

    result = production_graph._process_wan_video_job(
        {
            "job_id": local_job_id,
            "project_id": project_id,
            "provider": production_graph.WAN_PROVIDER_ID,
            "kind": "video",
            "payload_json": json.dumps({
                "candidate_id": candidate_id,
                "prompt": "A sweeping mountain reveal",
                "duration": 5.0,
                "aspect_ratio": "16:9",
            }),
        },
        {
            "candidate_id": candidate_id,
            "prompt": "A sweeping mountain reveal",
            "duration": 5.0,
            "aspect_ratio": "16:9",
        },
    )

    assert post_calls == []
    assert result["status"] == "succeeded"
    assert result["remote_job_id"] == remote_job_id
    assert result["storage_status"] == "primary_only"
    assert result["license_verified"] is True
    assert result["creative_approval"] == "PENDING"
    assert result["rights_status"] == "REVIEW_REQUIRED"
    assert saved and saved[0][0] == "candidate"
    with production_graph.db() as c:
        candidate = c.execute(
            "SELECT status,provider,model,storage_status,storage_json FROM pg_candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
    assert candidate["status"] == "ready"
    assert candidate["provider"] == production_graph.WAN_PROVIDER_ID
    assert candidate["storage_status"] == "primary_only"
    assert json.loads(candidate["storage_json"])["successful_providers"] == ["test-object-store"]
