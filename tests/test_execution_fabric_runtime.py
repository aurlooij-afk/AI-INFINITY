from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import ai_infinity_canonical as canonical
from ai_infinity.execution_fabric_runtime import register


def test_execute_plan_dispatches_real_canonical_commands_in_dependency_order(monkeypatch):
    calls = []
    monkeypatch.setattr(canonical, "current_user", lambda request, response: "user-test")

    def dispatch(command, user_id, payload):
        calls.append((command, user_id, payload))
        return {
            "status": "completed",
            "route": "research" if command.startswith("Research") else "script",
            "executed": True,
            "truthful": True,
            "result": {"command": command},
        }

    monkeypatch.setattr(canonical, "dispatch_command", dispatch)
    app = FastAPI()
    register(app)
    client = TestClient(app)
    response = client.post("/infinity/canonical/execute-plan", json={
        "request_id": "integration-1",
        "steps": [
            {"step_id": "draft", "command": "Draft a script about safe AI", "depends_on": ["research"]},
            {"step_id": "research", "command": "Research safe AI deployment"},
        ],
    })

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "succeeded"
    assert result["summary"]["succeeded"] == 2
    assert [item[0] for item in calls] == [
        "Research safe AI deployment",
        "Draft a script about safe AI",
    ]
    assert all(item[1] == "user-test" for item in calls)


def test_execute_plan_blocks_dependants_when_canonical_dispatch_is_not_verified(monkeypatch):
    calls = []
    monkeypatch.setattr(canonical, "current_user", lambda request, response: "user-test")

    def dispatch(command, user_id, payload):
        calls.append(command)
        if command.startswith("Research"):
            return {
                "status": "degraded",
                "route": "research",
                "executed": True,
                "truthful": True,
                "source_count": 0,
            }
        return {"status": "completed", "route": "script", "executed": True, "truthful": True}

    monkeypatch.setattr(canonical, "dispatch_command", dispatch)
    app = FastAPI()
    register(app)
    response = TestClient(app).post("/infinity/canonical/execute-plan", json={
        "request_id": "integration-2",
        "steps": [
            {"step_id": "research", "command": "Research a topic"},
            {"step_id": "draft", "command": "Draft a script", "depends_on": ["research"]},
        ],
    })

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "failed"
    assert result["steps"]["research"]["reason"] == "verification_failed"
    assert result["steps"]["draft"]["status"] == "blocked"
    assert calls == ["Research a topic"]


def test_execute_plan_rejects_malformed_or_oversized_requests(monkeypatch):
    monkeypatch.setattr(canonical, "current_user", lambda request, response: "user-test")
    app = FastAPI()
    register(app)
    client = TestClient(app)

    missing_id = client.post("/infinity/canonical/execute-plan", json={
        "steps": [{"step_id": "one", "command": "Check capabilities"}],
    })
    too_many = client.post("/infinity/canonical/execute-plan", json={
        "request_id": "too-many",
        "steps": [{"step_id": str(i), "command": "Check capabilities"} for i in range(21)],
    })

    assert missing_id.status_code == 422
    assert too_many.status_code == 422
