from __future__ import annotations

from scripts.benchmark_github_access import resolve_benchmark_token


def test_benchmark_requires_explicit_owner_token_and_never_falls_back_to_workflow_token():
    token, source = resolve_benchmark_token({
        "GITHUB_TOKEN": "workflow-token-with-read-only-or-limited-permissions",
    })
    assert token == ""
    assert source == "missing_dedicated_owner_token"


def test_benchmark_prefers_dedicated_maintenance_token():
    token, source = resolve_benchmark_token({
        "AI_INFINITY_MAINTENANCE_GITHUB_TOKEN": "owner-token",
        "AI_INFINITY_GITHUB_TOKEN": "legacy-owner-token",
        "GITHUB_TOKEN": "workflow-token",
    })
    assert token == "owner-token"
    assert source == "AI_INFINITY_MAINTENANCE_GITHUB_TOKEN"


def test_benchmark_accepts_legacy_explicit_owner_token_alias():
    token, source = resolve_benchmark_token({
        "AI_INFINITY_GITHUB_TOKEN": "legacy-owner-token",
        "GITHUB_TOKEN": "workflow-token",
    })
    assert token == "legacy-owner-token"
    assert source == "AI_INFINITY_GITHUB_TOKEN"
