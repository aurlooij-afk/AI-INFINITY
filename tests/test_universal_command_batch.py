from __future__ import annotations

import pytest
from fastapi import HTTPException

from universal_command_batch import execute_commands, normalize_commands


def test_normalize_commands_requires_unique_ids_and_earlier_dependencies():
    commands = normalize_commands([
        {"id": "research", "command": "research this topic"},
        {
            "id": "create",
            "command": "create a 20 second video",
            "depends_on": ["research"],
        },
        {
            "id": "status",
            "command": "check project status",
            "depends_on": ["create"],
            "project_id_from": "create",
        },
    ])
    assert [item["id"] for item in commands] == ["research", "create", "status"]
    assert commands[2]["project_id_from"] == "create"

    with pytest.raises(HTTPException):
        normalize_commands([
            {"id": "same", "command": "first valid step"},
            {"id": "same", "command": "duplicate id"},
        ])

    with pytest.raises(HTTPException):
        normalize_commands([
            {"id": "first", "command": "first valid step", "depends_on": ["later"]},
            {"id": "later", "command": "second valid step"},
        ])


def test_normalize_commands_rejects_inline_secrets_and_unsafe_payload_fields():
    with pytest.raises(HTTPException):
        normalize_commands([{"id": "one", "command": "create this api_key=abc123 video"}])
    with pytest.raises(HTTPException):
        normalize_commands([{
            "id": "one", "command": "create a video",
            "payload": {"gemini_api_key": "do-not-store"},
        }])


def test_batch_dispatches_in_order_and_binds_later_project_command():
    commands = normalize_commands([
        {"id": "create", "command": "create a 20 second video"},
        {
            "id": "inspect",
            "command": "check the project status",
            "depends_on": ["create"],
            "project_id_from": "create",
        },
    ])
    calls = []
    checkpoints = []

    def dispatch(command, user_id, payload):
        calls.append((command, user_id, dict(payload)))
        if command.startswith("create"):
            return {
                "status": "queued", "executed": True,
                "result": {"project_id": "studio_project_123"},
            }
        return {
            "status": "completed", "executed": True,
            "result": {"project_id": payload["project_id"], "state": "QUEUED"},
        }

    result = execute_commands(
        commands,
        user_id="owner-1",
        batch_id="batch-abc123",
        dispatch=dispatch,
        checkpoint=lambda rows, status: checkpoints.append((status, [dict(row) for row in rows])),
    )

    assert result["status"] == "accepted"
    assert result["accepted_steps"] == 1
    assert result["completed_steps"] == 1
    assert len(calls) == 2
    assert calls[1][2]["project_id"] == "studio_project_123"
    assert calls[0][2]["idempotency_key"] == "batch-batch-abc123-create"
    assert calls[1][2]["idempotency_key"] == "batch-batch-abc123-inspect"
    assert len(checkpoints) >= 5
    assert result["steps"][0]["state"] == "accepted"
    assert result["steps"][1]["state"] == "completed"


def test_batch_stops_after_failed_step_unless_continue_on_error_is_set():
    commands = normalize_commands([
        {"id": "bad", "command": "perform unsupported operation"},
        {"id": "not-run", "command": "check capabilities"},
    ])
    calls = []

    def dispatch(command, user_id, payload):
        calls.append(command)
        return {"status": "unsupported_or_incomplete", "executed": False}

    result = execute_commands(
        commands, user_id="owner-1", batch_id="batch-xyz123",
        dispatch=dispatch, checkpoint=lambda rows, status: None,
    )
    assert result["status"] == "failed"
    assert len(calls) == 1
    assert result["steps"][1]["state"] == "not_run"


def test_batch_does_not_run_authorization_gated_step_and_blocks_dependents():
    commands = normalize_commands([
        {"id": "publish", "command": "publish this video to YouTube"},
        {
            "id": "confirm",
            "command": "check project status",
            "depends_on": ["publish"],
            "project_id_from": "publish",
        },
    ])
    calls = []

    def dispatch(command, user_id, payload):
        calls.append(command)
        return {"status": "authorization_required", "executed": False}

    result = execute_commands(
        commands, user_id="owner-1", batch_id="batch-xyz123",
        dispatch=dispatch, checkpoint=lambda rows, status: None,
    )
    assert result["status"] == "blocked"
    assert len(calls) == 1
    assert result["steps"][0]["state"] == "authorization_required"
    assert result["steps"][1]["state"] == "dependency_blocked"


def test_batch_can_continue_only_when_step_explicitly_allows_it():
    commands = normalize_commands([
        {"id": "bad", "command": "perform unsupported operation", "continue_on_error": True},
        {"id": "capabilities", "command": "inspect available capabilities"},
    ])
    calls = []

    def dispatch(command, user_id, payload):
        calls.append(command)
        if command.startswith("perform"):
            return {"status": "unsupported_or_incomplete", "executed": False}
        return {"status": "completed", "executed": True, "result": {"ok": True}}

    result = execute_commands(
        commands, user_id="owner-1", batch_id="batch-xyz123",
        dispatch=dispatch, checkpoint=lambda rows, status: None,
    )
    assert result["status"] == "partial"
    assert len(calls) == 2
    assert result["steps"][0]["state"] == "failed"
    assert result["steps"][1]["state"] == "completed"
