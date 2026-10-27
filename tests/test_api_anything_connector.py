from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import api_anything_connector as connector



@pytest.fixture(autouse=True)
def clear_connector_call_limits():
    connector._LAST_CALL_AT.clear()
    connector._GENERIC_COOLDOWNS.clear()
    yield
    connector._LAST_CALL_AT.clear()
    connector._GENERIC_COOLDOWNS.clear()


def _stub_runner(*, site_name="youtube", operation_name="search", site_source="bundled", read_only=True, result=None, error=False):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        args = command[1:]
        if args == ["sites"]:
            payload = [{"name": site_name, "source": site_source}]
        elif args == ["ops", site_name]:
            payload = {"operations": [{"name": operation_name, "readOnly": read_only}]}
        elif args[:3] == ["call", site_name, operation_name]:
            if error:
                return subprocess.CompletedProcess(command, 2, stdout="", stderr="secret=do-not-return")
            if result is not None:
                payload = result
            elif site_name == "hacker-news":
                payload = {
                    "ok": True,
                    "class": "ok",
                    "tier": 1,
                    "data": [{
                        "objectID": "123456",
                        "title": "A useful technology discussion",
                        "url": "https://example.org/story",
                        "author": "sample-user",
                        "points": 42,
                        "num_comments": 8,
                    }],
                }
            else:
                payload = {
                    "ok": True,
                    "class": "ok",
                    "tier": 1,
                    "data": [
                        {"id": "abcdefghijk", "title": "A useful video", "channel": "Channel", "views": "1200 views"},
                        {"id": "bad id", "title": "Invalid identifier must be discarded"},
                        {"id": "lmnopqrstuv", "title": "Second useful video"},
                    ],
                }
        else:
            raise AssertionError(f"unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    return runner, calls


def test_youtube_search_uses_bundled_readonly_operation_and_tier_one(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _stub_runner()
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.youtube_search("  climate change   adaptation  ", limit=1)

    assert result["status"] == "ready"
    assert result["query"] == "climate change adaptation"
    assert result["transport_tier"] == 1
    assert result["items"] == [{
        "id": "abcdefghijk",
        "title": "A useful video",
        "channel": "Channel",
        "views": "1200 views",
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "source": "API Anything / YouTube",
    }]
    assert len(calls) == 3
    assert calls[0][1:] == ["sites"]
    assert calls[1][1:] == ["ops", "youtube"]
    assert calls[2][1:4] == ["call", "youtube", "search"]
    assert "--max-tier" in calls[2] and calls[2][calls[2].index("--max-tier") + 1] == "1"
    assert json.loads(calls[2][calls[2].index("--json") + 1]) == {"q": "climate change adaptation"}


def test_youtube_search_does_not_call_unbundled_or_user_overridden_specs(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _stub_runner(site_source="user")
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.youtube_search("AI video creation")

    assert result["status"] == "untrusted_site_spec"
    assert result["items"] == []
    assert len(calls) == 1


def test_youtube_search_refuses_operation_that_is_not_explicitly_read_only(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _stub_runner(read_only=False)
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.youtube_search("AI video creation")

    assert result["status"] == "operation_not_verified_read_only"
    assert result["items"] == []
    assert len(calls) == 2


def test_youtube_search_fails_closed_on_cli_failure_without_exposing_stderr(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _stub_runner(error=True)
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.youtube_search("AI video creation")

    assert result["status"] == "command_failed"
    assert result["items"] == []
    assert "secret" not in json.dumps(result)
    assert len(calls) == 3


def test_youtube_search_handles_missing_cli_and_blank_query(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: None)
    assert connector.youtube_search("AI Infinity")["status"] == "not_configured"
    assert connector.youtube_search("   ")["status"] == "invalid_input"
    assert connector.status()["available"] is False


def test_youtube_search_handles_timeout_without_retrying_side_effects(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    calls = 0

    def timeout_runner(command, **kwargs):
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired(command, timeout=kwargs["timeout"])

    monkeypatch.setattr(connector.subprocess, "run", timeout_runner)
    result = connector.youtube_search("AI Infinity")
    assert result["status"] == "timeout"
    assert result["items"] == []
    assert calls == 1


def test_production_research_uses_api_anything_results_without_claiming_more_than_returned(monkeypatch):
    import production_intelligence

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.setattr(
        production_intelligence.api_anything_connector,
        "search_candidates",
        lambda query, limit=10: {
            "status": "ready",
            "source_count": 1,
            "sources": [{
                "id": "abcdefghijk",
                "title": "Research example",
                "url": "https://www.youtube.com/watch?v=abcdefghijk",
                "channel": "Example channel",
                "views": "100 views",
                "source": "API Anything / YouTube",
            }],
            "operations": {"youtube.search": "ready", "hacker-news.search": "empty"},
            "truthful": True,
        },
    )

    result = production_intelligence.research("AI video editing", limit=5)

    assert result["provider"] == "api-anything-youtube"
    assert result["source_count"] == 1
    assert result["api_anything"] == {
        "status": "ready",
        "items_added": 1,
        "operations": {"youtube.search": "ready", "hacker-news.search": "empty"},
        "truthful": True,
    }
    assert result["sources"][0]["source"] == "API Anything / YouTube"
    assert result["sources"][0]["url"] == "https://www.youtube.com/watch?v=abcdefghijk"


def test_installed_adapter_enriches_the_existing_creator_research_function(monkeypatch):
    from types import SimpleNamespace
    import production_intelligence

    base_result = {
        "topic": "AI video editing",
        "sources": [{
            "title": "AI video editing basics",
            "url": "https://example.com/ai-video",
            "source": "Example",
            "summary": "AI video editing techniques",
        }],
        "source_count": 1,
        "provider_count": 1,
        "providers": {"Example": {"status": "ready", "count": 1, "truthful": True}},
        "degraded": False,
        "status": "READY",
        "truthful": True,
        "relevance_policy": {
            "retrieved_sources": 1,
            "accepted_sources": 1,
            "rejected_irrelevant_sources": 0,
        },
    }
    creator = SimpleNamespace(
        research_topic=lambda topic, limit=10: dict(base_result, sources=list(base_result["sources"]), providers=dict(base_result["providers"])),
        _research_source_relevant=lambda topic, row: True,
        utc_iso=lambda: "2026-10-27T00:00:00Z",
    )
    monkeypatch.setattr(production_intelligence, "studio", lambda: creator)
    monkeypatch.setattr(
        production_intelligence.api_anything_connector,
        "search_candidates",
        lambda topic, limit=10: {
            "status": "ready",
            "sources": [
                {
                    "id": "abcdefghijk", "source": "API Anything / YouTube",
                    "title": "AI video editing tutorial",
                    "url": "https://www.youtube.com/watch?v=abcdefghijk",
                    "channel": "Creator", "views": "1200 views",
                },
                {
                    "id": "invalid id", "source": "API Anything / YouTube",
                    "title": "Invalid row", "url": "https://www.youtube.com/watch?v=invalid%20id",
                },
                {
                    "id": "123", "source": "API Anything / Hacker News",
                    "title": "AI video editing discussion", "url": "https://example.org/story",
                    "author": "sample", "points": "4", "comments": "2",
                },
                {
                    "id": "35959740", "source": "API Anything / Goodreads",
                    "title": "Piranesi", "url": "https://www.goodreads.com/book/show/35959740",
                    "author": "Susanna Clarke", "rating": "4.3", "ratings_count": "100000",
                },
                {
                    "id": "interstellar", "source": "API Anything / Rotten Tomatoes",
                    "title": "Interstellar", "url": "https://www.rottentomatoes.com/m/interstellar",
                    "year": "2014", "tomatometer": "91%", "cast": "Matthew McConaughey",
                },
                {
                    "id": "B012345678", "source": "API Anything / Amazon",
                    "title": "USB microphone", "url": "https://www.amazon.com/dp/B012345678",
                    "price": "$29.99",
                },
            ],
            "operations": {
                "youtube.search": "ready", "hacker-news.search": "ready",
                "goodreads.searchBooks": "ready", "rottentomatoes.searchMovies": "ready", "amazon.search": "ready",
            },
            "truthful": True,
        },
    )

    installed = production_intelligence.install_creator_research_connector()
    result = creator.research_topic("AI video editing", limit=5)

    assert installed["status"] == "installed"
    assert result["source_count"] == 6
    assert result["provider_count"] == 6
    for source_name in (
        "API Anything / YouTube", "API Anything / Hacker News", "API Anything / Goodreads",
        "API Anything / Rotten Tomatoes", "API Anything / Amazon",
    ):
        assert [s["source"] for s in result["sources"]].count(source_name) == 1
        assert result["providers"][source_name]["status"] == "ready"
    assert result["research_connector"]["items_added"] == 5


def test_installed_adapter_respects_explicit_disable_external_research_policy(monkeypatch):
    from types import SimpleNamespace
    import production_intelligence

    result_when_disabled = {
        "topic": "AI video editing",
        "sources": [],
        "source_count": 0,
        "provider_count": 0,
        "providers": {
            "Wikipedia": {"status": "disabled_by_policy"},
            "DuckDuckGo": {"status": "disabled_by_policy"},
        },
        "degraded": True,
        "status": "DEGRADED",
        "degraded_reason": "External research access is disabled by runtime policy.",
        "truthful": True,
    }
    creator = SimpleNamespace(research_topic=lambda topic, limit=10: result_when_disabled)
    monkeypatch.setattr(production_intelligence, "studio", lambda: creator)

    def must_not_run(*args, **kwargs):
        raise AssertionError("API Anything must not run when external research is disabled")

    monkeypatch.setattr(production_intelligence.api_anything_connector, "search_candidates", must_not_run)
    production_intelligence.install_creator_research_connector()
    assert creator.research_topic("AI video editing") is result_when_disabled


def test_production_research_route_respects_external_provider_disable_policy(monkeypatch):
    from types import SimpleNamespace
    import production_intelligence

    monkeypatch.setenv("TAVILY_API_KEY", "must-not-be-used")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "must-not-be-used")
    creator = SimpleNamespace(
        _external_providers_disabled=lambda: True,
        research_topic=lambda topic, limit=10: {
            "topic": topic,
            "sources": [],
            "source_count": 0,
            "providers": {"Wikipedia": {"status": "disabled_by_policy", "count": 0}},
            "degraded": True,
            "status": "DEGRADED",
            "degraded_reason": "External research access is disabled by runtime policy.",
            "truthful": True,
        },
    )
    monkeypatch.setattr(production_intelligence, "studio", lambda: creator)

    def must_not_run(*args, **kwargs):
        raise AssertionError("Remote provider call must be blocked by policy")

    monkeypatch.setattr(production_intelligence.api_anything_connector, "search_candidates", must_not_run)
    monkeypatch.setattr(production_intelligence.requests, "post", must_not_run)
    monkeypatch.setattr(production_intelligence.requests, "get", must_not_run)

    result = production_intelligence.research("AI video editing", limit=5)

    assert result["provider"] == "disabled_by_policy"
    assert result["api_anything"]["status"] == "disabled_by_policy"
    assert result["source_count"] == 0
    assert result["sources"] == []


def test_invalid_timeout_configuration_fails_closed_without_running_cli(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    monkeypatch.setenv("AI_INFINITY_API_ANYTHING_TIMEOUT_SECONDS", "not-an-integer")

    def must_not_run(*args, **kwargs):
        raise AssertionError("invalid configuration must be rejected before execution")

    monkeypatch.setattr(connector.subprocess, "run", must_not_run)
    result = connector.youtube_search("AI Infinity")

    assert result["status"] == "invalid_configuration"
    assert result["items"] == []
    assert result["truthful"] is True


def test_youtube_search_applies_shared_per_site_rate_limit(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _stub_runner()
    monkeypatch.setattr(connector.subprocess, "run", runner)

    first = connector.youtube_search("AI video editing")
    second = connector.youtube_search("space exploration")

    assert first["status"] == "ready"
    assert second["status"] == "rate_limited"
    # The second request is rejected before any additional CLI/network operation.
    assert len(calls) == 3


def test_hacker_news_search_uses_bundled_readonly_operation_and_validates_https_results(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _stub_runner(site_name="hacker-news")
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.hacker_news_search(" AI video editing ", limit=3)

    assert result["status"] == "ready"
    assert result["site"] == "hacker-news"
    assert result["operation"] == "search"
    assert result["transport_tier"] == 1
    assert result["items"] == [{
        "id": "123456",
        "title": "A useful technology discussion",
        "url": "https://example.org/story",
        "author": "sample-user",
        "points": "42",
        "comments": "8",
        "source": "API Anything / Hacker News",
    }]
    assert calls[0][1:] == ["sites"]
    assert calls[1][1:] == ["ops", "hacker-news"]
    assert calls[2][1:4] == ["call", "hacker-news", "search"]
    assert json.loads(calls[2][calls[2].index("--json") + 1]) == {"query": "AI video editing"}
    assert calls[2][calls[2].index("--max-tier") + 1] == "1"


def test_hacker_news_search_rejects_non_https_result_urls(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    malicious = {
        "ok": True, "class": "ok", "tier": 1,
        "data": [
            {"objectID": "1", "title": "Plain HTTP link", "url": "http://example.org/story"},
            {"objectID": "2", "title": "Localhost link", "url": "https://localhost/private"},
            {"objectID": "3", "title": "Private IP link", "url": "https://127.0.0.1/private"},
            {"objectID": "4", "title": "Nonstandard port link", "url": "https://example.org:8443/private"},
            {"objectID": "5", "title": "Safe HTTPS link", "url": "https://example.org/safe"},
        ],
    }
    runner, calls = _stub_runner(site_name="hacker-news", result=malicious)
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.hacker_news_search("security")

    assert result["status"] == "ready"
    assert [x["url"] for x in result["items"]] == ["https://example.org/safe"]


def test_search_candidates_reports_each_allowlisted_operation_truthfully(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    seen = []

    def fake(site_name, operation_name, query_parameter, query, limit, result_kind):
        seen.append(site_name)
        if site_name == "youtube":
            return {"status": "ready", "items": [{"title": "video", "source": "API Anything / YouTube"}], "truthful": True}
        return {"status": "empty", "items": [], "truthful": True}

    monkeypatch.setattr(connector, "_bundled_search", fake)
    result = connector.search_candidates("AI video editing", limit=3)

    assert result["status"] == "ready"
    assert result["source_count"] == 1
    assert result["operations"] == {"youtube.search": "ready", "hacker-news.search": "empty"}
    assert seen == ["youtube", "hacker-news"]


@pytest.mark.parametrize(
    ("site", "operation", "query_parameter", "result_kind", "payload", "expected_source", "expected_url"),
    [
        (
            "goodreads", "searchBooks", "q", "goodreads",
            {"bookId": "35959740", "title": "Piranesi", "author": "Susanna Clarke", "avgRating": "4.3", "ratingsCount": "100000"},
            "API Anything / Goodreads", "https://www.goodreads.com/book/show/35959740",
        ),
        (
            "rottentomatoes", "searchMovies", "q", "rottentomatoes",
            {"url": "/m/interstellar", "title": "Interstellar", "year": "2014", "tomatometer": "91%", "cast": "Matthew McConaughey"},
            "API Anything / Rotten Tomatoes", "https://www.rottentomatoes.com/m/interstellar",
        ),
        (
            "amazon", "search", "k", "amazon",
            {"asin": "B012345678", "title": "USB microphone", "price": "$29.99"},
            "API Anything / Amazon", "https://www.amazon.com/dp/B012345678",
        ),
    ],
)
def test_additional_public_sources_normalize_to_canonical_provenance_urls(
    monkeypatch, site, operation, query_parameter, result_kind, payload, expected_source, expected_url
):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _stub_runner(
        site_name=site, operation_name=operation,
        result={"ok": True, "class": "ok", "tier": 1, "data": [payload]},
    )
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector._rate_limited_search(
        f"{site}.{operation}", "Piranesi microphone Celeste", 5
    )

    assert result["status"] == "ready"
    assert result["items"][0]["source"] == expected_source
    assert result["items"][0]["url"] == expected_url
    assert result["items"][0]["title"] == payload["title"]
    assert result["transport_tier"] == 1
    call = calls[-1]
    assert call[1:4] == ["call", site, operation]
    assert json.loads(call[call.index("--json") + 1]) == {query_parameter: "Piranesi microphone Celeste"}
    assert call[call.index("--max-tier") + 1] == "1"


def test_contextual_search_routes_only_relevant_specialist_sites(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    seen = []

    def fake_bundled(site_name, operation_name, query_parameter, query, limit, result_kind):
        seen.append((site_name, operation_name))
        return {"status": "empty", "items": [], "truthful": True}

    monkeypatch.setattr(connector, "_bundled_search", fake_bundled)

    books = connector.search_candidates("Research books and novels about artificial intelligence")
    assert books["operations"].keys() == {"youtube.search", "hacker-news.search", "goodreads.searchBooks"}
    assert [site for site, _ in seen] == ["youtube", "hacker-news", "goodreads"]

    seen.clear()
    connector._LAST_CALL_AT.clear()  # Keep this routing test independent of the deliberate cross-request cooldown.
    movies = connector.search_candidates("Compare movie critic scores and cinematic references")
    assert movies["operations"].keys() == {"youtube.search", "hacker-news.search", "rottentomatoes.searchMovies"}
    assert [site for site, _ in seen] == ["youtube", "hacker-news", "rottentomatoes"]

    seen.clear()
    connector._LAST_CALL_AT.clear()
    products = connector.search_candidates("Research laptop products and price comparison")
    assert products["operations"].keys() == {"youtube.search", "hacker-news.search", "amazon.search"}
    assert [site for site, _ in seen] == ["youtube", "hacker-news", "amazon"]


def test_additional_sources_reject_invalid_ids_before_constructing_links(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    cases = [
        ("goodreads", "searchBooks", "goodreads", {"bookId": "../private", "title": "Bad book"}),
        ("rottentomatoes", "searchMovies", "rottentomatoes", {"url": "/m/../../private", "title": "Bad movie"}),
        ("amazon", "search", "amazon", {"asin": "https://evil.example", "title": "Bad product"}),
    ]
    for site, operation, result_kind, row in cases:
        runner, _ = _stub_runner(
            site_name=site, operation_name=operation,
            result={"ok": True, "class": "ok", "tier": 1, "data": [row]},
        )
        monkeypatch.setattr(connector.subprocess, "run", runner)
        query_parameter = {"goodreads": "q", "rottentomatoes": "q", "amazon": "k"}[site]
        result = connector._rate_limited_search(f"{site}.{operation}", "test query", 5)
        assert result["status"] == "empty"
        assert result["items"] == []


def test_connector_enforces_output_limit_while_streaming_subprocess_stdout(monkeypatch, tmp_path):
    monkeypatch.setattr(connector, "MAX_OUTPUT_CHARS", 64)
    payload, error = connector._run_json(
        connector._ORIGINAL_SUBPROCESS_RUN,
        sys.executable,
        ["-c", "import sys; sys.stdout.write('x' * 1000000)"],
        cwd=str(tmp_path),
        env={"PATH": os.environ.get("PATH", "")},
        timeout=5,
    )

    assert payload is None
    assert error == "output_too_large"


def _generic_runner(*, read_only=True, parameters=None, result=None, site_source="bundled", min_tier=1):
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        args = command[1:]
        if args == ["sites"]:
            payload = [{"name": "demo-site", "source": site_source}]
        elif args == ["ops", "demo-site"]:
            operation = {
                "name": "search", "readOnly": read_only,
                "params": parameters if parameters is not None else ["q:string"],
                "summary": "Search public records",
            }
            if min_tier > 1:
                operation["minTier"] = min_tier
            payload = {"operations": [operation]}
        elif args[:3] == ["call", "demo-site", "search"]:
            payload = result if result is not None else {
                "ok": True, "site": "demo-site", "op": "search", "tier": 1,
                "data": [{"title": "Public record", "url": "https://example.org/item", "api_key": "must-not-leak"}],
            }
        else:
            raise AssertionError(f"unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    return runner, calls


def test_generic_catalog_lists_all_bundled_readonly_operations_without_executing(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _generic_runner()
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.list_bundled_read_only_operations()

    assert result["status"] == "ready"
    assert result["sites"] == ["demo-site"]
    assert result["operations"] == [{
        "site": "demo-site",
        "operation": "search",
        "read_only": True,
        "parameters": ["q"],
        "description": "Search public records",
        "notes": "",
        "transport": "tier_1_plain_http_only",
    }]
    assert result["executed"] is False
    assert len(calls) == 2
    assert all(kwargs["env"].get("OPENAI_API_KEY") is None for _, kwargs in calls)


def test_generic_readonly_executor_calls_selected_declared_parameter_and_redacts_secrets(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _generic_runner()
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.execute_bundled_read_only("demo-site", "search", {"q": "climate data"})

    assert result["status"] == "completed"
    assert result["executed"] is True
    assert result["read_only"] is True
    assert result["transport_tier"] == 1
    assert result["result"][0]["title"] == "Public record"
    assert result["result"][0]["api_key"] == "[redacted]"
    call_args = calls[-1][0][1:]
    assert call_args[:3] == ["call", "demo-site", "search"]
    assert json.loads(call_args[call_args.index("--json") + 1]) == {"q": "climate data"}
    assert call_args[call_args.index("--max-tier") + 1] == "1"
    # Application credentials are never inherited by the isolated API Anything child.
    assert all("OPENAI_API_KEY" not in kwargs["env"] for _, kwargs in calls)


def test_generic_readonly_executor_rejects_unknown_params_and_write_operations_before_network(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _generic_runner()
    monkeypatch.setattr(connector.subprocess, "run", runner)

    unknown = connector.execute_bundled_read_only("demo-site", "search", {"account_id": "x"})
    assert unknown["status"] == "undeclared_parameters"
    assert unknown["executed"] is False
    assert len(calls) == 2

    connector._GENERIC_COOLDOWNS.clear()
    write_runner, write_calls = _generic_runner(read_only=False)
    monkeypatch.setattr(connector.subprocess, "run", write_runner)
    refused = connector.execute_bundled_read_only("demo-site", "search", {"q": "nothing"})
    assert refused["status"] == "operation_not_verified_read_only"
    assert refused["executed"] is False
    assert len(write_calls) == 2


def test_generic_catalog_and_executor_refuse_operations_requiring_higher_transport_tiers(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, catalog_calls = _generic_runner(min_tier=2)
    monkeypatch.setattr(connector.subprocess, "run", runner)

    catalog = connector.list_bundled_read_only_operations()
    assert catalog["status"] == "empty"
    assert catalog["operations"] == []
    assert len(catalog_calls) == 2

    connector._GENERIC_COOLDOWNS.clear()
    runner, execution_calls = _generic_runner(min_tier=2)
    monkeypatch.setattr(connector.subprocess, "run", runner)
    refused = connector.execute_bundled_read_only("demo-site", "search", {"q": "anything"})
    assert refused["status"] == "tier_one_transport_required"
    assert refused["executed"] is False
    assert len(execution_calls) == 2


def test_generic_readonly_executor_rejects_unbundled_specs_and_sensitive_inputs(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _generic_runner(site_source="user")
    monkeypatch.setattr(connector.subprocess, "run", runner)

    untrusted = connector.execute_bundled_read_only("demo-site", "search", {"q": "test"})
    assert untrusted["status"] == "untrusted_site_spec"
    assert untrusted["executed"] is False
    assert len(calls) == 1

    connector._GENERIC_COOLDOWNS.clear()
    assert connector.execute_bundled_read_only("demo-site", "search", {"api_key": "value"})["status"] == "sensitive_parameter_not_allowed"
    assert connector.execute_bundled_read_only("bad site", "search", {})["status"] == "invalid_operation"



def test_generic_readonly_executor_accepts_callresult_without_optional_identity_fields(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _generic_runner(result={
        "ok": True, "class": "ok", "tier": 1,
        "data": [{"title": "Public result"}],
    })
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.execute_bundled_read_only("demo-site", "search", {"q": "climate data"})

    assert result["status"] == "completed"
    assert result["executed"] is True
    assert result["result"] == [{"title": "Public result"}]
    assert len(calls) == 3


def test_generic_readonly_executor_rejects_contradictory_result_identity(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _generic_runner(result={
        "ok": True, "class": "ok", "site": "other-site", "op": "search",
        "tier": 1, "data": [{"title": "Should not be accepted"}],
    })
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.execute_bundled_read_only("demo-site", "search", {"q": "climate data"})

    assert result["status"] == "result_identity_mismatch"
    assert result["executed"] is False
    assert len(calls) == 3


def _write_stub_runner(*, write_outcome="accepted", dry_preview=False):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        args = command[1:]
        if args == ["sites"]:
            payload = [{"name": "demo-site", "source": "bundled"}]
        elif args == ["ops", "demo-site"]:
            payload = {"operations": [{
                "name": "sendMessage",
                "readOnly": False,
                "parameters": ["recipient:string", "message:string"],
                "minTier": 1,
            }]}
        elif args[:3] == ["call", "demo-site", "sendMessage"]:
            if "--dry" in args:
                payload = {"ok": True, "class": "ok", "tier": 1, "request": {
                    "method": "POST", "url": "https://example.org/messages",
                    "body": {"recipient": "test-user", "message": "hello"},
                }}
            else:
                payload = {"ok": True, "class": "ok", "tier": 1,
                           "write": write_outcome, "data": {"receipt": "fixture-123"}}
        else:
            raise AssertionError(f"unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    return runner, calls


def test_generic_write_is_visible_but_not_sent_without_explicit_allowance(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _write_stub_runner()
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.execute_bundled_operation(
        "demo-site", "sendMessage", {"recipient": "test-user", "message": "hello"},
    )

    assert result["status"] == "write_confirmation_required"
    assert result["executed"] is False
    assert result["write"] is True
    assert not any(call[1:4] == ["call", "demo-site", "sendMessage"] for call in calls)


def test_generic_write_dry_run_never_claims_execution(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _write_stub_runner()
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.execute_bundled_operation(
        "demo-site", "sendMessage", {"recipient": "test-user", "message": "hello"},
        dry_run=True,
    )

    call = next(call for call in calls if call[1:4] == ["call", "demo-site", "sendMessage"])
    assert "--allow-writes" in call and "--dry" in call
    assert result["status"] == "dry_run_ready"
    assert result["executed"] is False
    assert result["request_preview"]["method"] == "POST"


def test_generic_write_acknowledgement_is_not_mislabeled_as_verified_delivery(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, calls = _write_stub_runner(write_outcome="accepted")
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.execute_bundled_operation(
        "demo-site", "sendMessage", {"recipient": "test-user", "message": "hello"},
        allow_writes=True,
    )

    assert result["status"] == "write_accepted_not_independently_verified"
    assert result["executed"] is True
    assert result["write_outcome"] == "accepted"
    call = next(call for call in calls if call[1:4] == ["call", "demo-site", "sendMessage"])
    assert "--allow-writes" in call and "--dry" not in call


def test_generic_write_ambiguous_result_warns_not_to_retry(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    runner, _ = _write_stub_runner(write_outcome="may_have_applied")
    monkeypatch.setattr(connector.subprocess, "run", runner)

    result = connector.execute_bundled_operation(
        "demo-site", "sendMessage", {"recipient": "test-user", "message": "hello"},
        allow_writes=True,
    )

    assert result["status"] == "write_outcome_ambiguous_check_target_do_not_retry"
    assert result["executed"] is True


def test_general_api_anything_catalog_includes_user_learned_reads_writes_and_tiers(monkeypatch, tmp_path):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    calls = []

    def fake_run(runner, executable, args, *, cwd, env, timeout):
        calls.append((list(args), dict(env)))
        if args == ["sites"]:
            return [
                {"name": "hacker-news", "source": "bundled"},
                {"name": "linkedin-workspace", "source": "user"},
            ], None
        if args == ["ops", "hacker-news"]:
            return {"operations": [{
                "name": "search", "readOnly": True, "minTier": 1,
                "parameters": [{"name": "query", "type": "string"}],
                "summary": "Search public stories",
            }]}, None
        if args == ["ops", "linkedin-workspace"]:
            return {"operations": [{
                "name": "sendMessage", "readOnly": False, "minTier": 3,
                "parameters": [{"name": "recipient"}, {"name": "message"}],
                "summary": "Send a message",
            }]}, None
        raise AssertionError(f"unexpected CLI request: {args}")

    monkeypatch.setattr(connector, "_run_json", fake_run)
    result = connector.list_api_anything_operations(user_id="user-a", include_writes=True)

    assert result["status"] == "ready"
    assert result["read_operation_count"] == 1
    assert result["write_operation_count"] == 1
    assert any(op["source"] == "user" and op["write_capable"] and op["min_tier"] == 3 for op in result["operations"])
    homes = {env["API_ANYTHING_HOME"] for _, env in calls}
    assert len(homes) == 1
    assert all(env.get("OPENAI_API_KEY") is None for _, env in calls)


def test_general_api_anything_executor_rejects_write_without_allowance_and_supports_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    connector._GENERIC_COOLDOWNS.clear()
    calls = []

    def fake_run(runner, executable, args, *, cwd, env, timeout):
        calls.append((list(args), dict(env)))
        if args == ["sites"]:
            return [{"name": "linkedin-workspace", "source": "user"}], None
        if args == ["ops", "linkedin-workspace"]:
            return {"operations": [{
                "name": "sendMessage", "readOnly": False, "minTier": 3,
                "parameters": [{"name": "recipient"}, {"name": "message"}],
            }]}, None
        if args[0:3] == ["call", "linkedin-workspace", "sendMessage"]:
            if "--dry" in args:
                return {"ok": True, "dry": True, "request": {"url": "https://example.com/message"}}, None
            return {"ok": True, "tier": 3, "data": {"queued": True}, "write": "accepted"}, None
        raise AssertionError(f"unexpected CLI request: {args}")

    monkeypatch.setattr(connector, "_run_json", fake_run)
    params = {"recipient": "reviewer", "message": "Hello"}
    refused = connector.execute_api_anything_operation(
        "linkedin-workspace", "sendMessage", params,
        user_id="user-a", allow_writes=False, max_tier=3,
    )
    assert refused["status"] == "write_confirmation_required"
    assert refused["executed"] is False
    assert len(calls) == 2

    connector._GENERIC_COOLDOWNS.clear()
    no_preview = connector.execute_api_anything_operation(
        "linkedin-workspace", "sendMessage", params,
        user_id="user-a", allow_writes=True, max_tier=3,
    )
    assert no_preview["status"] == "dry_run_required"
    assert no_preview["executed"] is False
    assert len(calls) == 4

    connector._GENERIC_COOLDOWNS.clear()
    preview = connector.execute_api_anything_operation(
        "linkedin-workspace", "sendMessage", params,
        user_id="user-a", allow_writes=True, max_tier=3, dry_run=True,
    )
    assert preview["status"] == "dry_run_ready"
    assert preview["executed"] is False
    assert preview["preview_receipt_saved"] is True
    call_args = calls[-1][0]
    assert "--allow-writes" in call_args
    assert "--dry" in call_args
    assert call_args[call_args.index("--max-tier") + 1] == "3"

    connector._GENERIC_COOLDOWNS.clear()
    executed = connector.execute_api_anything_operation(
        "linkedin-workspace", "sendMessage", params,
        user_id="user-a", allow_writes=True, max_tier=3,
    )
    assert executed["status"] == "write_accepted_not_independently_verified"
    assert executed["executed"] is True
    final_args = calls[-1][0]
    assert "--allow-writes" in final_args
    assert "--dry" not in final_args


def test_general_api_anything_executor_executes_scoped_user_read_with_selected_tier(monkeypatch, tmp_path):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    connector._GENERIC_COOLDOWNS.clear()
    calls = []

    def fake_run(runner, executable, args, *, cwd, env, timeout):
        calls.append((list(args), dict(env)))
        if args == ["sites"]:
            return [{"name": "reader-site", "source": "user"}], None
        if args == ["ops", "reader-site"]:
            return {"operations": [{
                "name": "getRecord", "readOnly": True, "minTier": 2,
                "parameters": [{"name": "recordId"}],
            }]}, None
        if args[0:3] == ["call", "reader-site", "getRecord"]:
            return {"ok": True, "tier": 2, "data": {"title": "Test record"}}, None
        raise AssertionError(f"unexpected CLI request: {args}")

    monkeypatch.setattr(connector, "_run_json", fake_run)
    result = connector.execute_api_anything_operation(
        "reader-site", "getRecord", {"recordId": "rec-42"},
        user_id="user-b", allow_writes=False, max_tier=2,
    )
    assert result["status"] == "completed"
    assert result["executed"] is True
    assert result["transport_tier"] == 2
    assert calls[-1][0][calls[-1][0].index("--max-tier") + 1] == "2"
    assert calls[-1][1]["API_ANYTHING_HOME"] != calls[0][1]["API_ANYTHING_HOME"]


def test_api_anything_learning_accepts_two_examples_and_refuses_local_targets(monkeypatch, tmp_path):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    calls = []

    def fake_run(runner, executable, args, *, cwd, env, timeout):
        calls.append((list(args), dict(env)))
        return {"ok": True, "status": "learned", "preview": {"operation": "search"}}, None

    monkeypatch.setattr(connector, "_run_json", fake_run)
    invalid = connector.learn_api_anything_operation(
        "demo-site", "search", "http://127.0.0.1/admin?q={q}",
        {"q": "cat"}, {"q": "dog"}, user_id="user-a",
    )
    assert invalid["status"] == "public_https_url_required"
    assert calls == []

    learned = connector.learn_api_anything_operation(
        "demo-site", "search", "https://example.com/search?q={q}",
        {"q": "cat"}, {"q": "dog"}, user_id="user-a", write=False,
        match={"method": "GET", "host": "example.com", "path": "/search"},
    )
    assert learned["status"] == "learned"
    args = calls[-1][0]
    assert args[:4] == ["add", "demo-site", "search", "--trigger"]
    assert "https://example.com/search?q={q}" in args
    assert args.count("--example") == 1
    assert args.count("--example2") == 1
    assert calls[-1][1]["API_ANYTHING_HOME"]


def test_api_anything_write_learning_uses_aborted_write_capture_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    calls = []

    def fake_run(runner, executable, args, *, cwd, env, timeout):
        calls.append(list(args))
        return {"ok": True, "status": "learned", "preview": {"operation": "sendMessage"}}, None

    monkeypatch.setattr(connector, "_run_json", fake_run)
    result = connector.learn_api_anything_operation(
        "social-site", "sendMessage", "https://example.com/messages?to={recipient}",
        {"recipient": "person-a"}, {"recipient": "person-b"},
        user_id="user-a", write=True,
    )
    assert result["status"] == "learned"
    assert result["learning_aborts_write_requests"] is True
    assert "--write" in calls[-1]


def test_general_executor_accepts_declared_credentials_and_nested_json_without_echoing_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    connector._GENERIC_COOLDOWNS.clear()
    calls = []

    def fake_run(runner, executable, args, *, cwd, env, timeout):
        calls.append((list(args), dict(env)))
        if args == ["sites"]:
            return [{"name": "data.example.com", "source": "user"}], None
        if args == ["ops", "data.example.com"]:
            return {"operations": [{
                "name": "queryRecords", "readOnly": True, "minTier": 1,
                "parameters": [{"name": "api_key"}, {"name": "filters"}],
            }]}, None
        if args[0:3] == ["call", "data.example.com", "queryRecords"]:
            return {"ok": True, "tier": 1, "data": {
                "records": [{"id": "record-1"}], "api_key": "echoed-secret",
            }}, None
        raise AssertionError(f"unexpected CLI request: {args}")

    monkeypatch.setattr(connector, "_run_json", fake_run)
    params = {
        "api_key": "caller-secret-value",
        "filters": {"regions": ["AF", "PK"], "active": True},
    }
    result = connector.execute_api_anything_operation(
        "data.example.com", "queryRecords", params,
        user_id="research-user", allow_writes=False, max_tier=1,
    )

    assert result["status"] == "completed"
    assert result["executed"] is True
    call_args = calls[-1][0]
    sent = json.loads(call_args[call_args.index("--json") + 1])
    assert sent == params
    serialized_result = json.dumps(result, ensure_ascii=False)
    assert "caller-secret-value" not in serialized_result
    assert "echoed-secret" not in serialized_result
    assert result["result"]["api_key"] == "[redacted]"


def test_status_distinguishes_empty_bundled_writes_from_supported_user_write_execution(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    state = connector.status()
    assert state["available"] is True
    assert state["generic_bundled_write_operations"] is False
    assert state["user_learned_write_operations"] is True
    assert state["registry_operations"] is True
    assert state["supported_transport_tiers"] == [1, 2, 3]
    assert state["write_execution_requires_matching_dry_run"] is True
    assert state["browser_learning_supported"] is True
    assert state["browser_learning_runtime_verified"] is False
    assert state["durable_profile_storage_verified"] is False


def test_learning_supports_interactive_steps_and_soft_navigation(monkeypatch, tmp_path):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    calls = []

    def fake_run(runner, executable, args, *, cwd, env, timeout):
        calls.append((list(args), dict(env)))
        return {"ok": True, "status": "learned", "preview": {"operation": "search"}}, None

    monkeypatch.setattr(connector, "_run_json", fake_run)
    result = connector.learn_api_anything_operation(
        "example.com", "search", "https://example.com/search",
        {"q": "cats"}, {"q": "dogs"},
        user_id="step-user",
        steps=[
            {"action": "fill", "selector": "#search", "value": "{q}"},
            {"action": "press", "selector": "#search", "value": "Enter"},
        ],
        soft_from="https://example.com/",
        description="Search records through the website UI",
    )

    assert result["status"] == "learned"
    assert result["has_browser_steps"] is True
    assert result["has_soft_navigation"] is True
    args = calls[-1][0]
    assert "--steps" in args
    step_json = json.loads(args[args.index("--steps") + 1])
    assert step_json[0]["value"] == "{q}"
    assert "--soft-from" in args
    assert args[args.index("--soft-from") + 1] == "https://example.com/"
    assert "--description" in args


def test_learning_rejects_private_browser_goto_step(monkeypatch, tmp_path):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    monkeypatch.setenv("AI_INFINITY_DATA_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(
        connector, "_run_json",
        lambda *args, **kwargs: (calls.append(args) or ({"ok": True, "status": "learned"}, None)),
    )
    result = connector.learn_api_anything_operation(
        "example.com", "search", "https://example.com/search?q={q}",
        {"q": "cats"}, {"q": "dogs"},
        user_id="step-user",
        steps=[{"action": "goto", "value": "http://127.0.0.1/private"}],
    )
    assert result["status"] == "public_https_url_required"
    assert calls == []
