from __future__ import annotations

"""Small, deterministic execution kernel for registered AI Infinity tools.

This module deliberately does not discover tools, call language models, or grant
authority. Callers must supply an explicit plan and a registry of trusted
handlers. Side-effecting handlers are blocked unless their named authority is
present in the invocation's approval set.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping


Handler = Callable[[Mapping[str, Any]], Any]
Verifier = Callable[[Any], bool]


@dataclass(frozen=True)
class ToolContract:
    name: str
    handler: Handler
    side_effect: bool = False
    required_authority: str | None = None
    verifier: Verifier | None = None


@dataclass(frozen=True)
class ExecutionStep:
    step_id: str
    tool: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionPlan:
    request_id: str
    steps: tuple[ExecutionStep, ...]


class InvalidExecutionPlan(ValueError):
    """Raised when a plan has duplicate IDs, missing dependencies, or cycles."""


def _topological_order(steps: tuple[ExecutionStep, ...]) -> list[ExecutionStep]:
    by_id: dict[str, ExecutionStep] = {}
    for step in steps:
        if not step.step_id or step.step_id in by_id:
            raise InvalidExecutionPlan("step IDs must be non-empty and unique")
        by_id[step.step_id] = step

    for step in steps:
        missing = set(step.depends_on) - by_id.keys()
        if missing:
            raise InvalidExecutionPlan(
                f"step {step.step_id!r} has unknown dependencies: {sorted(missing)}"
            )
        if step.step_id in step.depends_on:
            raise InvalidExecutionPlan(f"step {step.step_id!r} depends on itself")

    remaining = {step.step_id: set(step.depends_on) for step in steps}
    ordered: list[ExecutionStep] = []
    while remaining:
        ready = [step for step in steps if step.step_id in remaining and not remaining[step.step_id]]
        if not ready:
            raise InvalidExecutionPlan("execution plan contains a dependency cycle")
        for step in ready:
            ordered.append(step)
            remaining.pop(step.step_id)
            for dependencies in remaining.values():
                dependencies.discard(step.step_id)
    return ordered


def execute_plan(
    plan: ExecutionPlan,
    tools: Mapping[str, ToolContract],
    *,
    approved_authorities: Iterable[str] = (),
) -> dict[str, Any]:
    """Execute a validated DAG in deterministic order and return truthful receipts.

    Independent steps continue after a failure. Dependants run only when every
    dependency succeeded. A side-effecting tool is never called without its
    explicitly named authority approval.
    """
    if not plan.request_id.strip():
        raise InvalidExecutionPlan("request_id is required")

    ordered = _topological_order(plan.steps)
    approvals = set(approved_authorities)
    receipts: dict[str, dict[str, Any]] = {}

    for step in ordered:
        failed_dependencies = [
            dependency for dependency in step.depends_on
            if receipts.get(dependency, {}).get("status") != "succeeded"
        ]
        if failed_dependencies:
            receipts[step.step_id] = {
                "status": "blocked",
                "reason": "dependency_not_succeeded",
                "blocked_by": failed_dependencies,
            }
            continue

        contract = tools.get(step.tool)
        if contract is None:
            receipts[step.step_id] = {
                "status": "failed",
                "reason": "tool_not_registered",
                "tool": step.tool,
            }
            continue

        if contract.side_effect:
            authority = contract.required_authority
            if not authority or authority not in approvals:
                receipts[step.step_id] = {
                    "status": "authorization_required",
                    "reason": "explicit_authority_approval_missing",
                    "required_authority": authority,
                    "executed": False,
                }
                continue

        try:
            output = contract.handler(step.inputs)
        except Exception as exc:  # keep private exception text out of receipts
            receipts[step.step_id] = {
                "status": "failed",
                "reason": "tool_execution_error",
                "error_type": type(exc).__name__,
                "executed": True,
            }
            continue

        if contract.verifier is not None:
            try:
                verified = contract.verifier(output) is True
            except Exception:
                verified = False
            if not verified:
                receipts[step.step_id] = {
                    "status": "failed",
                    "reason": "verification_failed",
                    "executed": True,
                    "output": output,
                }
                continue

        receipts[step.step_id] = {
            "status": "succeeded",
            "executed": True,
            "verified": contract.verifier is not None,
            "output": output,
        }

    statuses = [receipt["status"] for receipt in receipts.values()]
    succeeded = statuses.count("succeeded")
    blocked = statuses.count("blocked")
    authorization_required = statuses.count("authorization_required")
    failed = statuses.count("failed")
    if succeeded == len(statuses) and statuses:
        overall = "succeeded"
    elif succeeded:
        overall = "partially_succeeded"
    elif authorization_required and not failed:
        overall = "authorization_required"
    else:
        overall = "failed"

    return {
        "request_id": plan.request_id,
        "status": overall,
        "summary": {
            "total_steps": len(statuses),
            "succeeded": succeeded,
            "failed": failed,
            "blocked": blocked,
            "authorization_required": authorization_required,
        },
        "steps": receipts,
        "truthful": True,
    }
