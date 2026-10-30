"""Regression tests for persistent acceptance results and scenario isolation."""

import importlib.util
import json
from pathlib import Path


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
