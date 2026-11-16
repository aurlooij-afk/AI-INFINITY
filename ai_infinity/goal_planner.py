from __future__ import annotations

"""Conservative natural-language goal to validated command-plan compiler.

Planning is preview-only: this module never dispatches a command. It reuses the
canonical classifier and the exact batch validator so a preview cannot advertise
a route or DAG shape that the execution boundary does not recognize.
"""

import hashlib
import json
import re
from typing import Any

from fastapi import HTTPException, Request, Response

import ai_infinity_canonical as canonical
import ai_infinity.execution_core as execution_core
from universal_command_batch import normalize_commands


MAX_GOAL_LENGTH = 8000
MAX_PLAN_STEPS = 20
_PLAN_SPLIT = re.compile(
    r"\s+(?:and\s+then|then|after\s+that|aur\s+phir|uske\s+baad|phir)\s+",
    re.IGNORECASE,
)
_PROJECT_ID_REQUIRED = "project_id_required"
_BLOCKING_ROUTES = {
    "authorization_required",
    "unsupported",
    "unsupported_deliverable",
    "deliverable_required",
    "unsupported_intent",
    "unsupported_deliverable",
    "project_id_required",
}
_ROUTES_REQUIRING_OPERATOR_AUTH = {"github_api"}


def _split_goal(goal: str) -> list[str]:
    parts = [part.strip(" ,;:.") for part in _PLAN_SPLIT.split(goal) if part.strip(" ,;:.")]
    return parts or [goal.strip()]


def _capability_index() -> dict[str, dict[str, Any]]:
    snapshot = execution_core.registry_snapshot()
    return {
        str(item.get("route") or ""): dict(item)
        for item in snapshot.get("capabilities", [])
        if isinstance(item, dict) and item.get("route")
    }


