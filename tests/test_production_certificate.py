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
        "full_decode_passed": True,
        "manifest_hash_verified": True,
        "final_media_sha256": "a" * 64,
        "final_media_bytes": 30_000_000,
        "fallback_visual_count": 0,
        "visual_rights_unresolved_count": 0,
        "source_count": 20,
        "rights_asset_count": 5,
        "package_sha256": "b" * 64,
        "package_size_bytes": 8_000_000,
        "package_round_trip_hash_verified": True,
        "quality_truth_checks": {
            "actual_rendered_scene_count": 5,
            "verified_motion_source_count": 5,
            "minimum_motion_source_count": 5,
            "visual_rights_evidence_present": True,
            "fallback_visual_count": 0,
            "visual_rights_cleared_count": 5,
            "visual_rights_review_required_count": 0,
            "visual_rights_unresolved_count": 0,
            "research_sources": 20,
        },
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


def _passing_storage():
    return {
        "status": "PASS",
        "backend": "backblaze_b2",
        "canary": "PUT_HEAD_GET_SHA_PASS",
        "snapshot": "PUT_HEAD_GET_SHA_PASS",
        "fresh_process_restore": "PASS",
        "object_key": "ai-infinity/project-state/certificate-test.verification-abc",
        "snapshot_size_bytes": 4096,
        "snapshot_sha256": "d" * 64,
        "restored_project_marker": "ai-infinity-storage-verification-abc",
        "restored_payload_sha256": "e" * 64,
        "production_snapshot_overwritten": False,
        "truthful": True,
    }


def _passing_inference():
    return {
        "status": "PASS",
        "provider": "wan2.2-ti2v-5b",
        "model_id": "Wan-AI/Wan2.2-TI2V-5B",
        "model_revision": "a" * 40,
        "source_revision": "1ea34ff48f87168174e12956e200b1d908b1c5ff",
        "license": "apache-2.0",
        "license_verified": True,
        "storage_persistent": True,
        "gpu": {"available": True, "total_vram_mb": 24576, "free_vram_mb": 23000},
        "job_id": "wan_" + "b" * 32,
        "inference_request_sent": True,
        "artifact_sha256": "c" * 64,
        "artifact_size_bytes": 1_000_000,
        "resolution": [1280, 720],
        "fps": 24.0,
        "video_codec": "h264",
        "full_decode": "PASS",
        "truthful": True,
    }


