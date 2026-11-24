from __future__ import annotations

"""HTTP adapter that connects the DAG kernel to AI Infinity's canonical router.

The adapter accepts only canonical natural-language commands. It does not
introduce a second tool registry or bypass canonical routing, ownership checks,
or action-specific authorization.
"""

from typing import Any

from fastapi import HTTPException, Request, Response

import ai_infinity_canonical as canonical
from ai_infinity.execution_fabric import (
    ExecutionPlan,
    ExecutionStep,
    ToolContract,
    execute_plan,
)

MAX_PLAN_STEPS = 20
MAX_COMMAND_LENGTH = 4000
MAX_REQUEST_ID_LENGTH = 128
_AGENT_ONLY_ROUTES = {"script", "strategy", "marketing", "seo"}


def _verified_canonical_result(value: Any) -> bool:
    """Accept only a truthful result that confirms a supported operation ran."""
    if not isinstance(value, dict) or value.get("truthful") is not True:
        return False
    if value.get("status") not in {"completed", "queued", "running", "producing", "discovery_started"}:
        return False
    if value.get("executed") is True or value.get("read_only") is True:
        return True
    # These routes return a real reviewed text/analysis result, not a media artifact.
    return value.get("route") in _AGENT_ONLY_ROUTES and value.get("review_required") is True


def register(app: Any) -> None:
    """Register the resumable-plan execution boundary once per application."""
    if getattr(app.state, "ai_infinity_execution_fabric_runtime_registered", False):
        return

    @app.post("/infinity/canonical/execute-plan")
    async def canonical_execute_plan(request: Request, response: Response):
        user_id = canonical.current_user(request, response)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "The execution-plan body must be a JSON object.")

        request_id = str(body.get("request_id") or "").strip()
        if not request_id or len(request_id) > MAX_REQUEST_ID_LENGTH:
            raise HTTPException(422, "request_id is required and must be at most 128 characters.")

        raw_steps = body.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_PLAN_STEPS:
            raise HTTPException(422, f"steps must contain 1..{MAX_PLAN_STEPS} items.")

        steps: list[ExecutionStep] = []
        for index, item in enumerate(raw_steps, 1):
            if not isinstance(item, dict):
                raise HTTPException(422, f"step {index} must be an object.")
            step_id = str(item.get("step_id") or "").strip()
            command = str(item.get("command") or "").strip()
            if not step_id:
                raise HTTPException(422, f"step {index} requires step_id.")
            if not 3 <= len(command) <= MAX_COMMAND_LENGTH:
                raise HTTPException(422, f"step {step_id} command must contain 3..{MAX_COMMAND_LENGTH} characters.")
            payload = item.get("payload") or {}
            dependencies = item.get("depends_on") or []
            if not isinstance(payload, dict):
                raise HTTPException(422, f"step {step_id} payload must be an object.")
            if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
                raise HTTPException(422, f"step {step_id} depends_on must be a list of step IDs.")
            steps.append(ExecutionStep(
                step_id=step_id,
                tool="canonical.dispatch",
                inputs={"command": command, "payload": payload},
                depends_on=tuple(dependencies),
            ))

        def dispatch(inputs: dict[str, Any]) -> dict[str, Any]:
            return canonical.dispatch_command(
                inputs["command"],
                user_id,
                inputs.get("payload") or {},
            )

        contract = ToolContract(
            name="canonical.dispatch",
            handler=dispatch,
            verifier=_verified_canonical_result,
        )
        return execute_plan(
            ExecutionPlan(request_id=request_id, steps=tuple(steps)),
            {"canonical.dispatch": contract},
        )

    app.state.ai_infinity_execution_fabric_runtime_registered = True
