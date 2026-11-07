from __future__ import annotations

import pytest
from threading import Barrier
from fastapi import HTTPException

from universal_command_batch import execute_commands, normalize_commands


def test_normalize_commands_requires_unique_ids_and_valid_acyclic_dependencies():
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

    forward = normalize_commands([
        {"id": "consumer", "command": "consume prepared result", "depends_on": ["producer"]},
        {"id": "producer", "command": "produce a result"},
    ])
    assert [item["id"] for item in forward] == ["consumer", "producer"]

    with pytest.raises(HTTPException):
        normalize_commands([
            {"id": "unknown", "command": "first valid step", "depends_on": ["missing"]},
        ])
    with pytest.raises(HTTPException):
        normalize_commands([
            {"id": "first", "command": "first valid step", "depends_on": ["second"]},
            {"id": "second", "command": "second valid step", "depends_on": ["first"]},
        ])


def test_normalize_commands_rejects_inline_secrets_and_unsafe_payload_fields():
    with pytest.raises(HTTPException):
        normalize_commands([{"id": "one", "command": "create this api_key=abc123 video"}])
    with pytest.raises(HTTPException):
        normalize_commands([{
            "id": "one", "command": "create a video",
            "payload": {"gemini_api_key": "do-not-store"},
        }])
    # Credential fields nested inside an options object must be rejected,
    # not silently redacted and dispatched as a partial request.
    with pytest.raises(HTTPException):
        normalize_commands([{
            "id": "nested-secret",
            "command": "create a video",
            "payload": {"options": {"provider": {"authorization": "Bearer hidden"}}},
        }])
    safe = normalize_commands([{
        "id": "nested-safe",
        "command": "create a video",
        "payload": {"options": {"quality": {"preset": "cinematic"}}},
    }])
    assert safe[0]["payload"]["options"]["quality"]["preset"] == "cinematic"


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



def test_forward_dependency_executes_in_graph_order_and_binds_project_id():
    commands = normalize_commands([
        {
            "id": "inspect",
            "command": "inspect the created project",
            "depends_on": ["create"],
            "project_id_from": "create",
        },
        {"id": "create", "command": "create a 20 second video"},
    ])
    calls = []

    def dispatch(command, user_id, payload):
        calls.append((command, dict(payload)))
        if command.startswith("create"):
            return {
                "status": "queued",
                "executed": True,
                "result": {"project_id": "studio_forward_project"},
            }
        assert payload["project_id"] == "studio_forward_project"
        return {"status": "completed", "executed": True, "result": {"ok": True}}

    result = execute_commands(
        commands,
        user_id="owner-1",
        batch_id="batch-forward-1",
        dispatch=dispatch,
        checkpoint=lambda rows, status: None,
    )

    assert [call[0] for call in calls] == [
        "create a 20 second video",
        "inspect the created project",
    ]
    assert result["steps"][0]["id"] == "inspect"
    assert result["steps"][0]["state"] == "completed"
    assert result["steps"][1]["state"] == "accepted"


def test_independent_ready_tasks_execute_concurrently_with_a_bounded_pool():
    barrier = Barrier(2)
    started = []

    def dispatch(command, user_id, payload):
        started.append(command)
        barrier.wait(timeout=3)
        return {"status": "completed", "executed": True, "result": {"command": command}}

    commands = normalize_commands([
        {"id": "research", "command": "research accessible captions"},
        {"id": "analyze", "command": "analyze caption timing"},
    ])
    result = execute_commands(
        commands,
        user_id="owner-1",
        batch_id="batch-parallel-1",
        dispatch=dispatch,
        checkpoint=lambda rows, status: None,
        max_concurrency=2,
    )

    assert len(started) == 2
    assert result["max_concurrency"] == 2
    assert result["completed_steps"] == 2
    assert all(step["state"] == "completed" for step in result["steps"])


def test_failure_blocks_only_descendants_when_failure_isolation_is_explicit():
    calls = []

    def dispatch(command, user_id, payload):
        calls.append(command)
        if command.startswith("fail"):
            return {"status": "failed", "executed": False}
        return {"status": "completed", "executed": True, "result": {"ok": True}}

    commands = normalize_commands([
        {"id": "failing", "command": "fail this optional branch", "continue_on_error": True},
        {"id": "dependent", "command": "use failed branch output", "depends_on": ["failing"]},
        {"id": "independent", "command": "complete independent analysis"},
    ])
    result = execute_commands(
        commands,
        user_id="owner-1",
        batch_id="batch-isolation-1",
        dispatch=dispatch,
        checkpoint=lambda rows, status: None,
        max_concurrency=1,
    )

    assert calls == [
        "fail this optional branch",
        "complete independent analysis",
    ]
    assert [step["state"] for step in result["steps"]] == [
        "failed",
        "dependency_blocked",
        "completed",
    ]
    assert result["status"] == "partial"


def test_concurrency_limit_is_validated():
    commands = normalize_commands([{"id": "one", "command": "inspect capabilities"}])
    with pytest.raises(HTTPException):
        execute_commands(
            commands,
            user_id="owner-1",
            batch_id="batch-invalid-concurrency",
            dispatch=lambda *args: {"status": "completed", "executed": True},
            checkpoint=lambda *args: None,
            max_concurrency=5,
        )



def test_cancel_request_prevents_pending_commands_but_does_not_claim_to_interrupt_running_work():
    commands = normalize_commands([
        {"id": "first", "command": "complete the first independent task"},
        {"id": "second", "command": "complete the second independent task"},
        {"id": "third", "command": "complete the third independent task"},
    ])
    calls = []

    def dispatch(command, user_id, payload):
        calls.append(command)
        return {"status": "completed", "executed": True, "result": {"command": command}}

    def cancellation_requested():
        # The scheduler polls between settled tasks. Once the first dispatch has
        # returned, subsequent commands must remain unstarted.
        return len(calls) >= 1

    result = execute_commands(
        commands,
        user_id="owner-1",
        batch_id="batch-cancel-1",
        dispatch=dispatch,
        checkpoint=lambda rows, status: None,
        max_concurrency=1,
        cancel_requested=cancellation_requested,
    )

    assert calls == ["complete the first independent task"]
    assert result["status"] == "partially_cancelled"
    assert result["completed_steps"] == 1
    assert result["cancelled_steps"] == 2
    assert [step["state"] for step in result["steps"]] == [
        "completed", "cancelled", "cancelled",
    ]
    assert result["cancellation_observed"] is True


def test_cancellation_callback_failure_fails_closed_before_dispatch():
    commands = normalize_commands([
        {"id": "one", "command": "perform the first task"},
    ])

    def unavailable_cancellation_store():
        raise RuntimeError("state store unavailable")

    calls = []
    result = execute_commands(
        commands,
        user_id="owner-1",
        batch_id="batch-cancel-store-1",
        dispatch=lambda *args: calls.append(args) or {
            "status": "completed", "executed": True,
        },
        checkpoint=lambda rows, status: None,
        cancel_requested=unavailable_cancellation_store,
    )

    assert calls == []
    assert result["status"] == "cancelled"
    assert result["cancelled_steps"] == 1
    assert result["cancellation_observed"] is True
