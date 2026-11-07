from __future__ import annotations

import json
import sys

import scripts.benchmark_github_access as benchmark
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


def test_full_github_benchmark_probes_identity_cross_repo_write_and_cleanup(monkeypatch, tmp_path):
    report_path = tmp_path / "github-benchmark.json"
    for key, value in {
        "AI_INFINITY_MAINTENANCE_GITHUB_TOKEN": "owner-token-fixture",
        "AI_INFINITY_GITHUB_TOKEN": "",
        "AI_INFINITY_MAINTENANCE_GITHUB_TOKEN_SOURCE": "AI_INFINITY_MAINTENANCE_GITHUB_TOKEN",
        "GITHUB_TOKEN": "workflow-token-must-not-be-used",
        "GITHUB_REPOSITORY": "owner/project",
        "AI_INFINITY_BENCHMARK_HEAD_SHA": "a" * 40,
        "GITHUB_RUN_ID": "777",
        "GITHUB_RUN_ATTEMPT": "1",
        "AI_INFINITY_MAINTENANCE_REPOSITORY": "owner/project",
        "AI_INFINITY_MAINTENANCE_BASE_BRANCH": "main",
        "AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE": "restricted",
        "AI_INFINITY_SELF_MAINTENANCE_ENABLED": "false",
        "AI_INFINITY_MAINTENANCE_ADMIN_TOKEN": "old-admin-placeholder",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "argv", ["benchmark", "--output", str(report_path)])

    calls = []

    def fake_github_request(cfg, method, path, payload=None):
        calls.append((method, path, payload, cfg.get("github_access_mode")))
        if path == "/repos/owner/project":
            return {
                "default_branch": "main",
                "visibility": "private",
                "permissions": {"pull": True, "push": True, "admin": True},
            }
        if path == "/user":
            return {"login": "owner", "id": 123, "type": "User"}
        if path.startswith("/user/repos?"):
            if "&page=1&" in path:
                rows = [{
                    "full_name": "owner/project",
                    "private": True,
                    "default_branch": "main",
                    "permissions": {"pull": True, "push": True, "admin": True},
                }, {
                    "full_name": "owner/second-project",
                    "private": True,
                    "default_branch": "develop",
                    "permissions": {"pull": True, "push": True, "admin": False},
                }]
                rows.extend({
                    "full_name": f"owner/benchmark-fixture-{index:03d}",
                    "private": True,
                    "default_branch": "main",
                    "permissions": {"pull": True, "push": False, "admin": False},
                } for index in range(98))
                return rows
            if "&page=2&" in path:
                return [{
                    "full_name": "owner/third-project",
                    "private": True,
                    "default_branch": "main",
                    "permissions": {"pull": True, "push": True, "admin": False},
                }]
            return []
        if path == "/graphql" and method == "POST":
            assert payload == {"query": "query { viewer { login } }", "variables": {}}
            return {"data": {"viewer": {"login": "owner"}}}
        if path == "/repos/owner/second-project":
            return {
                "visibility": "private",
                "permissions": {"pull": True, "push": True, "admin": False},
            }
        if path == "/repos/owner/second-project/git/ref/heads/develop":
            return {"object": {"sha": "b" * 40}}
        if path == "/repos/owner/second-project/git/refs" and method == "POST":
            return {"ref": "refs/heads/ai-infinity-access-probe-777-1"}
        if path == "/user/orgs?per_page=100":
            return []
        if path == "/rate_limit":
            return {"resources": {"core": {"limit": 5000, "remaining": 4999, "reset": 0}}}
        if path == "/repos/owner/project/pulls/123":
            return {"draft": True, "state": "open", "head": {"ref": "ai-infinity/self-maintenance/probe"}}
        if path.startswith("/repos/owner/project/git/refs/heads/"):
            return {"ref": path}
        if path == "/repos/owner/project/pulls/123" and method == "PATCH":
            return {"state": "closed"}
        if path == "/repos/owner/second-project/git/refs/heads/ai-infinity-access-probe-777-1":
            return {}
        raise AssertionError(f"unexpected GitHub call: {method} {path}")

    monkeypatch.setattr(benchmark.maintenance, "github_request", fake_github_request)
    monkeypatch.setattr(
        benchmark.maintenance,
        "discover_sources",
        lambda *args, **kwargs: {
            "matched_count": 1, "tree_truncated": False, "suggested_paths": [],
            "matches": [{"path": "tests/test_sample.py"}],
        },
    )
    monkeypatch.setattr(
        benchmark.maintenance,
        "inspect_sources",
        lambda *args, **kwargs: {
            "files": [], "total_source_bytes": 0, "total_context_bytes": 0,
        },
    )
    monkeypatch.setattr(
        benchmark.maintenance,
        "inspect_ci",
        lambda *args, **kwargs: {
            "commit_sha_filter": "a" * 40, "run_count": 1, "passed_runs": 1,
            "failed_runs": 0, "in_progress_runs": 0, "runs": [],
        },
    )
    monkeypatch.setattr(
        benchmark.maintenance,
        "improve_repository",
        lambda *args, **kwargs: {
            "pull_request": {
                "pull_request_number": 123,
                "branch": "ai-infinity/self-maintenance/probe",
                "pull_request_url": "https://github.com/owner/project/pull/123",
            },
            "changed_files": ["tests/test_sample.py"],
            "provider": "deterministic-fixture",
        },
    )

    result = benchmark.main()
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result == 0
    assert report["status"] == "PASS"
    assert report["access_mode"] == "github_full"
    assert report["application_repository_allowlist"] is False
    assert report["account_wide_access_verified"] is True
    assert report["account_wide_access_verification"]["status"] == "VERIFIED"
    assert report["account_wide_access_verification"]["scope"] == (
        "complete_repository_inventory_returned_by_the_configured_token_and_sampled_cross_repository_write"
    )
    assert report["account_wide_access_verification"]["total_token_visible_repositories"] == 101
    assert all(report["account_wide_access_verification"]["checks"].values())
    assert report["required_operations"]["account_wide_access_verification"] == "PASS"
    assert report["authenticated_identity"]["login"] == "owner"
    assert report["accessible_repositories"]["first_page_count"] == 100
    assert report["accessible_repositories"]["total_repository_count"] == 101
    assert report["accessible_repositories"]["pages_fetched"] == 2
    assert report["accessible_repositories"]["pagination_complete"] is True
    assert report["required_operations"]["accessible_repositories_all_pages"] == "PASS"
    assert report["required_operations"]["authenticated_graphql_viewer_read"] == "PASS"
    assert report["graphql_identity"]["login"] == "owner"
    assert report["cross_repository_probe"]["repository"] == "owner/second-project"
    assert report["cross_repository_write_probe"]["branch_created"] is True
    assert report["required_operations"]["cross_repository_temporary_branch_delete_cleanup"] == "PASS"
    assert any(method == "POST" and path == "/graphql" for method, path, _, _ in calls)
    assert any(
        method == "POST" and path == "/repos/owner/second-project/git/refs"
        for method, path, _, _ in calls
    )
    assert any(
        method == "DELETE" and path == "/repos/owner/second-project/git/refs/heads/ai-infinity-access-probe-777-1"
        for method, path, _, _ in calls
    )
    assert all(mode == "github_full" for _, _, _, mode in calls)
    assert report["token_value_recorded"] is False


