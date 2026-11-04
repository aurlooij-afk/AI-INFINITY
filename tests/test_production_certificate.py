from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.create_production_certificate import build_certificate


def _passing_acceptance():
    tests = [
        {"scenario_id": f"{index:02d}", "status": "PASS", "result": {}}
        for index in range(1, 14)
    ]
    tests[0]["result"] = {
        "truth_verified": True,
        "fallback_visual_count": 0,
        "visual_rights_unresolved_count": 0,
        "final_media_sha256": "a" * 64,
        "reviewer_packet": {
            "human_review_status": "PENDING",
            "media_sha256": "a" * 64,
        },
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
        "overall_status": "PASS",
        "passed": True,
        "completed_scenarios": 13,
        "tests": tests,
    }


def test_production_certificate_keeps_external_and_human_gates_separate():
    candidate = "b" * 40
    certificate = build_certificate(
        candidate_sha=candidate,
        acceptance=_passing_acceptance(),
        storage={"status": "BLOCKED"},
        inference={"status": "BLOCKED"},
        deployment=None,
        human_approval=None,
        evidence_hashes={},
        gemini_writer={"status": "PASS", "model": "gemini-3.1-flash-lite", "response_sha256": "c" * 64},
    )

    assert certificate["candidate_commit_sha"] == candidate
    assert certificate["ENGINEERING_STATE"] == "PASS"
    assert certificate["PRODUCTION_VERIFIED"] == "BLOCKED"
    assert certificate["CREATIVE_APPROVAL"] == "PENDING"
    assert certificate["gates"]["durable_storage"] == "BLOCKED"
    assert certificate["gates"]["live_gpu_inference"] == "BLOCKED"
    assert certificate["gates"]["live_deployment"] == "BLOCKED"
    assert certificate["gates"]["otio_import_and_rerender"] == "PASS"
    assert certificate["certificate_sha256"]
    assert certificate["truthful"] is True


def test_production_certificate_passes_only_with_every_exact_candidate_gate():
    candidate = "c" * 40
    certificate = build_certificate(
        candidate_sha=candidate,
        acceptance=_passing_acceptance(),
        storage={"status": "PASS"},
        inference={"status": "PASS"},
        deployment={"status": "PASS", "deployed_commit_sha": candidate},
        human_approval={
            "candidate_commit_sha": candidate,
            "reviewed_media_sha256": "a" * 64,
            "reviewer": "project_owner",
            "reviewed_at_utc": "2026-11-04T00:00:00Z",
            "review_notes": "Reviewed the complete exact media cut and approved the creative result.",
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
            "rights_notes": "Reviewed source and licence records for the exact media SHA.",
        },
        evidence_hashes={},
        gemini_writer={"status": "PASS", "model": "gemini-3.1-flash-lite", "response_sha256": "c" * 64},
    )

    assert certificate["ENGINEERING_STATE"] == "PASS"
    assert certificate["PRODUCTION_VERIFIED"] == "PASS"
    assert certificate["CREATIVE_APPROVAL"] == "PASS"
    assert certificate["remaining_blockers"] == []


def test_production_certificate_rejects_approval_for_another_commit():
    candidate = "d" * 40
    certificate = build_certificate(
        candidate_sha=candidate,
        acceptance=_passing_acceptance(),
        storage={"status": "PASS"},
        inference={"status": "PASS"},
        deployment={"status": "PASS", "deployed_commit_sha": candidate},
        human_approval={
            "candidate_commit_sha": "e" * 40,
            "reviewed_media_sha256": "a" * 64,
            "reviewer": "project_owner",
            "reviewed_at_utc": "2026-11-04T00:00:00Z",
            "review_notes": "Reviewed an earlier candidate.",
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
            "rights_notes": "Reviewed the exact earlier media.",
        },
        evidence_hashes={},
        gemini_writer={"status": "PASS", "model": "gemini-3.1-flash-lite", "response_sha256": "c" * 64},
    )

    assert certificate["CREATIVE_APPROVAL"] == "PENDING"
    assert certificate["PRODUCTION_VERIFIED"] == "BLOCKED"
    assert "human_creative_and_rights_approval" in certificate["remaining_blockers"]


def test_certificate_cli_writes_revision_bound_record_when_external_evidence_is_missing(tmp_path: Path):
    candidate = "f" * 40
    acceptance_path = tmp_path / "acceptance.json"
    acceptance_path.write_text(json.dumps(_passing_acceptance()), encoding="utf-8")
    output = tmp_path / "production-certificate.json"
    gemini_path = tmp_path / "gemini-writer-verification.json"
    gemini_path.write_text(json.dumps({"status": "PASS", "model": "gemini-3.1-flash-lite", "response_sha256": "c" * 64}), encoding="utf-8")
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
    certificate = json.loads(output.read_text(encoding="utf-8"))
    assert certificate["candidate_commit_sha"] == candidate
    assert certificate["ENGINEERING_STATE"] == "PASS"
    assert certificate["PRODUCTION_VERIFIED"] == "BLOCKED"
    assert certificate["CREATIVE_APPROVAL"] == "PENDING"
    assert certificate["certificate_sha256"]



def test_production_certificate_rejects_approval_for_different_media_bytes():
    candidate = "f" * 40
    certificate = build_certificate(
        candidate_sha=candidate,
        acceptance=_passing_acceptance(),
        storage={"status": "PASS"},
        inference={"status": "PASS"},
        deployment={"status": "PASS", "deployed_commit_sha": candidate},
        human_approval={
            "candidate_commit_sha": candidate,
            "reviewed_media_sha256": "b" * 64,
            "reviewer": "project_owner",
            "reviewed_at_utc": "2026-11-04T00:00:00Z",
            "review_notes": "Reviewed and approved a different media file.",
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
            "rights_notes": "Reviewed the rights of the other media file.",
        },
        evidence_hashes={},
        gemini_writer={"status": "PASS", "model": "gemini-3.1-flash-lite", "response_sha256": "c" * 64},
    )

    assert certificate["CREATIVE_APPROVAL"] == "PENDING"
    assert certificate["PRODUCTION_VERIFIED"] == "BLOCKED"
    assert certificate["human_approval_media_sha256"] == "b" * 64
    assert certificate["reviewed_media_sha256"] == "a" * 64


def test_production_certificate_requires_gemini_smoke_pass():
    candidate = "a" * 40
    certificate = build_certificate(
        candidate_sha=candidate,
        acceptance=_passing_acceptance(),
        storage={"status": "PASS"},
        inference={"status": "PASS"},
        deployment={"status": "PASS", "deployed_commit_sha": candidate},
        human_approval=None,
        evidence_hashes={},
        gemini_writer={"status": "FAIL", "model": "gemini-3.1-flash-lite", "response_sha256": "c" * 64},
    )
    assert certificate["ENGINEERING_STATE"] == "FAIL"
    assert certificate["gates"]["gemini_writer_preflight"] == "BLOCKED_OR_FAIL"
    assert certificate["gates"]["900s_gemini_narration"] == "PASS"
