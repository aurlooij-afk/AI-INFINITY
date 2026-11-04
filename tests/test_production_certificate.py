from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.create_production_certificate import build_certificate
from scripts.verify_live_deployment import assess_deployment_response


def _passing_acceptance(candidate):
    tests = [
        {"scenario_id": f"{index:02d}", "status": "PASS", "result": {}}
        for index in range(1, 14)
    ]
    tests[0]["result"] = {
        "truth_verified": True,
        "fallback_visual_count": 0,
        "visual_rights_unresolved_count": 0,
        "reviewer_packet": {"human_review_status": "PENDING"},
    }
    tests[11]["result"] = {
        "script_generation": {
            "status": "complete",
            "provider": "google-gemini-free-tier:gemini-3.1-flash-lite",
            "required_spoken_words": 1875,
            "actual_spoken_words": 2000,
            "unique_sentence_ratio": 0.95,
        }
    }
    tests[3]["result"] = {
        "otio_import_rerender_passed": True,
        "otio_rerender_full_decode_passed": True,
        "otio_rerender_sha256": "a" * 64,
    }
    return {
        "source_revision": candidate,
        "overall_status": "PASS",
        "passed": True,
        "completed_scenarios": 13,
        "tests": tests,
    }


def test_production_certificate_keeps_external_and_human_gates_separate():
    candidate = "b" * 40
    certificate = build_certificate(
        candidate_sha=candidate,
        acceptance=_passing_acceptance(candidate),
        storage={"status": "BLOCKED"},
        inference={"status": "BLOCKED"},
        deployment=None,
        human_approval=None,
        evidence_hashes={},
        gemini_writer={"status": "PASS", "model": "gemini-3.1-flash-lite", "candidate_commit_sha": candidate, "response_sha256": "a" * 64},
    )

    assert certificate["candidate_commit_sha"] == candidate
    assert certificate["ENGINEERING_STATE"] == "PASS"
    assert certificate["PRODUCTION_VERIFIED"] == "BLOCKED"
    assert certificate["CREATIVE_APPROVAL"] == "PENDING"
    assert certificate["gates"]["durable_storage"] == "BLOCKED"
    assert certificate["gates"]["live_gpu_inference"] == "BLOCKED"
    assert certificate["gates"]["live_deployment"] == "BLOCKED"
    assert certificate["gates"]["otio_import_and_rerender"] == "PASS"
    assert certificate["certificate_generated"] is False
    assert certificate["artifact_type"] == "production_readiness_report"
    assert "certificate_sha256" not in certificate
    assert certificate["readiness_report_sha256"]
    assert certificate["truthful"] is True


def test_production_certificate_passes_only_with_every_exact_candidate_gate():
    candidate = "c" * 40
    certificate = build_certificate(
        candidate_sha=candidate,
        acceptance=_passing_acceptance(candidate),
        storage={"status": "PASS", "candidate_commit_sha": candidate},
        inference={"status": "PASS", "candidate_commit_sha": candidate},
        deployment={"status": "PASS", "candidate_commit_sha": candidate, "deployed_commit_sha": candidate},
        human_approval={
            "candidate_commit_sha": candidate,
            "approved": True,
            "average_score": 4.7,
            "dimension_scores": {
                "director": 4.8,
                "cinematographer": 4.5,
                "editor": 4.7,
                "sound": 4.2,
                "story": 4.8,
                "rights": 4.7,
            },
            "rights_approval": True,
        },
        evidence_hashes={},
        gemini_writer={"status": "PASS", "model": "gemini-3.1-flash-lite", "candidate_commit_sha": candidate, "response_sha256": "a" * 64},
    )

    assert certificate["ENGINEERING_STATE"] == "PASS"
    assert certificate["PRODUCTION_VERIFIED"] == "PASS"
    assert certificate["CREATIVE_APPROVAL"] == "PASS"
    assert certificate["remaining_blockers"] == []
    assert certificate["certificate_generated"] is True
    assert certificate["artifact_type"] == "production_certificate"
    assert certificate["certificate_sha256"]


def test_production_certificate_rejects_approval_for_another_commit():
    candidate = "d" * 40
    certificate = build_certificate(
        candidate_sha=candidate,
        acceptance=_passing_acceptance(candidate),
        storage={"status": "PASS", "candidate_commit_sha": candidate},
        inference={"status": "PASS", "candidate_commit_sha": candidate},
        deployment={"status": "PASS", "candidate_commit_sha": candidate, "deployed_commit_sha": candidate},
        human_approval={
            "candidate_commit_sha": "e" * 40,
            "approved": True,
            "average_score": 5.0,
            "dimension_scores": {
                "director": 5,
                "cinematographer": 5,
                "editor": 5,
                "sound": 5,
                "story": 5,
                "rights": 5,
            },
            "rights_approval": True,
        },
        evidence_hashes={},
        gemini_writer={"status": "PASS", "model": "gemini-3.1-flash-lite", "candidate_commit_sha": candidate, "response_sha256": "a" * 64},
    )

    assert certificate["CREATIVE_APPROVAL"] == "PENDING"
    assert certificate["PRODUCTION_VERIFIED"] == "BLOCKED"
    assert certificate["certificate_generated"] is False
    assert "certificate_sha256" not in certificate
    assert "human_creative_and_rights_approval" in certificate["remaining_blockers"]