def test_missing_owner_token_writes_truthful_blocked_metrics(monkeypatch, tmp_path):
    report_path = tmp_path / "blocked-github-benchmark.json"
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/project")
    monkeypatch.setenv("AI_INFINITY_MAINTENANCE_GITHUB_TOKEN", "")
    monkeypatch.setenv("AI_INFINITY_GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_TOKEN", "workflow-token-not-authorized-for-benchmark")
    monkeypatch.setattr(
        sys, "argv", ["benchmark", "--output", str(report_path)]
    )

    result = benchmark.main()
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result == 2
    assert report["status"] == "BLOCKED"
    assert report["credential_source"] == "missing_dedicated_owner_token"
    assert report["operation_attempt_count"] == 0
    assert report["operation_pass_count"] == 0
    assert report["operation_fail_count"] == 0
    assert report["operation_pass_rate"] is None
    assert report["duration_seconds"] >= 0
    assert report["token_value_recorded"] is False
    assert report["secrets_returned"] is False

def test_account_wide_verification_fails_closed_for_truncated_or_unproven_repository_coverage():
    report = {
        "authenticated_identity": {"login": "owner"},
        "graphql_identity": {"login": "owner"},
        "accessible_repositories": {
            "pagination_complete": False,
            "complete_account_inventory_claimed": False,
            "total_repository_count": 101,
        },
        "cross_repository_probe": {"repository": "owner/second-project"},
        "cross_repository_write_probe": {
            "branch_created": True,
            "default_branch_modified": False,
        },
        "temporary_write_probe": {"pull_request_number": 123},
    }
    operations = {
        "accessible_repositories_all_pages": "PASS",
        "cross_repository_metadata_read_sample": "PASS",
        "cross_repository_temporary_branch_create": "PASS",
        "cross_repository_temporary_branch_delete_cleanup": "PASS",
        "self_maintenance_source_to_commit_to_draft_pr": "PASS",
        "temporary_pull_request_readback": "PASS",
        "temporary_pull_request_close": "PASS",
        "temporary_branch_delete_cleanup": "PASS",
    }

    result = benchmark.account_wide_access_verification(report, operations)

    assert result["verified"] is False
    assert result["status"] == "NOT_VERIFIED"
    assert result["checks"]["complete_token_visible_repository_inventory"] is False

