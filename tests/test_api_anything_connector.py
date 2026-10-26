from __future__ import annotations

import json
import subprocess

import api_anything_connector as connector


def _stub_runner(*, site_source="bundled", read_only=True, result=None, error=False):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        args = command[1:]
        if args == ["sites"]:
            payload = [{"name": "youtube", "source": site_source}]
        elif args == ["ops", "youtube"]:
            payload = {"operations": [{"name": "search", "readOnly": read_only}]}
        elif args[:3] == ["call", "youtube", "search"]:
            if error:
                return subprocess.CompletedProcess(command, 2, stdout="", stderr="secret=do-not-return")
            payload = result if result is not None else {
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
        "youtube_search",
        lambda query, limit=10: {
            "status": "ready",
            "items": [{
                "title": "Research example",
                "url": "https://www.youtube.com/watch?v=abcdefghijk",
                "channel": "Example channel",
                "views": "100 views",
            }],
            "truthful": True,
        },
    )

    result = production_intelligence.research("AI video editing", limit=5)

    assert result["provider"] == "api-anything-youtube"
    assert result["source_count"] == 1
    assert result["api_anything"] == {
        "status": "ready",
        "items_added": 1,
        "operation": "youtube.search",
        "truthful": True,
    }
    assert result["sources"][0]["source"] == "API Anything / YouTube"
    assert result["sources"][0]["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
