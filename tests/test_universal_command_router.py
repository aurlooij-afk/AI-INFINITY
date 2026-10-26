from __future__ import annotations

import sys
from pathlib import Path
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


def test_primary_director_mode_command_box_uses_canonical_router_and_renders_research():
    html = Path("creator_studio_2030.html").read_text(encoding="utf-8")
    start = html.index("async function createCommand(){")
    end = html.index("\nasync function planCommand(){", start)
    handler = html[start:end]

    assert 'api("/infinity/canonical/execute"' in handler
    assert 'if(route==="research")' in handler
    assert 'if(route==="create")' in handler
    assert "authorization_required" in handler or "supported safe execution route" in handler
    assert 'onclick="createCommand()">Run command</button>' in html


def test_script_strategy_seo_analytics_publisher_and_monetization_are_distinct_routes():
    assert canonical.classify_command("Draft a script for an AI Infinity launch video")["route"] == "script"
    assert canonical.classify_command("Plan a launch strategy for AI Infinity")["route"] == "strategy"
    assert canonical.classify_command("Generate SEO metadata for my video")["route"] == "seo"
    assert canonical.classify_command("Analyze performance metrics")["route"] == "analytics"
    assert canonical.classify_command("Show my publishing options")["route"] == "publisher"
    assert canonical.classify_command("List monetization options")["route"] == "monetization"


def test_script_agent_delegates_to_existing_research_and_creative_plan_without_claiming_media_artifact(monkeypatch):
    fake_engine = SimpleNamespace(
        research_topic=lambda topic, limit=8: {
            "topic": topic, "source_count": 1,
            "sources": [{"title": "Source", "url": "https://example.org/source"}],
            "truthful": True,
        },
        creative_plan=lambda request, research, model_fn: ({
            "title": request["title"], "script": "A reviewed draft goes here.",
        }, "deterministic_fallback"),
    )
    monkeypatch.setattr(canonical, "studio", lambda: fake_engine)
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(_2700_model=None))

    result = canonical.dispatch_command("Draft a script for an AI Infinity launch video", "user-1", {})

    assert result["route"] == "script"
    assert result["status"] == "completed"
    assert result["provider"] == "deterministic_fallback"
    assert result["result"]["script"] == "A reviewed draft goes here."
    assert result["artifact_created"] is False
    assert result["review_required"] is True


def test_read_only_analytics_and_publisher_routes_do_not_publish(monkeypatch):
    fake_engine = SimpleNamespace(
        _analytics_3621=lambda user_id: {"project_count": 2, "truthful": True},
        _connections=lambda user_id: [{"provider": "youtube", "connected": False}],
    )
    monkeypatch.setattr(canonical, "studio", lambda: fake_engine)

    analytics = canonical.dispatch_command("Analyze performance metrics", "user-1", {})
    publisher = canonical.dispatch_command("Show my publishing options", "user-1", {})

    assert analytics["route"] == "analytics"
    assert analytics["result"]["project_count"] == 2
    assert publisher["route"] == "publisher"
    assert publisher["result"]["publish_executed"] is False
    assert publisher["result"]["authorization_required_for_publish"] is True


def test_primary_director_mode_displays_agent_outputs_without_faking_media_creation():
    html = Path("creator_studio_2030.html").read_text(encoding="utf-8")
    start = html.index("async function createCommand(){")
    end = html.index("\nasync function planCommand(){", start)
    handler = html[start:end]

    assert 'if(["script","strategy","marketing","seo","analytics","publisher","monetization"].indexOf(route)>=0)' in handler
    assert "no media file was created" in handler
    assert "no external action was performed" in handler


def test_unsupported_primary_deliverable_does_not_fall_back_to_ui_default_video():
    result = canonical.classify_command(
        "Create a website for AI Infinity",
        {"content_type": "video"},
    )
    assert result["route"] == "unsupported_deliverable"


def test_supported_video_about_an_unsupported_topic_still_routes_to_video_creation():
    result = canonical.classify_command(
        "Create a video about website design",
        {"content_type": "video"},
    )
    assert result["route"] == "create"


def test_cinematic_video_creation_is_not_misclassified_as_an_edit_of_selected_project():
    result = canonical.classify_command(
        "Create a professional cinematic video about accessible design",
        {"content_type": "video", "project_id": "studio_existing_project"},
    )
    assert result["route"] == "create"


def test_explicit_but_unrecognized_project_edit_fails_closed_without_render(monkeypatch):
    monkeypatch.setattr(canonical, "require_project", lambda project_id, user_id: {"project_id": project_id})

    def must_not_render(*args, **kwargs):
        raise AssertionError("unsupported edit must not trigger a render")

    monkeypatch.setattr(canonical, "apply_edit", must_not_render)
    result = canonical.dispatch_command(
        "Change the title to something shorter",
        "user-1",
        {"task_type": "edit", "project_id": "studio_project_1"},
    )
    assert result["status"] == "unsupported_or_incomplete"
    assert result["executed"] is False


def test_multilingual_research_commands_do_not_fall_back_to_selected_video_type():
    cases = [
        "AI ویڈیو ایڈیٹنگ پر تحقیق کریں",
        "د AI ویډیو ایډیټنګ په اړه څېړنه وکړئ",
        "AI ویدیو تدوین را بررسی کنید",
        "ابحث عن مصادر حول تحرير الفيديو",
    ]
    for command in cases:
        result = canonical.classify_command(command, {"content_type": "video", "language": "Urdu"})
        assert result["route"] == "research", (command, result)


