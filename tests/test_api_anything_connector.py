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
    yield
    connector._LAST_CALL_AT.clear()


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
            ],
            "operations": {"youtube.search": "ready", "hacker-news.search": "ready"},
            "truthful": True,
        },
    )

    installed = production_intelligence.install_creator_research_connector()
    result = creator.research_topic("AI video editing", limit=5)

    assert installed["status"] == "installed"
    assert result["source_count"] == 3
    assert result["provider_count"] == 3
    assert [s["source"] for s in result["sources"]].count("API Anything / YouTube") == 1
    assert [s["source"] for s in result["sources"]].count("API Anything / Hacker News") == 1
    assert result["research_connector"]["items_added"] == 2
    assert result["providers"]["API Anything / YouTube"]["status"] == "ready"
    assert result["providers"]["API Anything / Hacker News"]["status"] == "ready"


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
            "steam", "search", "term", "steam",
            {"itemKey": "504230", "title": "Celeste", "price": "$19.99", "releaseDate": "2018"},
            "API Anything / Steam", "https://store.steampowered.com/app/504230/",
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
    assert call[:3] == ["call", site, operation]
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
    games = connector.search_candidates("Compare PC games about space exploration")
    assert games["operations"].keys() == {"youtube.search", "hacker-news.search", "steam.search"}
    assert [site for site, _ in seen] == ["youtube", "hacker-news", "steam"]

    seen.clear()
    products = connector.search_candidates("Research laptop products and price comparison")
    assert products["operations"].keys() == {"youtube.search", "hacker-news.search", "amazon.search"}
    assert [site for site, _ in seen] == ["youtube", "hacker-news", "amazon"]


def test_additional_sources_reject_invalid_ids_before_constructing_links(monkeypatch):
    monkeypatch.setattr(connector.shutil, "which", lambda _: "/usr/local/bin/api-anything")
    cases = [
        ("goodreads", "searchBooks", "goodreads", {"bookId": "../private", "title": "Bad book"}),
        ("steam", "search", "steam", {"itemKey": "../../private", "title": "Bad game"}),
        ("amazon", "search", "amazon", {"asin": "https://evil.example", "title": "Bad product"}),
    ]
    for site, operation, result_kind, row in cases:
        runner, _ = _stub_runner(
            site_name=site, operation_name=operation,
            result={"ok": True, "class": "ok", "tier": 1, "data": [row]},
        )
        monkeypatch.setattr(connector.subprocess, "run", runner)
        query_parameter = {"goodreads": "q", "steam": "term", "amazon": "k"}[site]
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
