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
        'Use a public API to search climate data operation_id=verified-get-op q="climate data"', "user-public-api",
        {"task_type": "public_api"},
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

def test_public_api_command_can_resolve_api_id_from_natural_language(monkeypatch):
    inspected = []
    fake_fabric = SimpleNamespace(
        operations=lambda api_id, limit=50: inspected.append((api_id, limit)) or [{
            "op_id": "op-abc123", "method": "GET", "path": "/search", "parameters_json": '[{"name":"q","in":"query"}]'
        }],
        verify_api=lambda api_id: (_ for _ in ()).throw(AssertionError("verified existing operation should not be re-verified")),
    )
    monkeypatch.setitem(sys.modules, "free_api_fabric", fake_fabric)

    result = canonical.dispatch_command(
        "Use a public API api_id=free-a1b2c3d4 to inspect operations", "user-public-api", {}
    )

    assert result["route"] == "public_api"
    assert result["api_id"] == "free-a1b2c3d4"
    assert result["operations"][0]["op_id"] == "op-abc123"
    assert inspected == [("free-a1b2c3d4", 50)]


def test_public_api_intent_has_ui_result_handler():
    html = Path("creator_studio_2030.html").read_text(encoding="utf-8")
    start = html.index("async function createCommand(){")
    end = html.index("\nasync function planCommand(){", start)
    handler = html[start:end]

    assert 'if(route==="public_api")' in handler
    assert "No request has been executed yet" in handler
    assert "operation_id=…" in handler



def test_api_anything_command_discovers_bundled_readonly_operations(monkeypatch):
    connector = SimpleNamespace(
        list_bundled_read_only_operations=lambda site_name="", limit=100: {
            "status": "ready", "sites": ["youtube", "goodreads"],
            "operations": [{"site": "goodreads", "operation": "searchBooks", "read_only": True, "parameters": ["q"]}],
            "executed": False, "truthful": True,
        }
    )
    monkeypatch.setitem(sys.modules, "api_anything_connector", connector)
    monkeypatch.setitem(sys.modules, "free_api_fabric", SimpleNamespace(
        catalog=lambda **kwargs: (_ for _ in ()).throw(AssertionError("API Anything must use its dynamic bundled catalog"))
    ))

    result = canonical.dispatch_command("Show API Anything read-only operations", "user-1", {})

    assert result["route"] == "public_api"
    assert result["provider"] == "api-anything"
    assert result["executed"] is False
    assert result["operations"][0]["operation"] == "searchBooks"


