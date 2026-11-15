#!/usr/bin/env python3
"""Run a real GitHub-hosted smoke through AI Infinity's production execution core.

This proof is deliberately read-only. It exercises the real application router,
SQLite execution ledger, idempotency contract, and dependency-aware batch
coordinator. It also verifies the exact checked-out commit against GitHub's API.
It does not claim model inference, durable cross-runner storage, GPU inference,
or production deployment unless separate evidence establishes those properties.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "github-native-evidence"
TRUTHY = {"1", "true", "yes", "on"}


def _json_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _github_get(path: str) -> dict:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is unavailable; exact-revision GitHub read-back cannot be verified.")
    request = Request(
        "https://api.github.com" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AI-Infinity-GitHub-Native-Execution-Proof",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("GitHub API returned a non-object response.")
            return payload
    except HTTPError as exc:
        raise RuntimeError(f"GitHub API read-back failed with HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"GitHub API read-back failed: {type(exc).__name__}.") from exc


def verify_github_revision() -> dict:
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    revision = os.getenv("GITHUB_SHA", "").strip().lower()
    branch = os.getenv("GITHUB_REF_NAME", "").strip()
    if not repository or not revision or not branch:
        raise RuntimeError("Required GitHub repository, revision, or branch context is missing.")
    if "/" not in repository or len(revision) != 40:
        raise RuntimeError("GitHub repository or commit context is malformed.")

    repo_data = _github_get(f"/repos/{repository}")
    ref_data = _github_get(f"/repos/{repository}/git/ref/heads/{quote(branch, safe='/')}")
    commit_data = _github_get(f"/repos/{repository}/commits/{revision}")

    ref_sha = str((ref_data.get("object") or {}).get("sha") or "").lower()
    commit_sha = str(commit_data.get("sha") or "").lower()
    local_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip().lower()
    if ref_sha != revision:
        raise RuntimeError("GitHub branch ref no longer points at this workflow's exact commit.")
    if commit_sha != revision or local_sha != revision:
        raise RuntimeError("GitHub commit read-back, workflow SHA, and checked-out SHA do not match.")

    checked_paths = (
        "ai_infinity/execution_core.py",
        "universal_command_batch.py",
        "ai_infinity_canonical.py",
        "main.py",
    )
    source_readback = {}
    for path in checked_paths:
        encoded_path = quote(path, safe="/")
        data = _github_get(
            f"/repos/{repository}/contents/{encoded_path}?ref={revision}"
        )
        if not data.get("sha") or int(data.get("size") or 0) <= 0:
            raise RuntimeError(f"GitHub source read-back is incomplete for {path}.")
        local_path = ROOT / path
        if not local_path.is_file():
            raise RuntimeError(f"Checked-out source file is missing: {path}.")
        source_readback[path] = {
            "github_blob_sha": str(data["sha"]),
            "bytes": int(data["size"]),
            "local_sha256": hashlib.sha256(local_path.read_bytes()).hexdigest(),
        }

    return {
        "status": "PASS",
        "repository": repository,
        "branch": branch,
        "workflow_commit_sha": revision,
        "github_ref_commit_sha": ref_sha,
        "github_commit_sha": commit_sha,
        "local_checkout_sha": local_sha,
        "commit_tree_sha": str((commit_data.get("commit") or {}).get("tree", {}).get("sha") or ""),
        "default_branch": str(repo_data.get("default_branch") or ""),
        "source_readback": source_readback,
        "api_read_operations": 3 + len(checked_paths),
        "credential_scope": "workflow GITHUB_TOKEN with contents:read",
        "truthful": True,
    }


def execute_real_application_path(revision: str) -> dict:
    # These settings must be in place before importing the application modules.
    os.environ["AI_INFINITY_FAST_MODE"] = "1"
    os.environ["AI_INFINITY_3601_BACKGROUND"] = "false"

    with tempfile.TemporaryDirectory(prefix="ai-infinity-gh-proof-") as data_dir:
        os.environ["AI_INFINITY_DATA_DIR"] = data_dir

        import main
        import ai_infinity.execution_core as execution_core
        from fastapi.testclient import TestClient

        # Register the execution ledger introspection endpoints if the main
        # production entrypoint has not already registered them.
        execution_core.register(main.app)
        client = TestClient(main.app)

        health = client.get("/health")
        if health.status_code != 200 or health.json().get("truthful") is not True:
            raise RuntimeError("Production application health route did not return truthful HTTP 200.")
        execution_health = client.get("/infinity/execution/health")
        if execution_health.status_code != 200:
            raise RuntimeError("Execution-core health route was not registered.")
        health_body = execution_health.json()
        if int(health_body.get("executable_count") or 0) < 1:
            raise RuntimeError("No executable capability is bound to the canonical dispatcher.")

        command = "Show AI Infinity system capabilities"
        payload = {
            "task_type": "capabilities",
            "idempotency_key": "gh-native-execution-" + revision[:12],
        }
        first_response = client.post(
            "/infinity/canonical/execute",
            json={"command": command, **payload},
        )
        if first_response.status_code != 200:
            raise RuntimeError(
                f"Canonical execution endpoint returned HTTP {first_response.status_code}."
            )
        first = first_response.json()
        execution = first.get("execution") if isinstance(first.get("execution"), dict) else {}
        if (
            first.get("status") != "completed"
            or first.get("route") != "capabilities"
            or first.get("executed") is not True
            or execution.get("state") != "completed"
            or not execution.get("execution_id")
        ):
            raise RuntimeError("The real canonical execution did not complete with a persisted execution identity.")

        replay_response = client.post(
            "/infinity/canonical/execute",
            json={"command": command, **payload},
        )
        if replay_response.status_code != 200:
            raise RuntimeError("Idempotent execution replay did not return HTTP 200.")
        replay = replay_response.json()
        replay_execution = replay.get("execution") if isinstance(replay.get("execution"), dict) else {}
        if (
            replay.get("status") != "existing"
            or replay.get("executed") is not False
            or replay.get("idempotent_replay") is not True
            or replay_execution.get("execution_id") != execution.get("execution_id")
        ):
            raise RuntimeError("Execution idempotency did not prevent duplicate dispatch.")

        ledger_response = client.get(
            "/infinity/execution/" + str(execution["execution_id"])
        )
        if ledger_response.status_code != 200:
            raise RuntimeError("Execution ledger read-back failed.")
        ledger = ledger_response.json()
        if (
            ledger.get("state") != "completed"
            or ledger.get("execution_id") != execution.get("execution_id")
            or ledger.get("truthful") is not True
        ):
            raise RuntimeError("Persisted execution ledger read-back did not match the command result.")

        batch_body = {
            "idempotency_key": "gh-native-batch-" + revision[:12],
            "max_concurrency": 2,
            "commands": [
                {
                    "id": "capabilities-first",
                    "command": "Show system capabilities",
                    "payload": {"task_type": "capabilities"},
                },
                {
                    "id": "capabilities-dependent",
                    "command": "Inspect runtime capabilities",
                    "payload": {"task_type": "capabilities"},
                    "depends_on": ["capabilities-first"],
                },
            ],
        }
        batch_response = client.post("/infinity/canonical/batches", json=batch_body)
        if batch_response.status_code != 200:
            raise RuntimeError(
                f"Canonical command-batch endpoint returned HTTP {batch_response.status_code}."
            )
        batch = batch_response.json()
        steps = batch.get("steps") if isinstance(batch.get("steps"), list) else []
        if (
            batch.get("status") != "completed"
            or int(batch.get("completed_steps") or 0) != 2
            or len(steps) != 2
            or any(step.get("state") != "completed" for step in steps)
        ):
            raise RuntimeError("Dependency-aware command batch did not complete both real dispatches.")

        batch_replay_response = client.post(
            "/infinity/canonical/batches", json=batch_body
        )
        if batch_replay_response.status_code != 200:
            raise RuntimeError("Command-batch idempotent replay did not return HTTP 200.")
        batch_replay = batch_replay_response.json()
        if (
            batch_replay.get("replayed") is not True
            or batch_replay.get("batch_id") != batch.get("batch_id")
            or batch_replay.get("status") != "completed"
        ):
            raise RuntimeError("Command-batch replay did not return the previously persisted batch.")

        return {
            "status": "PASS",
            "application_entrypoint": "main:app",
            "health_route": "PASS",
            "execution_health": {
                "status": health_body.get("status"),
                "registry_count": int(health_body.get("registry_count") or 0),
                "executable_count": int(health_body.get("executable_count") or 0),
                "guard_count": int(health_body.get("guard_count") or 0),
                "ledger": health_body.get("ledger"),
                "retry_policy": health_body.get("retry_policy"),
            },
            "single_command": {
                "status": first.get("status"),
                "route": first.get("route"),
                "executed": first.get("executed"),
                "execution_id": execution.get("execution_id"),
                "capability_id": execution.get("capability_id"),
                "lifecycle_state": execution.get("state"),
                "request_sha256": execution.get("request_sha256"),
                "ledger_readback": "PASS",
                "idempotent_replay": "PASS",
            },
            "multi_command_batch": {
                "status": batch.get("status"),
                "batch_id": batch.get("batch_id"),
                "step_count": len(steps),
                "completed_steps": int(batch.get("completed_steps") or 0),
                "dependency_order": [step.get("id") for step in steps],
                "states": [step.get("state") for step in steps],
                "idempotent_replay": "PASS",
            },
            "state_boundary": (
                "The SQLite execution ledger was real within this job's temporary runner "
                "filesystem. This proof does not claim persistence after the runner is destroyed."
            ),
            "truthful": True,
        }


def run_proof() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    github = verify_github_revision()
    runtime = execute_real_application_path(github["workflow_commit_sha"])
    report = {
        "schema": "ai-infinity.github-native-execution-proof.v1",
        "status": "PASS",
        "evidence_level": "TESTED_ON_EXACT_GITHUB_ACTIONS_COMMIT",
        "generated_at_unix": int(time.time()),
        "repository": github["repository"],
        "branch": github["branch"],
        "commit_sha": github["workflow_commit_sha"],
        "workflow_run_id": os.getenv("GITHUB_RUN_ID", ""),
        "workflow_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
        "github_api": github,
        "real_application_execution": runtime,
        "scope_limits": {
            "external_model_inference": "NOT_ESTABLISHED_BY_THIS_EXECUTOR_SMOKE; separate consent-gated Gemini check is recorded independently",
            "gpu_inference": "NOT_TESTED; GitHub-hosted CPU runner proof only",
            "durable_cross_run_state": "NOT_ESTABLISHED; runner-local SQLite is temporary",
            "long_form_media_acceptance": "NOT_TESTED_BY_THIS_PROOF",
            "production_deployment": "NOT_TESTED_BY_THIS_PROOF",
            "human_creative_and_rights_approval": "NOT_TESTED_BY_THIS_PROOF",
        },
        "truthful": True,
    }
    _json_write(EVIDENCE_DIR / "execution-proof.json", report)
    print("GITHUB_NATIVE_EXECUTION_PROOF=PASS")
    print("EXECUTION_CORE=PASS")
    print("SINGLE_COMMAND_IDEMPOTENCY=PASS")
    print("LEDGER_READBACK=PASS")
    print("DEPENDENCY_AWARE_BATCH=PASS")
    print("BATCH_IDEMPOTENCY=PASS")
    print("EXACT_SHA_GITHUB_API_READBACK=PASS")


def finalize() -> None:
    report_path = EVIDENCE_DIR / "execution-proof.json"
    if not report_path.is_file():
        raise RuntimeError("Execution proof report is missing; refusing to package evidence.")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    model_path = EVIDENCE_DIR / "model-inference-verification.json"
    if model_path.is_file():
        model = json.loads(model_path.read_text(encoding="utf-8"))
    else:
        model = {"status": "NOT_RUN", "reason": "model_gate_artifact_not_produced", "truthful": True}
    report["scope_limits"]["external_model_inference"] = str(model.get("status") or "UNKNOWN")
    report["model_inference_gate"] = model
    _json_write(report_path, report)

    files = {}
    for path in sorted(EVIDENCE_DIR.iterdir()):
        if path.is_file() and path.name != "artifact-manifest.json":
            files[path.name] = {
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    manifest = {
        "schema": "ai-infinity.github-native-evidence-manifest.v1",
        "repository": os.getenv("GITHUB_REPOSITORY", ""),
        "commit_sha": os.getenv("GITHUB_SHA", "").lower(),
        "workflow_run_id": os.getenv("GITHUB_RUN_ID", ""),
        "files": files,
        "truthful": True,
    }
    _json_write(EVIDENCE_DIR / "artifact-manifest.json", manifest)
    print("EVIDENCE_MANIFEST=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    try:
        finalize() if args.finalize else run_proof()
        return 0
    except Exception as exc:
        print(f"GITHUB_NATIVE_EXECUTION_PROOF=FAIL reason={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