def test_multilingual_creation_routes_use_the_intended_supported_content_type():
    cases = [
        ("AI Infinity کے لیے ایک ویڈیو بنائیں", "video"),
        ("AI Infinity کے لیے پوڈکاسٹ بنائیں", "podcast"),
        ("AI Infinity لپاره ویډیو جوړه کړئ", "video"),
        ("یک ویدیو درباره طراحی بسازید", "video"),
        ("أنشئ فيديو عن تصميم المحتوى", "video"),
    ]
    for command, expected_type in cases:
        result = canonical.classify_command(command, {"content_type": "article", "language": "Arabic"})
        assert result["route"] == "create", (command, result)
        assert result["content_type"] == expected_type, (command, result)


def test_multilingual_script_request_and_unsupported_website_request_are_not_video_jobs():
    script = canonical.classify_command("AI Infinity کا اسکرپٹ لکھیں", {"content_type": "video"})
    website = canonical.classify_command("أنشئ موقع ويب", {"content_type": "video"})

    assert script["route"] == "script"
    assert website["route"] == "unsupported_deliverable"


def test_multilingual_consequential_actions_require_authorization():
    cases = [
        "یہ ویڈیو شائع کریں",
        "دا ویډیو خپور کړئ",
        "این ویدیو را نشر کنید",
        "انشر الفيديو على يوتيوب",
        "أرسل رسالة إلى الجميع",
        "حذف المشروع",
    ]
    for command in cases:
        result = canonical.classify_command(command)
        assert result["route"] == "authorization_required", (command, result)
        assert result.get("action")

def test_public_api_intent_routes_to_the_existing_read_only_fabric():
    result = canonical.classify_command("Use a public API to search for climate adaptation evidence")
    assert result["route"] == "public_api"
    assert "verified" in result["reason"]


def test_public_api_command_invokes_only_a_selected_operation_id(monkeypatch):
    calls = []
    fake_fabric = SimpleNamespace(
        invoke=lambda operation_id, query=None, body=None: (
            calls.append((operation_id, query, body)) or {
                "api_id": "public-api-1", "operation_id": operation_id,
                "status_code": 200, "data": [{"title": "Climate data"}], "truthful": True,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "free_api_fabric", fake_fabric)

    result = canonical.dispatch_command(
        "Use a public API to search climate data", "user-public-api",
        {"task_type": "public_api", "op_id": "verified-get-op", "api_params": {"q": "climate data"}},
    )

    assert result["route"] == "public_api"
    assert result["status"] == "completed"
    assert result["executed"] is True
    assert result["read_only"] is True
    assert calls == [("verified-get-op", {"q": "climate data"}, None)]


def test_public_api_command_discovers_candidates_without_claiming_execution(monkeypatch):
    fake_fabric = SimpleNamespace(
        catalog=lambda query, limit, executable_only: [{
            "api_id": "public-api-1", "name": "Public Climate Data", "executable": 1,
        }],
        SYNC_STATE={"bootstrap_started": False, "bootstrap_complete": False},
        start_background_sync=lambda: False,
        status=lambda: {"bootstrap_started": False, "bootstrap_complete": False, "truthful": True},
    )
    monkeypatch.setitem(sys.modules, "free_api_fabric", fake_fabric)

    result = canonical.dispatch_command(
        "Use a public API to find climate data", "user-public-api",
        {"task_type": "public_api", "api_search": "climate"},
    )

    assert result["route"] == "public_api"
    assert result["status"] == "completed"
    assert result["executed"] is False
    assert result["candidates"][0]["api_id"] == "public-api-1"
    assert "Choose a verified API candidate" in result["next_action"]


def test_public_api_discovery_reports_warming_state_without_fake_success(monkeypatch):
    starts = []
    fake_fabric = SimpleNamespace(
        catalog=lambda query, limit, executable_only: [],
        SYNC_STATE={"bootstrap_started": False, "bootstrap_complete": False},
        start_background_sync=lambda: starts.append(True) or True,
        status=lambda: {"bootstrap_started": True, "bootstrap_complete": False, "truthful": True},
    )
    monkeypatch.setitem(sys.modules, "free_api_fabric", fake_fabric)

    result = canonical.dispatch_command(
        "Use a free public API to search for air quality", "user-public-api",
        {"task_type": "public_api", "api_search": "air quality"},
    )

    assert starts == [True]
    assert result["status"] == "discovery_started"
    assert result["discovery_status"] == "warming"
    assert result["executed"] is False


def test_failed_public_api_operation_is_truthfully_degraded_and_redacted(monkeypatch):
    fake_fabric = SimpleNamespace(
        invoke=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("secret=caller-supplied-value"))
    )
    monkeypatch.setitem(sys.modules, "free_api_fabric", fake_fabric)

    result = canonical.dispatch_command(
        "Use a public API to search", "user-public-api",
        {"task_type": "public_api", "op_id": "verified-get-op", "api_params": {"q": "private topic"}},
    )

    assert result["status"] == "degraded"
    assert result["executed"] is False
    assert result["error_type"] == "ValueError"
    assert "secret=" not in str(result)
    assert "private topic" not in str(result)

