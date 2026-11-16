from __future__ import annotations

import pytest
from fastapi import HTTPException

import ai_infinity_canonical as canonical
from ai_infinity.goal_planner import compile_goal


def test_single_goal_compiles_to_a_preview_using_an_existing_capability(monkeypatch):
    dispatched = []

    def forbidden_dispatch(*args, **kwargs):
        dispatched.append(args)
        raise AssertionError("planning must never dispatch a command")

    monkeypatch.setattr(canonical, "dispatch_command", forbidden_dispatch)
    plan = compile_goal("Create a 20 second video about responsible design")

    assert plan["schema"] == "ai-infinity.goal-plan.v1"
    assert plan["status"] == "READY_FOR_REVIEW"
    assert plan["ready_for_submission"] is True
    assert plan["execution_started"] is False
    assert plan["step_count"] == 1
    assert plan["steps"][0]["route"] == "create"
    assert plan["steps"][0]["capability_id"] == "creative.create"
    assert plan["commands"][0]["command"] == "Create a 20 second video about responsible design"
    assert dispatched == []


def test_ordinary_next_generation_phrase_is_not_split_into_a_second_task():
    goal = "Create a video for the next generation of creators"
    plan = compile_goal(goal)

    assert plan["status"] == "READY_FOR_REVIEW"
    assert plan["step_count"] == 1
    assert plan["commands"][0]["command"] == goal


def test_goal_compiler_builds_a_valid_create_then_project_status_dependency():
    plan = compile_goal(
        "Create a 20 second video about responsible design then check the project status"
    )

    assert plan["status"] == "READY_FOR_REVIEW"
    assert plan["step_count"] == 2
    create, status = plan["steps"]
    assert create["route"] == "create"
    assert status["route"] == "project_status"
    assert status["project_id_from"] == create["id"]
    assert status["depends_on"] == [create["id"]]
    assert plan["commands"][1]["project_id_from"] == create["id"]
    assert "__planned_project_id__" not in str(plan)


def test_goal_compiler_requires_existing_project_context_for_project_status():
    plan = compile_goal("check the project status")

    assert plan["status"] == "NEEDS_CLARIFICATION"
    assert plan["ready_for_submission"] is False
    assert plan["commands"] == []
    assert plan["steps"][0]["route"] == "project_id_required"

    existing = compile_goal("check the project status", project_id="studio_existing_project")
    assert existing["status"] == "READY_FOR_REVIEW"
    assert existing["commands"][0]["payload"]["project_id"] == "studio_existing_project"


def test_consequential_goal_is_blocked_for_action_specific_authorization():
    plan = compile_goal("publish this video to YouTube")

    assert plan["status"] == "NEEDS_AUTH"
    assert plan["ready_for_submission"] is False
    assert plan["execution_started"] is False
    assert plan["commands"] == []
    assert plan["steps"][0]["route"] == "authorization_required"
    assert plan["steps"][0]["status"] == "NEEDS_AUTH"


def test_unsupported_goal_is_never_silently_rewritten_as_video_creation():
    plan = compile_goal("Build a new mobile application for my business")

    assert plan["status"] == "NEEDS_CLARIFICATION"
    assert plan["ready_for_submission"] is False
    assert plan["commands"] == []
    assert plan["steps"][0]["route"] == "unsupported_deliverable"


def test_goal_compiler_enforces_input_and_step_limits():
    with pytest.raises(HTTPException) as too_short:
        compile_goal("  ")
    assert too_short.value.status_code == 422

    with pytest.raises(HTTPException) as too_long:
        compile_goal("x" * 8001)
    assert too_long.value.status_code == 422

    chained = " then ".join("inspect available capabilities" for _ in range(21))
    with pytest.raises(HTTPException) as too_many:
        compile_goal(chained)
    assert too_many.value.status_code == 422