def compile_goal(goal: Any, *, project_id: Any = None) -> dict[str, Any]:
    """Compile one goal into a safe batch preview; never execute it."""
    text = str(goal or "").strip()
    if len(text) < 3 or len(text) > MAX_GOAL_LENGTH:
        raise HTTPException(422, f"goal length must be 3..{MAX_GOAL_LENGTH} characters.")

    existing_project_id = str(project_id or "").strip()
    if len(existing_project_id) > 200:
        raise HTTPException(422, "project_id is too long.")

    phrases = _split_goal(text)
    if len(phrases) > MAX_PLAN_STEPS:
        raise HTTPException(422, f"A goal plan is limited to {MAX_PLAN_STEPS} steps.")

    capabilities = _capability_index()
    preview_steps: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    last_step_id: str | None = None
    latest_created_step_id: str | None = None
    blocked_reason: str | None = None
    blocked_status: str | None = None

    for index, phrase in enumerate(phrases, 1):
        step_id = f"step-{index}"
        payload: dict[str, Any] = {}
        decision = canonical.classify_command(phrase, payload)
        route = str(decision.get("route") or "unsupported")

        # Resolve project-scoped commands only when a real project context was
        # supplied or an earlier create step can provide its runtime project id.
        # A placeholder is used only for route classification and is never
        # returned or dispatched.
        project_id_from = None
        if route == _PROJECT_ID_REQUIRED:
            if existing_project_id:
                payload["project_id"] = existing_project_id
                decision = canonical.classify_command(phrase, payload)
                route = str(decision.get("route") or "unsupported")
            elif latest_created_step_id:
                decision = canonical.classify_command(phrase, {"project_id": "__planned_project_id__"})
                route = str(decision.get("route") or "unsupported")
                if route in {"project_status", "edit"}:
                    project_id_from = latest_created_step_id

        capability = capabilities.get(route)
        reason = str(decision.get("reason") or "")
        mode = str((capability or {}).get("mode") or "unregistered")
        status = "PLANNED"

        if route == "authorization_required":
            status = "NEEDS_AUTH"
            blocked_status = "NEEDS_AUTH"
            blocked_reason = reason or "This consequential action requires action-specific authorization."
        elif not capability:
            status = "UNSUPPORTED"
            blocked_status = "NEEDS_CLARIFICATION"
            blocked_reason = reason or f"No registered capability matches step {index}."
        elif mode != "execute":
            status = "GUARDED"
            blocked_status = "NEEDS_CLARIFICATION"
            blocked_reason = reason or f"Step {index} resolves to a non-executable guard."
        elif route == "github_api" or route in _ROUTES_REQUIRING_OPERATOR_AUTH:
            status = "NEEDS_AUTH"
            blocked_status = "NEEDS_AUTH"
            blocked_reason = "GitHub operations require the separate operator authorization and server-side credential scope."
        elif route in _BLOCKING_ROUTES:
            status = "BLOCKED"
            blocked_status = "NEEDS_CLARIFICATION" if route != "authorization_required" else "NEEDS_AUTH"
            blocked_reason = reason or f"Step {index} is blocked by the canonical intent guard."

        dependencies: list[str] = []
        if last_step_id:
            dependencies.append(last_step_id)
        if project_id_from and project_id_from not in dependencies:
            dependencies.append(project_id_from)

        row = {
            "id": step_id,
            "command": phrase,
            "payload": payload if not project_id_from else {},
            "depends_on": dependencies,
            "continue_on_error": False,
        }
        if project_id_from:
            row["project_id_from"] = project_id_from

        preview_steps.append({
            "id": step_id,
            "command": phrase,
            "route": route,
            "capability_id": (capability or {}).get("id"),
            "domain": (capability or {}).get("domain"),
            "side_effect_policy": (capability or {}).get("side_effect_policy"),
            "verification_contract": (capability or {}).get("verification_contract"),
            "handler_binding": (capability or {}).get("handler"),
            "readiness_state": (capability or {}).get("readiness_state"),
            "status": status,
            "reason": reason or None,
            "depends_on": dependencies,
            "project_id_from": project_id_from,
        })

        if status != "PLANNED":
            # Do not return an executable partial batch when any part of the
            # requested goal is unknown, guarded, or requires extra authority.
            break

        commands.append(row)
        last_step_id = step_id
        if route == "create":
            latest_created_step_id = step_id

    if blocked_status:
        normalized_commands: list[dict[str, Any]] = []
        plan_status = blocked_status
    else:
        normalized_commands = normalize_commands(commands)
        plan_status = "READY_FOR_REVIEW"

    canonical_plan = {
        "goal": text,
        "commands": normalized_commands,
        "steps": preview_steps,
    }
    plan_sha256 = hashlib.sha256(
        json.dumps(canonical_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return {
        "schema": "ai-infinity.goal-plan.v1",
        "plan_id": "plan_" + plan_sha256[:24],
        "plan_sha256": plan_sha256,
        "status": plan_status,
        "ready_for_submission": plan_status == "READY_FOR_REVIEW" and bool(normalized_commands),
        "execution_started": False,
        "goal": text,
        "step_count": len(preview_steps),
        "steps": preview_steps,
        "commands": normalized_commands,
        "submission": {
            "method": "POST",
            "path": "/infinity/canonical/batches",
            "requires_owner_review": True,
            "requires_batch_idempotency_key": True,
            "authorization_is_rechecked_at_dispatch": True,
            "verification_remains_domain_specific": True,
        },
        "blocked_reason": blocked_reason,
        "limitations": [
            "This endpoint compiles and validates a plan only; it never executes commands.",
            "Only routes already recognized by the canonical runtime are eligible.",
            "Sequential dependencies express ordering; project IDs are handed off only for supported create-to-edit/status steps.",
            "External side effects still require their existing action-specific authorization.",
            "A planned capability binding is not proof that a provider is configured or operational.",
        ],
        "truthful": True,
    }


def register(app: Any) -> None:
    """Register the session-scoped, preview-only natural-language planner."""
    if getattr(app.state, "ai_infinity_goal_planner_registered", False):
        return

    @app.post("/infinity/canonical/plan")
    async def canonical_goal_plan(request: Request, response: Response):
        user_id = canonical.current_user(request, response)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "The goal-planning body must be a JSON object.")
        goal = body.get("goal") or body.get("command") or ""
        return {
            **compile_goal(goal, project_id=body.get("project_id")),
            "owner_context": "session_scoped",
            "truthful": True,
        }

    app.state.ai_infinity_goal_planner_registered = True