def test_certificate_cli_writes_readiness_report_not_certificate_when_gates_are_missing(tmp_path: Path):
    candidate = "f" * 40
    acceptance_path = tmp_path / "acceptance.json"
    acceptance_path.write_text(json.dumps(_passing_acceptance(candidate)), encoding="utf-8")
    output = tmp_path / "production-certificate.json"
    gemini_path = tmp_path / "gemini-writer-verification.json"
    gemini_path.write_text(json.dumps({"status": "PASS", "model": "gemini-3.1-flash-lite", "candidate_commit_sha": candidate, "response_sha256": "a" * 64}), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "create_production_certificate.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--candidate-sha",
            candidate,
            "--acceptance",
            str(acceptance_path),
            "--gemini-writer",
            str(gemini_path),
            "--storage",
            str(tmp_path / "missing-storage.json"),
            "--inference",
            str(tmp_path / "missing-inference.json"),
            "--deployment",
            str(tmp_path / "missing-deployment.json"),
            "--human-approval",
            str(tmp_path / "missing-human-approval.json"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report_path = tmp_path / "production-readiness-report.json"
    assert not output.exists(), "blocked gates must never create a production certificate"
    assert report_path.is_file()
    certificate = json.loads(report_path.read_text(encoding="utf-8"))
    assert certificate["candidate_commit_sha"] == candidate
    assert certificate["ENGINEERING_STATE"] == "PASS"
    assert certificate["PRODUCTION_VERIFIED"] == "BLOCKED"
    assert certificate["CREATIVE_APPROVAL"] == "PENDING"
    assert certificate["certificate_generated"] is False
    assert certificate["readiness_report_sha256"]


def test_live_deployment_verification_requires_exact_trusted_render_revision():
    candidate = "a" * 40
    payload = {
        "status": "healthy",
        "truthful": True,
        "content_creation_ready": True,
        "deployment": {
            "platform": "render",
            "source": "render_runtime_environment",
            "commit_sha": candidate,
            "branch": "main",
        },
    }

    result = assess_deployment_response(candidate, 200, payload)

    assert result["status"] == "PASS"
    assert result["deployed_commit_sha"] == candidate


def test_live_deployment_verification_blocks_a_stale_production_revision():
    candidate = "b" * 40
    payload = {
        "status": "healthy",
        "truthful": True,
        "content_creation_ready": True,
        "deployment": {
            "platform": "render",
            "source": "render_runtime_environment",
            "commit_sha": "c" * 40,
            "branch": "main",
        },
    }

    result = assess_deployment_response(candidate, 200, payload)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "exact_candidate_is_not_the_live_production_revision"
    assert result["deployed_commit_sha"] == "c" * 40


def test_live_deployment_verification_rejects_unhealthy_exact_candidate():
    candidate = "d" * 40
    payload = {
        "status": "degraded",
        "truthful": True,
        "content_creation_ready": False,
        "deployment": {
            "platform": "render",
            "source": "render_runtime_environment",
            "commit_sha": candidate,
            "branch": "main",
        },
    }

    result = assess_deployment_response(candidate, 200, payload)

    assert result["status"] == "FAIL"
    assert result["deployed_commit_sha"] == candidate



def test_certificate_rejects_unbound_or_stale_pass_evidence():
    candidate = "a" * 40
    certificate = build_certificate(
        candidate_sha=candidate,
        acceptance=_passing_acceptance(candidate),
        storage={"status": "PASS"},
        inference={"status": "PASS"},
        deployment={"status": "PASS", "deployed_commit_sha": candidate},
        human_approval=None,
        evidence_hashes={},
        gemini_writer={
            "status": "PASS",
            "model": "gemini-3.1-flash-lite",
            "response_sha256": "a" * 64,
        },
    )

    assert certificate["ENGINEERING_STATE"] == "FAIL"
    assert certificate["PRODUCTION_VERIFIED"] == "BLOCKED"
    assert certificate["production_requirements"]["durable_storage_round_trip_and_fresh_process_restore"] is False
    assert certificate["production_requirements"]["live_gpu_inference_and_artifact_verification"] is False
    assert certificate["production_requirements"]["exact_candidate_live_deployment"] is False
