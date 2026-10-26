from __future__ import annotations

import sys
from types import SimpleNamespace

import ai_infinity_canonical as canonical


def test_research_only_command_does_not_default_to_video_creation():
    result = canonical.classify_command("Research reliable sources about AI video editing")

    assert result["route"] == "research"


def test_research_and_creation_command_uses_existing_creator_pipeline():
    result = canonical.classify_command(
        "Research competitors and create a 60-second video about AI video editing"
    )

    assert result["route"] == "create"


def test_supported_content_formats_route_to_creation():
    assert canonical.classify_command("Create a podcast about accessible design")["route"] == "create"
    assert canonical.classify_command("Write an article about open source video tools")["route"] == "create"
    assert canonical.classify_command("Create a social campaign for a new tutorial")["route"] == "create"


def test_unknown_task_does_not_create_a_default_video():
    result = canonical.classify_command("Calculate 123 multiplied by 456")

    assert result["route"] == "unsupported"


def test_create_without_supported_deliverable_requests_explicit_output_type():
    result = canonical.classify_command("Create something useful about technology")

    assert result["route"] == "deliverable_required"


def test_external_side_effect_cannot_be_overridden_by_task_type_hint():
    result = canonical.classify_command(
        "Publish this video to YouTube",
        {"task_type": "create", "content_type": "video"},
    )

    assert result["route"] == "authorization_required"
    assert result["action"] == "publish"


def test_edit_and_project_status_require_an_existing_project():
    edit = canonical.classify_command("Trim the first 5 seconds")
    status = canonical.classify_command("Check project status")

    assert edit["route"] == "project_id_required"
    assert status["route"] == "project_id_required"


def test_project_edit_routes_only_to_the_existing_edit_engine(monkeypatch):
    monkeypatch.setattr(canonical, "require_project", lambda project_id, user_id: {"project_id": project_id})
    monkeypatch.setattr(canonical, "apply_edit", lambda project_id, user_id, command: {
        "status": "completed", "project_id": project_id, "operation": {"speed": 1.15}
    })

    result = canonical.dispatch_command("Make this video faster", "user-1", {
        "project_id": "studio_project_1",
    })

    assert result["route"] == "edit"
    assert result["status"] == "completed"
    assert result["result"]["project_id"] == "studio_project_1"


def test_research_route_calls_existing_research_adapter_and_keeps_review_truth(monkeypatch):
    fake = SimpleNamespace(research=lambda topic, limit=8: {
        "topic": topic,
        "source_count": 1,
        "sources": [{"title": "Example source", "url": "https://example.org/research"}],
        "provider": "existing+api-anything",
        "truthful": True,
    })
    monkeypatch.setitem(sys.modules, "production_intelligence", fake)

    result = canonical.dispatch_command("Research sources about AI video editing", "user-1", {"limit": 4})

    assert result["route"] == "research"
    assert result["status"] == "completed"
    assert result["source_count"] == 1
    assert result["quality_state"] == "REVIEW_REQUIRED"
    assert result["claims_independently_verified"] is False
    assert result["result"]["topic"] == "sources about AI video editing"


def test_create_route_delegates_to_existing_creator_engine(monkeypatch):
    calls = []
    monkeypatch.setattr(canonical, "create_from_command", lambda command, user_id, raw: (
        calls.append((command, user_id, raw)) or {"status": "queued", "project_id": "studio_project_2"}
    ))

    result = canonical.dispatch_command(
        "Create a 60-second video about climate resilience",
        "user-2",
        {"content_type": "video", "duration": 60},
    )

    assert result["route"] == "create"
    assert result["status"] == "queued"
    assert calls and calls[0][1] == "user-2"


def test_unsupported_and_consequential_tasks_do_not_start_work(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("no execution path should be called")

    monkeypatch.setattr(canonical, "create_from_command", must_not_run)
    unsupported = canonical.dispatch_command("Calculate 3 plus 5", "user-1", {})
    protected = canonical.dispatch_command("Send an email to everyone", "user-1", {})

    assert unsupported["executed"] is False
    assert protected["status"] == "authorization_required"
    assert protected["executed"] is False
