from __future__ import annotations

import pytest

from ai_infinity.execution_fabric import (
    ExecutionPlan,
    ExecutionStep,
    InvalidExecutionPlan,
    ToolContract,
    execute_plan,
)


def test_executes_dependency_graph_in_order_and_verifies_outputs():
    calls = []
    tools = {
        "research": ToolContract(
            "research",
            lambda data: calls.append("research") or {"sources": 2},
            verifier=lambda output: output["sources"] > 0,
        ),
        "draft": ToolContract(
            "draft",
            lambda data: calls.append("draft") or {"draft": "ready"},
            verifier=lambda output: output.get("draft") == "ready",
        ),
    }
    plan = ExecutionPlan("req-1", (
        ExecutionStep("a", "research"),
        ExecutionStep("b", "draft", depends_on=("a",)),
    ))

    result = execute_plan(plan, tools)

    assert calls == ["research", "draft"]
    assert result["status"] == "succeeded"
    assert result["summary"]["succeeded"] == 2
    assert result["steps"]["a"]["verified"] is True


def test_side_effecting_tool_is_never_called_without_named_authority():
    calls = []
    tools = {
        "publish": ToolContract(
            "publish",
            lambda data: calls.append("publish") or {"published": True},
            side_effect=True,
            required_authority="publish:youtube",
        )
    }

    result = execute_plan(
        ExecutionPlan("req-2", (ExecutionStep("publish", "publish"),)),
        tools,
    )

    assert calls == []
    assert result["status"] == "authorization_required"
    assert result["steps"]["publish"]["executed"] is False


def test_approved_side_effect_runs_only_with_exact_authority():
    calls = []
    tools = {
        "publish": ToolContract(
            "publish",
            lambda data: calls.append("publish") or {"published": True},
            side_effect=True,
            required_authority="publish:youtube",
        )
    }
    plan = ExecutionPlan("req-3", (ExecutionStep("publish", "publish"),))

    result = execute_plan(plan, tools, approved_authorities={"publish:youtube"})

    assert calls == ["publish"]
    assert result["status"] == "succeeded"


def test_failed_step_blocks_dependants_but_independent_work_continues():
    tools = {
        "broken": ToolContract("broken", lambda data: (_ for _ in ()).throw(RuntimeError("secret"))),
        "dependent": ToolContract("dependent", lambda data: {"ok": True}),
        "independent": ToolContract("independent", lambda data: {"ok": True}),
    }
    plan = ExecutionPlan("req-4", (
        ExecutionStep("a", "broken"),
        ExecutionStep("b", "dependent", depends_on=("a",)),
        ExecutionStep("c", "independent"),
    ))

    result = execute_plan(plan, tools)

    assert result["status"] == "partially_succeeded"
    assert result["steps"]["a"]["reason"] == "tool_execution_error"
    assert "secret" not in str(result)
    assert result["steps"]["b"]["status"] == "blocked"
    assert result["steps"]["c"]["status"] == "succeeded"


def test_rejects_cycles_and_unknown_dependencies():
    with pytest.raises(InvalidExecutionPlan, match="cycle"):
        execute_plan(
            ExecutionPlan("req-5", (
                ExecutionStep("a", "noop", depends_on=("b",)),
                ExecutionStep("b", "noop", depends_on=("a",)),
            )),
            {},
        )

    with pytest.raises(InvalidExecutionPlan, match="unknown dependencies"):
        execute_plan(
            ExecutionPlan("req-6", (ExecutionStep("a", "noop", depends_on=("missing",)),)),
            {},
        )


def test_failed_verification_is_not_reported_as_success():
    tools = {
        "render": ToolContract(
            "render",
            lambda data: {"file": "final.mp4"},
            verifier=lambda output: False,
        )
    }
    result = execute_plan(
        ExecutionPlan("req-7", (ExecutionStep("render", "render"),)),
        tools,
    )
    assert result["status"] == "failed"
    assert result["steps"]["render"]["reason"] == "verification_failed"