def test_api_anything_command_executes_selected_readonly_operation_without_public_api_fallback(monkeypatch):
    calls = []
    connector = SimpleNamespace(
        execute_bundled_read_only=lambda site, operation, params: (
            calls.append((site, operation, params)) or {
                "status": "completed", "executed": True, "read_only": True,
                "site": site, "operation": operation, "result": [{"title": "Piranesi"}],
                "transport_tier": 1, "truthful": True,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "api_anything_connector", connector)
    monkeypatch.setitem(sys.modules, "free_api_fabric", SimpleNamespace(
        invoke=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not fall through to free API fabric"))
    ))

    result = canonical.dispatch_command(
        "Use API Anything site=goodreads operation=searchBooks q=Piranesi", "user-1", {}
    )

    assert result["status"] == "completed"
    assert result["provider"] == "api-anything"
    assert result["executed"] is True
    assert result["result"]["transport_tier"] == 1
    assert calls == [("goodreads", "searchBooks", {"q": "Piranesi"})]


def test_api_anything_explicit_payload_supports_parameters_and_does_not_authorize_writes(monkeypatch):
    calls = []
    connector = SimpleNamespace(
        execute_bundled_read_only=lambda site, operation, params: (
            calls.append((site, operation, params)) or {
                "status": "operation_not_verified_read_only", "executed": False, "truthful": True,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "api_anything_connector", connector)

    result = canonical.dispatch_command(
        "Call API Anything operation", "user-1",
        {"api_anything_site": "linkedin", "api_anything_operation": "sendMessage",
         "api_anything_params": {"recipient": "someone", "message": "hello"}},
    )

    assert result["status"] == "degraded"
    assert result["executed"] is False
    assert calls == [("linkedin", "sendMessage", {"recipient": "someone", "message": "hello"})]
    assert canonical.classify_command("Send a message through API Anything")["route"] == "authorization_required"

def test_explicit_site_operation_syntax_routes_to_verified_public_api_without_provider_name():
    result = canonical.classify_command(
        "Run site=hacker-news operation=search query=sqlite"
    )

    assert result["route"] == "public_api"
    assert "verified" in result["reason"].lower()


def test_api_anything_write_requires_explicit_allowance_and_returns_confirmation_state(monkeypatch):
    import api_anything_connector as connector

    calls = []
    connector_fake = SimpleNamespace(
        execute_bundled_read_only=lambda *args: {
            "status": "operation_not_verified_read_only", "executed": False, "truthful": True,
        },
        execute_bundled_operation=lambda site, operation, params, allow_writes=False, dry_run=False: (
            calls.append((site, operation, params, allow_writes, dry_run)) or {
                "status": "write_confirmation_required", "executed": False,
                "read_only": False, "write": True, "truthful": True,
            }
        ),
    )
    monkeypatch.setitem(sys.modules, "api_anything_connector", connector_fake)

    result = canonical.dispatch_command(
        "Run site=demo-site operation=sendMessage recipient=test-user message=hello",
        "user-1", {},
    )

    assert result["route"] == "public_api"
    assert result["status"] == "write_confirmation_required"
    assert result["executed"] is False
    assert result["write"] is True
    assert calls == [("demo-site", "sendMessage", {"recipient": "test-user", "message": "hello"}, False, False)]


def test_api_anything_explicit_allow_writes_reaches_write_executor(monkeypatch):
    calls = []
    connector_fake = SimpleNamespace(
        execute_bundled_operation=lambda site, operation, params, allow_writes=False, dry_run=False: (
            calls.append((site, operation, params, allow_writes, dry_run)) or {
                "status": "write_accepted_not_independently_verified",
                "executed": True, "read_only": False, "write": True, "truthful": True,
            }
        ),
        execute_bundled_read_only=lambda *args: (_ for _ in ()).throw(
            AssertionError("explicit write must use the generalized operation executor")
        ),
    )
    monkeypatch.setitem(sys.modules, "api_anything_connector", connector_fake)

    result = canonical.dispatch_command(
        "Run site=demo-site operation=sendMessage recipient=test-user message=hello --allow-writes",
        "user-1", {},
    )

    assert result["status"] == "write_accepted_not_independently_verified"
    assert result["executed"] is True
    assert result["write"] is True
    assert calls == [("demo-site", "sendMessage", {"recipient": "test-user", "message": "hello"}, True, False)]


def test_api_anything_can_list_read_and_write_capabilities_separately(monkeypatch):
    captured = []
    connector_fake = SimpleNamespace(
        list_bundled_operations=lambda site_name="", limit=100, include_writes=True: (
            captured.append((site_name, limit, include_writes)) or {
                "status": "ready", "sites": ["demo-site"], "operations": [
                    {"site": "demo-site", "operation": "search", "read_only": True},
                    {"site": "demo-site", "operation": "sendMessage", "read_only": False, "write": True},
                ], "executed": False, "truthful": True,
            }
        ),
        list_bundled_read_only_operations=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("write catalog was requested explicitly")
        ),
    )
    monkeypatch.setitem(sys.modules, "api_anything_connector", connector_fake)

    result = canonical.dispatch_command("Show API Anything write operations", "user-1", {})

    assert result["status"] == "completed"
    assert result["executed"] is False
    assert any(operation.get("write") for operation in result["operations"])
    assert captured == [("", 100, True)]


def test_api_anything_user_profile_operation_dispatches_tier_and_user_id(monkeypatch):
    calls = []
    connector_fake = SimpleNamespace(
        execute_api_anything_operation=lambda site, operation, params, **kwargs: (
            calls.append((site, operation, params, kwargs)) or {
                "status": "completed", "executed": True, "read_only": True,
                "write_capable": False, "transport_tier": 2, "truthful": True,
            }
        ),
    )
    monkeypatch.setitem(sys.modules, "api_anything_connector", connector_fake)

    result = canonical.dispatch_command(
        "Use API Anything site=reader-site operation=getRecord recordId=rec-42 max_tier=2",
        "user-research-7", {},
    )

    assert result["status"] == "completed"
    assert result["executed"] is True
    assert result["read_only"] is True
    assert calls == [(
        "reader-site", "getRecord", {"recordId": "rec-42"},
        {
            "user_id": "user-research-7", "allow_writes": False,
            "max_tier": 2, "dry_run": False, "idempotency_key": "", "again": False,
        },
    )]


def test_api_anything_learn_command_passes_two_examples_to_profiled_learner(monkeypatch):
    calls = []
    connector_fake = SimpleNamespace(
        learn_api_anything_operation=lambda site, operation, trigger, example, example2, **kwargs: (
            calls.append((site, operation, trigger, example, example2, kwargs)) or {
                "status": "learned", "executed": False, "truthful": True,
            }
        ),
    )
    monkeypatch.setitem(sys.modules, "api_anything_connector", connector_fake)

    result = canonical.dispatch_command(
        "API Anything learn site=reader-site operation=search trigger_url=https://example.com/search?q={q}",
        "user-learn-3",
        {
            "api_anything_action": "learn",
            "api_anything_example": {"q": "cats"},
            "api_anything_example2": {"q": "dogs"},
        },
    )

    assert result["status"] == "learned"
    assert result["executed"] is False
    assert calls == [(
        "reader-site", "search", "https://example.com/search?q={q}",
        {"q": "cats"}, {"q": "dogs"},
        {"user_id": "user-learn-3", "write": False, "match": {}},
    )]


def test_api_anything_write_uses_explicit_natural_language_intent_without_flag(monkeypatch):
    calls = []
    connector_fake = SimpleNamespace(
        execute_api_anything_operation=lambda site, operation, params, **kwargs: (
            calls.append((site, operation, params, kwargs)) or {
                "status": "dry_run_required", "executed": False,
                "write_capable": True, "truthful": True,
            }
        ),
    )
    monkeypatch.setitem(sys.modules, "api_anything_connector", connector_fake)
    result = canonical.dispatch_command(
        "Send message using API Anything site=social-site operation=sendMessage recipient=reviewer body=hello",
        "user-write-4", {},
    )
    assert result["route"] == "public_api"
    assert result["executed"] is False
    assert result["status"] == "dry_run_required"
    assert result["write"] is True
    assert calls == [(
        "social-site", "sendMessage", {"recipient": "reviewer", "body": "hello"},
        {
            "user_id": "user-write-4", "allow_writes": True, "max_tier": 3,
            "dry_run": False, "idempotency_key": "", "again": False, "auto_preflight": True,
        },
    )]

