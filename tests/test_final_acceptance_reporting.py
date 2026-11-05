"""Regression tests for persistent acceptance results and scenario isolation."""

import importlib.util
import json
from pathlib import Path


EVIDENCE_SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "verify_creator_runtime_evidence.py"
EVIDENCE_SPEC = importlib.util.spec_from_file_location("ai_infinity_creator_runtime_evidence_under_test", EVIDENCE_SCRIPT_PATH)
assert EVIDENCE_SPEC is not None and EVIDENCE_SPEC.loader is not None
creator_evidence = importlib.util.module_from_spec(EVIDENCE_SPEC)
EVIDENCE_SPEC.loader.exec_module(creator_evidence)


SUITE_PATH = Path(__file__).with_name("final_acceptance_suite.py")
SPEC = importlib.util.spec_from_file_location("ai_infinity_acceptance_suite_under_test", SUITE_PATH)
assert SPEC is not None and SPEC.loader is not None
suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(suite)


def test_acceptance_runner_persists_a_failure_and_continues_to_the_next_scenario(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_INFINITY_EVIDENCE_DIR", str(tmp_path))
    suite.SCENARIO_RESULTS.clear()

    def fail_with_project_evidence():
        raise AssertionError({
            "project_id": "studio_acceptance_failure_fixture",
            "status": "failed",
            "stage": "quality_control",
            "error": "source motion coverage was not met",
        })

    failed = suite._record_scenario(
        "01",
        "scenario with real project failure",
        fail_with_project_evidence,
        requested={"duration_seconds": 20},
    )
    passed = suite._record_scenario(
        "02",
        "independent scenario after failure",
        lambda: {"project_id": "studio_independent_pass", "actual_duration_seconds": 20.0},
    )

    document = json.loads((tmp_path / "final-acceptance-result.json").read_text(encoding="utf-8"))
    assert failed["status"] == "FAIL"
    assert failed["project_id"] == "studio_acceptance_failure_fixture"
    assert "traceback" in failed["failure"]
    assert "source motion coverage" in failed["failure"]["message"]
    assert passed["status"] == "PASS"
    assert passed["project_id"] == "studio_independent_pass"
    assert document["passed"] is False
    assert document["overall_status"] == "INCOMPLETE"
    assert [item["scenario_id"] for item in document["tests"]] == ["01", "02"]
    assert document["completed_scenarios"] == 2


def test_human_review_scorecard_matches_the_candidate_bound_approval_thresholds():
    policy = suite.HUMAN_REVIEW_POLICY
    assert policy["average_minimum"] == 4.5
    assert policy["dimension_minimum"] == 4.0
    assert policy["critical_dimension_minimum"] == 4.0
    assert policy["role_score_minimum"] == 4.0
    assert policy["required_approval_roles"] == [
        "director", "cinematographer", "editor", "sound", "story", "rights",
    ]
    assert policy["explicit_rights_approval_required"] is True
    assert policy["critical_fact_or_rights_failure_allowed"] is False


def test_human_approval_workflow_requires_each_of_the_19_scenarios_exactly_once():
    workflow_path = SUITE_PATH.parents[1] / ".github" / "workflows" / "human-approval-certificate.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    assert "range(1, 20)" in workflow
    assert 'acceptance.get("scenario_count")' in workflow
    assert 'acceptance.get("completed_scenarios")' in workflow
    assert "set(scenario_map) != expected_scenario_ids" in workflow
    assert "Every one of the 19 acceptance scenarios must pass." in workflow


def test_caption_phrase_is_reconstructed_across_srt_cue_boundaries():
    srt = (
        "1\n00:00:00,000 --> 00:00:02,000\n"
        "Every extraordinary world begins with something nobody else\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\ncan see.\n"
    )
    assert "every extraordinary world begins with something nobody else can see." in (
        suite._normalized_srt_caption_text(srt)
    )


def test_blocked_acceptance_scenarios_are_not_misreported_as_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_INFINITY_EVIDENCE_DIR", str(tmp_path))
    suite.SCENARIO_RESULTS.clear()

    blocked = suite._record_blocked_scenario(
        "10",
        "recovery depends on concurrency",
        "concurrency did not produce an isolated project",
        requested={"requires": "scenario 09"},
    )

    document = json.loads((tmp_path / "final-acceptance-result.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "BLOCKED"
    assert blocked["failure"]["reason"] == "concurrency did not produce an isolated project"
    assert document["passed"] is False
    assert document["tests"][0]["status"] == "BLOCKED"


def test_runtime_review_packet_reports_candidate_gates_without_stale_suspension_claims():
    notes = creator_evidence._remaining_release_gate_notes()
    text = "\n".join(notes).lower()
    assert "exact-candidate live render deployment" in text
    assert "standalone ci run is not production deployment evidence" in text
    assert "19-scenario" in text and "900-second" in text
    assert "fresh-process recovery" in text
    assert "authenticated wan2.2 gpu inference" in text
    assert "authorized reviewer" in text
    assert "service is suspended" not in text
