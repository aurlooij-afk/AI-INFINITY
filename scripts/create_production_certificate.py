from __future__ import annotations

"""Create a candidate-SHA-bound production certificate from real evidence files.

This script does not infer live deployment, durable storage, GPU inference or
human approval from code/tests. Missing external evidence remains BLOCKED/PENDING.
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_ACCEPTANCE_SCENARIO_IDS = {f"{index:02d}" for index in range(1, 20)}


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scenario_map(acceptance: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(acceptance, dict):
        return {}
    rows = acceptance.get("tests")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("scenario_id") or "").zfill(2): row
        for row in rows
        if isinstance(row, dict) and row.get("scenario_id") is not None
    }


def _valid_hex_digest(value: Any, length: int = 64) -> bool:
    text = str(value or "")
    return len(text) == length and all(character in "0123456789abcdef" for character in text.lower())


def _int_value(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _int_equals(value: Any, expected: int) -> bool:
    try:
        return int(value) == expected
    except (TypeError, ValueError, OverflowError):
        return False


def _float_within(value: Any, expected: float, tolerance: float) -> bool:
    try:
        return abs(float(value) - expected) <= tolerance
    except (TypeError, ValueError, OverflowError):
        return False


def _valid_human_approval(
    value: dict[str, Any] | None,
    candidate_sha: str,
    reviewed_media_sha256: str | None,
) -> bool:
    """Accept only a real review bound to both the candidate and exact reviewed bytes."""
    if not isinstance(value, dict) or not reviewed_media_sha256:
        return False
    if value.get("candidate_commit_sha") != candidate_sha or value.get("approved") is not True:
        return False
    if value.get("reviewed_media_sha256") != reviewed_media_sha256:
        return False
    if not str(value.get("reviewer") or "").strip():
        return False
    reviewed_at = str(value.get("reviewed_at_utc") or "").strip()
    try:
        parsed_reviewed_at = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed_reviewed_at.tzinfo is None:
        return False
    if len(str(value.get("review_notes") or "").strip()) < 20:
        return False
    if value.get("rights_approval") is not True or len(str(value.get("rights_notes") or "").strip()) < 20:
        return False
    try:
        average = float(value.get("average_score"))
    except (TypeError, ValueError):
        return False
    dimensions = value.get("dimension_scores")
    required_roles = {"director", "cinematographer", "editor", "sound", "story", "rights"}
    if not isinstance(dimensions, dict) or set(str(k).strip().casefold() for k in dimensions) != required_roles:
        return False
    try:
        scores = {str(role).strip().casefold(): float(score) for role, score in dimensions.items()}
    except (TypeError, ValueError):
        return False
    if any(score < 4.0 or score > 5.0 for score in scores.values()):
        return False
    measured_average = sum(scores.values()) / len(required_roles)
    return average >= 4.5 and abs(average - measured_average) <= 0.01


def build_certificate(
    candidate_sha: str,
    acceptance: dict[str, Any] | None,
    storage: dict[str, Any] | None,
    inference: dict[str, Any] | None,
    deployment: dict[str, Any] | None,
    human_approval: dict[str, Any] | None,
    evidence_hashes: dict[str, str | None],
    gemini_writer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenarios = _scenario_map(acceptance)
    acceptance_tests = acceptance.get("tests") if isinstance(acceptance, dict) else None
    acceptance_tests = acceptance_tests if isinstance(acceptance_tests, list) else []
    scenario_statuses = [str(row.get("status") or "") for row in scenarios.values()]
    longform_result = scenarios.get("12", {}).get("result", {})
    longform_script = longform_result.get("script_generation") if isinstance(longform_result.get("script_generation"), dict) else {}
    longform_gemini_pass = bool(
        longform_script.get("status") == "complete"
        and str(longform_script.get("provider") or "").startswith("google-gemini-free-tier:gemini-3.1-flash-lite")
        and int(longform_script.get("required_spoken_words") or 0) >= 1875
        and int(longform_script.get("actual_spoken_words") or 0) >= 1875
        and int(longform_script.get("actual_spoken_words") or 0) >= int(longform_script.get("required_spoken_words") or 1)
        and float(longform_script.get("unique_sentence_ratio") or 0) >= 0.85
    )
    gemini_sha = str((gemini_writer or {}).get("response_sha256") or "")
    gemini_writer_pass = bool(
        isinstance(gemini_writer, dict)
        and gemini_writer.get("status") == "PASS"
        and gemini_writer.get("model") == "gemini-3.1-flash-lite"
        and int(gemini_writer.get("http_status") or 0) == 200
        and gemini_writer.get("finish_reason") == "STOP"
        and int(gemini_writer.get("response_characters") or 0) > 0
        and _valid_hex_digest(gemini_sha)
    )
    engineering_pass = bool(
        acceptance
        and acceptance.get("overall_status") == "PASS"
        and acceptance.get("passed") is True
        and int(acceptance.get("scenario_count") or 0) == len(EXPECTED_ACCEPTANCE_SCENARIO_IDS)
        and int(acceptance.get("completed_scenarios") or 0) == len(EXPECTED_ACCEPTANCE_SCENARIO_IDS)
        and len(acceptance_tests) == len(EXPECTED_ACCEPTANCE_SCENARIO_IDS)
        and len(scenarios) == len(EXPECTED_ACCEPTANCE_SCENARIO_IDS)
        and set(scenarios) == EXPECTED_ACCEPTANCE_SCENARIO_IDS
        and len(scenario_statuses) == len(EXPECTED_ACCEPTANCE_SCENARIO_IDS)
        and all(status == "PASS" for status in scenario_statuses)
        and gemini_writer_pass
        and longform_gemini_pass
    )

    flagship = scenarios.get("01", {}).get("result", {})
    otio = scenarios.get("04", {}).get("result", {})
    reviewer_packet = flagship.get("reviewer_packet") if isinstance(flagship.get("reviewer_packet"), dict) else {}
    flagship_media_sha256 = str(
        flagship.get("final_media_sha256")
        or reviewer_packet.get("media_sha256")
        or ""
    )
    creative_approval = (
        "PASS"
        if _valid_human_approval(human_approval, candidate_sha, flagship_media_sha256)
        else "PENDING"
    )

    storage_status = str((storage or {}).get("status") or "BLOCKED").upper()
    inference_status = str((inference or {}).get("status") or "BLOCKED").upper()
    deployment_status = str((deployment or {}).get("status") or "BLOCKED").upper()
    deployment_sha = str((deployment or {}).get("deployed_commit_sha") or "")

    quality = flagship.get("quality_truth_checks") if isinstance(flagship.get("quality_truth_checks"), dict) else {}
    def _int_at_least(value: Any, minimum: int = 0) -> bool:
        try:
            return int(value) >= minimum
        except (TypeError, ValueError, OverflowError):
            return False

    actual_scene_count = quality.get("actual_rendered_scene_count")
    verified_motion_count = quality.get("verified_motion_source_count")
    cleared_rights_count = quality.get("visual_rights_cleared_count")
    technical_rights_pass = bool(
        flagship
        and flagship.get("truth_verified") is True
        and flagship.get("full_decode_passed") is True
        and flagship.get("manifest_hash_verified") is True
        and _int_at_least(flagship.get("final_media_bytes"), 100_000)
        and _valid_hex_digest(flagship.get("final_media_sha256"))
        and _int_equals(flagship.get("fallback_visual_count"), 0)
        and _int_equals(flagship.get("visual_rights_unresolved_count"), 0)
        and _int_at_least(actual_scene_count, 1)
        and _int_at_least(verified_motion_count, 1)
        and _int_at_least(verified_motion_count, _int_value(quality.get("minimum_motion_source_count"), 0))
        and _int_at_least(cleared_rights_count, _int_value(verified_motion_count, 0))
        and quality.get("visual_rights_evidence_present") is True
        and _int_equals(quality.get("visual_rights_review_required_count"), 0)
        and _int_equals(quality.get("visual_rights_unresolved_count"), 0)
        and _int_equals(quality.get("fallback_visual_count"), 0)
        and _int_at_least(flagship.get("source_count"), 1)
        and _int_at_least(flagship.get("rights_asset_count"), int(verified_motion_count or 0))
        and _valid_hex_digest(flagship.get("package_sha256"))
        and _int_at_least(flagship.get("package_size_bytes"), 100_000)
        and flagship.get("package_round_trip_hash_verified") is True
    )
    otio_sha = str((otio or {}).get("otio_rerender_sha256") or "")
    otio_rerender_pass = bool(
        otio
        and otio.get("otio_import_rerender_passed") is True
        and otio.get("otio_rerender_full_decode_passed") is True
        and len(otio_sha) == 64
        and all(character in "0123456789abcdef" for character in otio_sha.lower())
    )
    deployment_media = (deployment or {}).get("media_inspection") if isinstance((deployment or {}).get("media_inspection"), dict) else {}
    deployment_video = deployment_media.get("video") if isinstance(deployment_media.get("video"), dict) else {}
    deployment_audio = deployment_media.get("audio") if isinstance(deployment_media.get("audio"), dict) else {}
    deployment_rights = (deployment or {}).get("rights_evidence") if isinstance((deployment or {}).get("rights_evidence"), dict) else {}
    deployment_sha256 = str((deployment or {}).get("final_media_sha256") or "")
    deployment_manifest_sha = str(deployment_rights.get("manifest_sha256") or "")
    deployment_artifacts = (deployment or {}).get("required_artifact_bytes") if isinstance((deployment or {}).get("required_artifact_bytes"), dict) else {}
    required_live_artifacts = {
        "final.mp4", "thumbnail.jpg", "audio_master.mp3", "captions.srt",
        "article.md", "seo.json", "social_campaign.json",
        "production_manifest.json", "rights_manifest.json", "package.zip",
    }
    live_deployment_pass = bool(
        deployment_status == "PASS"
        and deployment_sha == candidate_sha
        and str((deployment or {}).get("expected_commit_sha") or "") == candidate_sha
        and str((deployment or {}).get("project_status") or "").lower() == "completed"
        and str((deployment or {}).get("project_id") or "").strip()
        and _valid_hex_digest(deployment_sha256)
        and _int_at_least((deployment or {}).get("final_media_bytes"), 100_000)
        and deployment_media.get("full_decode_passed") is True
        and _float_within(deployment_media.get("duration_seconds"), 20.0, 1.0)
        and deployment_video.get("codec") == "h264"
        and _int_at_least(deployment_video.get("width"), 1280)
        and _int_at_least(deployment_video.get("height"), 720)
        and deployment_audio.get("codec") in {"aac", "mp3"}
        and (deployment or {}).get("package_entries_verified") is True
        and required_live_artifacts.issubset(set(deployment_artifacts))
        and all(_int_at_least(deployment_artifacts.get(name), 1) for name in required_live_artifacts)
        and _valid_hex_digest(deployment_manifest_sha)
        and _int_equals(deployment_rights.get("unresolved_count"), 0)
        and _int_equals(deployment_rights.get("review_required_count"), 0)
        and _int_at_least(deployment_rights.get("actual_rendered_scene_count"), 1)
        and deployment_rights.get("visual_rights_evidence_present") is True
        and _int_at_least(deployment_rights.get("verified_motion_source_count"), 1)
        and _int_at_least(deployment_rights.get("rights_asset_count"), _int_value(deployment_rights.get("verified_motion_source_count"), 0))
        and _int_at_least(deployment_rights.get("rights_cleared_count"), int(deployment_rights.get("verified_motion_source_count") or 0))
        and _int_equals(deployment_rights.get("fallback_visual_count"), 0)
        and deployment_rights.get("manifest_parse_passed") is True
        and (deployment or {}).get("truthful") is True
    )

    storage_snapshot_sha = str((storage or {}).get("snapshot_sha256") or "")
    storage_payload_sha = str((storage or {}).get("restored_payload_sha256") or "")
    live_storage = (deployment or {}).get("storage_health") if isinstance((deployment or {}).get("storage_health"), dict) else {}
    live_storage_backend_status = (
        live_storage.get("project_state_backend_status")
        if isinstance(live_storage.get("project_state_backend_status"), dict) else {}
    )
    live_storage_backend = str(live_storage.get("project_state_backend") or "").strip().lower()
    live_storage_sha = str(live_storage_backend_status.get("last_sha256") or "").lower()
    live_storage_sha_valid = _valid_hex_digest(live_storage_sha)
    live_storage_pass = bool(
        live_storage.get("persistent") is True
        and live_storage.get("mode") == "durable"
        and live_storage.get("project_state_backend_confirmed") is True
        and live_storage_backend in {"backblaze_b2", "supabase_s3"}
        and live_storage_backend == str((storage or {}).get("backend") or "").strip().lower()
        and live_storage.get("truthful") is True
        and live_storage_backend_status.get("backend") == live_storage_backend
        and live_storage_backend_status.get("contract_enabled") is True
        and live_storage_backend_status.get("configured") is True
        and live_storage_backend_status.get("active") is True
        and live_storage_backend_status.get("verified") is True
        and live_storage_backend_status.get("snapshot_verified") is True
        and live_storage_sha_valid
        and bool(live_storage_backend_status.get("last_sync_at"))
        and live_storage_backend_status.get("truthful") is True
    )
    live_storage_explicitly_blocked = bool(
        live_storage
        and (
            live_storage.get("persistent") is False
            or (live_storage.get("mode") and live_storage.get("mode") != "durable")
            or (live_storage_backend_status.get("contract_enabled") is False)
            or (live_storage_backend_status.get("configured") is False)
            or (live_storage_backend_status.get("active") is False)
            or (live_storage_backend_status.get("verified") is False)
            or (live_storage_backend_status.get("snapshot_verified") is False)
        )
    )
    storage_pass = bool(
        live_storage_pass
        and storage_status == "PASS"
        and (storage or {}).get("backend") in {"backblaze_b2", "supabase_s3"}
        and (storage or {}).get("canary") == "PUT_HEAD_GET_SHA_PASS"
        and (storage or {}).get("snapshot") == "PUT_HEAD_GET_SHA_PASS"
        and (storage or {}).get("fresh_process_restore") == "PASS"
        and str((storage or {}).get("object_key") or "").strip()
        and _int_at_least((storage or {}).get("snapshot_size_bytes"), 1)
        and _valid_hex_digest(storage_snapshot_sha)
        and str((storage or {}).get("restored_project_marker") or "").startswith("ai-infinity-storage-verification-")
        and _valid_hex_digest(storage_payload_sha)
        and (storage or {}).get("production_snapshot_overwritten") is False
        and (storage or {}).get("truthful") is True
    )

    inference_gpu = (inference or {}).get("gpu") if isinstance((inference or {}).get("gpu"), dict) else {}
    inference_sha = str((inference or {}).get("artifact_sha256") or "")
    inference_pass = bool(
        inference_status == "PASS"
        and (inference or {}).get("provider") == "wan2.2-ti2v-5b"
        and (inference or {}).get("model_id") == "Wan-AI/Wan2.2-TI2V-5B"
        and _valid_hex_digest((inference or {}).get("model_revision"), 40)
        and str((inference or {}).get("source_revision") or "") == "1ea34ff48f87168174e12956e200b1d908b1c5ff"
        and (inference or {}).get("license") == "apache-2.0"
        and (inference or {}).get("license_verified") is True
        and (inference or {}).get("storage_persistent") is True
        and inference_gpu.get("available") is True
        and _int_at_least(inference_gpu.get("total_vram_mb"), 24576)
        and _int_at_least(inference_gpu.get("free_vram_mb"), 22000)
        and str((inference or {}).get("job_id") or "").startswith("wan_")
        and len(str((inference or {}).get("job_id") or "")) == 36
        and all(character in "0123456789abcdef" for character in str((inference or {}).get("job_id") or "")[4:])
        and (inference or {}).get("inference_request_sent") is True
        and _valid_hex_digest(inference_sha)
        and _int_at_least((inference or {}).get("artifact_size_bytes"), 100_000)
        and (inference or {}).get("resolution") == [1280, 720]
        and _float_within((inference or {}).get("fps"), 24.0, 0.05)
        and (inference or {}).get("video_codec") == "h264"
        and (inference or {}).get("full_decode") == "PASS"
        and (inference or {}).get("truthful") is True
    )

    production_requirements = {
        "engineering_acceptance": engineering_pass,
        "technical_rights_evidence": technical_rights_pass,
        "otio_import_rerender": otio_rerender_pass,
        "durable_storage_round_trip_and_fresh_process_restore": storage_pass,
        "live_gpu_inference_and_artifact_verification": inference_pass,
        "exact_candidate_live_deployment": live_deployment_pass,
        "human_creative_and_rights_approval": creative_approval == "PASS",
    }
    production_pass = all(production_requirements.values())

    blockers = [name for name, passed in production_requirements.items() if not passed]
    record: dict[str, Any] = {
        "schema": "ai-infinity-production-certificate.v1",
        "repository": "aurlooij-afk/AI-INFINITY",
        "candidate_commit_sha": candidate_sha,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "ENGINEERING_STATE": "PASS" if engineering_pass else "FAIL",
        "PRODUCTION_VERIFIED": "PASS" if production_pass else "BLOCKED",
        "CREATIVE_APPROVAL": creative_approval,
        "gates": {
            "final_acceptance": "PASS" if engineering_pass else str((acceptance or {}).get("overall_status") or "MISSING"),
            "gemini_writer_preflight": "PASS" if gemini_writer_pass else "BLOCKED_OR_FAIL",
            "900s_gemini_narration": "PASS" if longform_gemini_pass else "FAIL_OR_MISSING",
            "flagship_technical_and_rights_evidence": "PASS" if technical_rights_pass else "FAIL_OR_MISSING",
            "otio_import_and_rerender": "PASS" if otio_rerender_pass else "BLOCKED",
            "durable_storage": (
                "PASS" if storage_pass else
                "BLOCKED" if storage_status == "BLOCKED" or live_storage_explicitly_blocked else
                storage_status if storage_status == "FAIL" else
                "FAIL_OR_INCOMPLETE"
            ),
            "live_gpu_inference": (
                "PASS" if inference_pass else
                inference_status if inference_status in {"FAIL", "BLOCKED"} else
                "FAIL_OR_INCOMPLETE"
            ),
            "live_deployment": (
                "PASS" if live_deployment_pass else
                "FAIL" if deployment_status == "PASS" and deployment_sha != candidate_sha else
                "BLOCKED"
            ),
            "human_creative_and_rights_approval": creative_approval,
        },
        "production_requirements": production_requirements,
        "remaining_blockers": blockers,
        "deployed_commit_sha": deployment_sha or None,
        "human_approval_evidence_present": human_approval is not None,
        "reviewed_media_sha256": flagship_media_sha256 or None,
        "human_approval_media_sha256": (human_approval or {}).get("reviewed_media_sha256"),
        "evidence_sha256": evidence_hashes,
        "truthful": True,
    }
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record["certificate_sha256"] = hashlib.sha256(canonical).hexdigest()
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--acceptance", type=Path, default=Path("final-acceptance-result.json"))
    parser.add_argument("--gemini-writer", type=Path, default=Path("acceptance-evidence/gemini-writer-verification.json"))
    parser.add_argument("--storage", type=Path, default=Path("acceptance-evidence/b2-storage-verification.json"))
    parser.add_argument("--inference", type=Path, default=Path("acceptance-evidence/wan-live-inference-verification.json"))
    parser.add_argument("--deployment", type=Path, default=Path("acceptance-evidence/live-deployment-verification.json"))
    parser.add_argument("--human-approval", type=Path, default=Path("acceptance-evidence/human-creative-approval.json"))
    parser.add_argument("--output", type=Path, default=Path("production-certificate.json"))
    parser.add_argument("--report", type=Path, default=Path("production-gate-report.json"))
    args = parser.parse_args()

    if len(args.candidate_sha) != 40 or any(c not in "0123456789abcdef" for c in args.candidate_sha.lower()):
        parser.error("--candidate-sha must be a full 40-character Git commit SHA")

    paths = {
        "acceptance": args.acceptance,
        "gemini_writer": args.gemini_writer,
        "storage": args.storage,
        "inference": args.inference,
        "deployment": args.deployment,
        "human_approval": args.human_approval,
    }
    values = {name: read_json(path) for name, path in paths.items()}
    record = build_certificate(
        candidate_sha=args.candidate_sha.lower(),
        acceptance=values["acceptance"],
        storage=values["storage"],
        inference=values["inference"],
        deployment=values["deployment"],
        human_approval=values["human_approval"],
        evidence_hashes={name: file_sha256(path) for name, path in paths.items()},
        gemini_writer=values["gemini_writer"],
    )
    if record["PRODUCTION_VERIFIED"] == "PASS":
        # Only a fully passing, evidence-bound release receives a certificate.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        args.report.unlink(missing_ok=True)
        print(
            "PRODUCTION_CERTIFICATE="
            + json.dumps({
                "candidate_commit_sha": record["candidate_commit_sha"],
                "ENGINEERING_STATE": record["ENGINEERING_STATE"],
                "PRODUCTION_VERIFIED": record["PRODUCTION_VERIFIED"],
                "CREATIVE_APPROVAL": record["CREATIVE_APPROVAL"],
                "certificate_sha256": record["certificate_sha256"],
                "remaining_blockers": [],
            }, sort_keys=True)
        )
    else:
        # An incomplete attempt produces a gate report, never a certificate.
        # Remove stale output so a previous pass cannot be mistaken for this run.
        args.output.unlink(missing_ok=True)
        report = dict(record)
        report.pop("certificate_sha256", None)
        report["schema"] = "ai-infinity-production-gate-report.v1"
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            "PRODUCTION_GATE_REPORT="
            + json.dumps({
                "candidate_commit_sha": report["candidate_commit_sha"],
                "ENGINEERING_STATE": report["ENGINEERING_STATE"],
                "PRODUCTION_VERIFIED": report["PRODUCTION_VERIFIED"],
                "CREATIVE_APPROVAL": report["CREATIVE_APPROVAL"],
                "remaining_blockers": report["remaining_blockers"],
                "report_file": str(args.report),
            }, sort_keys=True)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
