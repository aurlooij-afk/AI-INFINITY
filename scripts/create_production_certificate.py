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


def _valid_human_approval(value: dict[str, Any] | None, candidate_sha: str) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("candidate_commit_sha") != candidate_sha or value.get("approved") is not True:
        return False
    try:
        average = float(value.get("average_score"))
    except (TypeError, ValueError):
        return False
    dimensions = value.get("dimension_scores")
    if not isinstance(dimensions, dict) or not dimensions:
        return False
    try:
        all_dimensions_pass = all(float(score) >= 4.0 for score in dimensions.values())
    except (TypeError, ValueError):
        return False
    return average >= 4.5 and all_dimensions_pass and value.get("rights_approval") is True


def build_certificate(
    candidate_sha: str,
    acceptance: dict[str, Any] | None,
    storage: dict[str, Any] | None,
    inference: dict[str, Any] | None,
    deployment: dict[str, Any] | None,
    human_approval: dict[str, Any] | None,
    evidence_hashes: dict[str, str | None],
) -> dict[str, Any]:
    scenarios = _scenario_map(acceptance)
    scenario_statuses = [str(row.get("status") or "") for row in scenarios.values()]
    engineering_pass = bool(
        acceptance
        and acceptance.get("overall_status") == "PASS"
        and acceptance.get("passed") is True
        and int(acceptance.get("completed_scenarios") or 0) == 13
        and len(scenarios) == 13
        and len(scenario_statuses) == 13
        and all(status == "PASS" for status in scenario_statuses)
    )

    flagship = scenarios.get("01", {}).get("result", {})
    otio = scenarios.get("04", {}).get("result", {})
    creative_approval = "PASS" if _valid_human_approval(human_approval, candidate_sha) else "PENDING"

    storage_status = str((storage or {}).get("status") or "BLOCKED").upper()
    inference_status = str((inference or {}).get("status") or "BLOCKED").upper()
    deployment_status = str((deployment or {}).get("status") or "BLOCKED").upper()
    deployment_sha = str((deployment or {}).get("deployed_commit_sha") or "")

    technical_rights_pass = bool(
        flagship
        and flagship.get("truth_verified") is True
        and int(flagship.get("fallback_visual_count") or 0) == 0
        and int(flagship.get("visual_rights_unresolved_count") or 0) == 0
    )
    otio_rerender_pass = bool(
        otio
        and otio.get("otio_import_rerender_passed") is True
        and otio.get("otio_rerender_full_decode_passed") is True
        and bool(otio.get("otio_rerender_sha256"))
    )
    live_deployment_pass = deployment_status == "PASS" and deployment_sha == candidate_sha
    storage_pass = storage_status == "PASS"
    inference_pass = inference_status == "PASS"

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
            "flagship_technical_and_rights_evidence": "PASS" if technical_rights_pass else "FAIL_OR_MISSING",
            "otio_import_and_rerender": "PASS" if otio_rerender_pass else "BLOCKED",
            "durable_storage": storage_status if storage_status in {"PASS", "FAIL", "BLOCKED"} else "FAIL",
            "live_gpu_inference": inference_status if inference_status in {"PASS", "FAIL", "BLOCKED"} else "FAIL",
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
    parser.add_argument("--storage", type=Path, default=Path("acceptance-evidence/b2-storage-verification.json"))
    parser.add_argument("--inference", type=Path, default=Path("acceptance-evidence/wan-live-inference-verification.json"))
    parser.add_argument("--deployment", type=Path, default=Path("acceptance-evidence/live-deployment-verification.json"))
    parser.add_argument("--human-approval", type=Path, default=Path("acceptance-evidence/human-creative-approval.json"))
    parser.add_argument("--output", type=Path, default=Path("production-certificate.json"))
    args = parser.parse_args()

    if len(args.candidate_sha) != 40 or any(c not in "0123456789abcdef" for c in args.candidate_sha.lower()):
        parser.error("--candidate-sha must be a full 40-character Git commit SHA")

    paths = {
        "acceptance": args.acceptance,
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
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(
        "PRODUCTION_CERTIFICATE="
        + json.dumps({
            "candidate_commit_sha": record["candidate_commit_sha"],
            "ENGINEERING_STATE": record["ENGINEERING_STATE"],
            "PRODUCTION_VERIFIED": record["PRODUCTION_VERIFIED"],
            "CREATIVE_APPROVAL": record["CREATIVE_APPROVAL"],
            "certificate_sha256": record["certificate_sha256"],
            "remaining_blockers": record["remaining_blockers"],
        }, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