def _passing_deployment(candidate):
    return {
        "status": "PASS",
        "expected_commit_sha": candidate,
        "deployed_commit_sha": candidate,
        "project_status": "completed",
        "project_id": "studio_live_verified",
        "final_media_sha256": "f" * 64,
        "final_media_bytes": 8_000_000,
        "media_inspection": {
            "duration_seconds": 20.0,
            "full_decode_passed": True,
            "video": {"codec": "h264", "width": 1280, "height": 720},
            "audio": {"codec": "aac", "channels": 2},
        },
        "required_artifact_bytes": {
            name: 1024 for name in (
                "final.mp4", "thumbnail.jpg", "audio_master.mp3", "captions.srt",
                "article.md", "seo.json", "social_campaign.json",
                "production_manifest.json", "rights_manifest.json", "package.zip",
            )
        },
        "package_entries_verified": True,
        "rights_evidence": {
            "unresolved_count": 0,
            "review_required_count": 0,
            "rights_cleared_count": 5,
            "fallback_visual_count": 0,
            "manifest_sha256": "9" * 64,
            "manifest_parse_passed": True,
        },
        "truthful": True,
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
        storage=_passing_storage(),
        inference=_passing_inference(),
        deployment=_passing_deployment(candidate),
        human_approval={
            "candidate_commit_sha": candidate,
            "reviewed_media_sha256": "a" * 64,
            "reviewer": "project_owner",
            "reviewed_at_utc": "2026-11-04T00:00:00Z",
            "review_notes": "Reviewed the complete exact media cut and approved the creative result.",
            "approved": True,
            "average_score": 4.6167,
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
        storage=_passing_storage(),
        inference=_passing_inference(),
        deployment=_passing_deployment(candidate),
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


def test_certificate_cli_writes_gate_report_not_certificate_when_evidence_is_missing(tmp_path: Path):
    candidate = "f" * 40
    acceptance_path = tmp_path / "acceptance.json"
    acceptance_path.write_text(json.dumps(_passing_acceptance()), encoding="utf-8")
    output = tmp_path / "production-certificate.json"
    report = tmp_path / "production-gate-report.json"
    gemini_path = tmp_path / "gemini-writer-verification.json"
    gemini_path.write_text(json.dumps({
        "status": "PASS",
        "model": "gemini-3.1-flash-lite",
        "http_status": 200,
        "finish_reason": "STOP",
        "response_characters": 42,
        "response_sha256": "c" * 64,
    }), encoding="utf-8")
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
            "--report",
            str(report),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not output.exists(), "A blocked candidate must never receive a production certificate."
    gate_report = json.loads(report.read_text(encoding="utf-8"))
    assert gate_report["schema"] == "ai-infinity-production-gate-report.v1"
    assert gate_report["candidate_commit_sha"] == candidate
    assert gate_report["ENGINEERING_STATE"] == "PASS"
    assert gate_report["PRODUCTION_VERIFIED"] == "BLOCKED"
    assert gate_report["CREATIVE_APPROVAL"] == "PENDING"
    assert "certificate_sha256" not in gate_report


def test_certificate_cli_issues_certificate_only_when_every_gate_passes(tmp_path: Path):
    candidate = "c" * 40
    media_sha = "a" * 64
    acceptance_path = tmp_path / "acceptance.json"
    acceptance_path.write_text(json.dumps(_passing_acceptance()), encoding="utf-8")
    gemini_path = tmp_path / "gemini-writer-verification.json"
    gemini_path.write_text(json.dumps({
        "status": "PASS",
        "model": "gemini-3.1-flash-lite",
        "http_status": 200,
        "finish_reason": "STOP",
        "response_characters": 42,
        "response_sha256": "d" * 64,
    }), encoding="utf-8")
    storage_path = tmp_path / "storage.json"
    storage_path.write_text(json.dumps(_passing_storage()), encoding="utf-8")
    inference_path = tmp_path / "inference.json"
    inference_path.write_text(json.dumps(_passing_inference()), encoding="utf-8")
    deployment_path = tmp_path / "deployment.json"
    deployment_path.write_text(json.dumps(_passing_deployment(candidate)), encoding="utf-8")
    human_path = tmp_path / "human-approval.json"
    human_path.write_text(json.dumps({
        "candidate_commit_sha": candidate,
        "reviewed_media_sha256": media_sha,
        "reviewer": "project_owner",
        "reviewed_at_utc": "2026-11-04T00:00:00Z",
        "review_notes": "Reviewed the complete exact media cut and approved the creative result.",
        "approved": True,
        "average_score": 4.6167,
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
    }), encoding="utf-8")
    output = tmp_path / "production-certificate.json"
    report = tmp_path / "production-gate-report.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "create_production_certificate.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--candidate-sha", candidate,
            "--acceptance", str(acceptance_path),
            "--gemini-writer", str(gemini_path),
            "--storage", str(storage_path),
            "--inference", str(inference_path),
            "--deployment", str(deployment_path),
            "--human-approval", str(human_path),
            "--output", str(output),
            "--report", str(report),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    certificate = json.loads(output.read_text(encoding="utf-8"))
    assert certificate["candidate_commit_sha"] == candidate
    assert certificate["PRODUCTION_VERIFIED"] == "PASS"
    assert certificate["CREATIVE_APPROVAL"] == "PASS"
    assert certificate["remaining_blockers"] == []
    assert certificate["reviewed_media_sha256"] == media_sha
    assert certificate["certificate_sha256"]
    assert not report.exists()



def test_production_certificate_rejects_approval_for_different_media_bytes():
    candidate = "f" * 40
    certificate = build_certificate(
        candidate_sha=candidate,
        acceptance=_passing_acceptance(),
        storage=_passing_storage(),
        inference=_passing_inference(),
        deployment=_passing_deployment(candidate),
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
        storage=_passing_storage(),
        inference=_passing_inference(),
        deployment=_passing_deployment(candidate),
        human_approval=None,
        evidence_hashes={},
        gemini_writer={"status": "FAIL", "model": "gemini-3.1-flash-lite", "response_sha256": "c" * 64},
    )
    assert certificate["ENGINEERING_STATE"] == "FAIL"
    assert certificate["gates"]["gemini_writer_preflight"] == "BLOCKED_OR_FAIL"
    assert certificate["gates"]["900s_gemini_narration"] == "PASS"
