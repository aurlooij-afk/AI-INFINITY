from __future__ import annotations

"""Measure AI Infinity's real GitHub repository access without touching main.

The benchmark requires a dedicated owner-provisioned GitHub token. It never falls
back to the workflow GITHUB_TOKEN, whose effective write permissions may be restricted.
It exercises AI Infinity's own discovery, inspection, commit and draft-PR path.
Every write is made on a temporary branch; the draft PR is closed and the branch
is deleted in a finally block. No token or secret value is written to the report.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import self_maintenance as maintenance


def _benchmark_model(run_marker: str):
    """Deterministic writer fixture: test the GitHub execution path, not model quality."""
    def invoke(payload: dict) -> dict:
        messages = payload.get("messages") if isinstance(payload, dict) else []
        prompt = str(messages[0].get("content") or "") if messages else ""
        marker = "INPUT: "
        if marker not in prompt:
            raise RuntimeError("self-maintenance input envelope missing")
        source = json.loads(prompt.rsplit(marker, 1)[1])
        files = source.get("files") if isinstance(source, dict) else []
        row = next(
            (item for item in (files or []) if isinstance(item, dict)
             and item.get("path") == "tests/test_project_status_truth_latency.py"),
            None,
        )
        if not row or row.get("content_mode") != "full_file":
            raise RuntimeError("benchmark regression test was not available as a full-file source")
        updated = str(row.get("content") or "")
        updated += (
            "\n\n# Temporary AI Infinity GitHub autonomy benchmark marker: "
            + run_marker + ". This line is removed with the benchmark branch.\n"
        )
        result = {
            "summary": "Temporary GitHub-access probe; no product behavior changed.",
            "changes": [{"path": "tests/test_project_status_truth_latency.py", "content": updated}],
        }
        return {"text": json.dumps(result), "provider": "deterministic-github-access-benchmark"}

    return invoke


def resolve_benchmark_token(environ: dict[str, str] | None = None) -> tuple[str, str]:
    """Resolve only an explicitly configured owner token; never downgrade silently."""
    source = os.environ if environ is None else environ
    if source.get("AI_INFINITY_MAINTENANCE_GITHUB_TOKEN", "").strip():
        return source["AI_INFINITY_MAINTENANCE_GITHUB_TOKEN"].strip(), "AI_INFINITY_MAINTENANCE_GITHUB_TOKEN"
    if source.get("AI_INFINITY_GITHUB_TOKEN", "").strip():
        return source["AI_INFINITY_GITHUB_TOKEN"].strip(), "AI_INFINITY_GITHUB_TOKEN"
    return "", "missing_dedicated_owner_token"


def _save(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="github-access-benchmark.json")
    args = parser.parse_args()
    output = Path(args.output)

    repo = os.getenv("GITHUB_REPOSITORY") or os.getenv("AI_INFINITY_MAINTENANCE_REPOSITORY", "")
    token, resolved_token_source = resolve_benchmark_token()
    token_source = os.getenv("AI_INFINITY_MAINTENANCE_GITHUB_TOKEN_SOURCE", resolved_token_source)
    head_sha = os.getenv("AI_INFINITY_BENCHMARK_HEAD_SHA", os.getenv("GITHUB_SHA", ""))
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    run_attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
    run_marker = f"{run_id}-{run_attempt}"

    report: dict[str, Any] = {
        "schema": "ai-infinity-github-autonomy-benchmark.v1",
        "repository": repo,
        "source_revision": head_sha,
        "workflow_run_id": run_id,
        "workflow_run_attempt": run_attempt,
        "credential_source": token_source,
        "access_mode": "github_full",
        "scope": "all_GitHub_API_resources_authorized_by_configured_owner_token",
        "application_repository_allowlist": False,
        "account_wide_access_verified": False,
        "started_at_epoch": time.time(),
        "operations": [],
        "status": "RUNNING",
        "truthful": True,
    }
    if not repo or "/" not in repo or not token:
        completed = time.time()
        report.update({
            "status": "BLOCKED",
            "error": (
                "GITHUB_REPOSITORY is invalid or no dedicated owner-provisioned GitHub token is configured. "
                "The workflow GITHUB_TOKEN is intentionally not accepted as a substitute."
            ),
            "completed_at_epoch": completed,
            "duration_seconds": round(completed - report["started_at_epoch"], 3),
            "operation_pass_count": 0,
            "operation_fail_count": 0,
            "operation_blocked_count": 0,
            "operation_attempt_count": 0,
            "operation_pass_rate": None,
            "token_value_recorded": False,
            "secrets_returned": False,
            "temporary_pull_request_merged": False,
            "production_deployment_performed": False,
        })
        _save(output, report)
        return 2

    os.environ["AI_INFINITY_MAINTENANCE_REPOSITORY"] = repo
    os.environ.setdefault("AI_INFINITY_MAINTENANCE_BASE_BRANCH", "main")
    os.environ["AI_INFINITY_MAINTENANCE_GITHUB_TOKEN"] = token
    os.environ["AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE"] = "github_full"
    os.environ["AI_INFINITY_SELF_MAINTENANCE_ENABLED"] = "true"
    # The benchmark invokes internal functions directly; this one-use value is never
    # transmitted and is not used as a GitHub credential.
    os.environ["AI_INFINITY_MAINTENANCE_ADMIN_TOKEN"] = "ephemeral-github-benchmark"
    cfg = maintenance.load_settings()

    def record(name: str, started: float, status: str, details: dict | None = None) -> None:
        report["operations"].append({
            "name": name,
            "status": status,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            **(details or {}),
        })

    created_pr = None
    created_branch = None
    created_cross_repository_branch = None
    exit_code = 1

    def run_operation(name: str, action):
        started = time.monotonic()
        try:
            result = action()
            report["operations"].append({
                "name": name,
                "status": "PASS",
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            })
            return True, result
        except Exception as exc:
            detail = getattr(exc, "detail", None)
            if isinstance(detail, (str, int, float)):
                safe_error = str(detail)[:300]
            else:
                safe_error = type(exc).__name__
            report["operations"].append({
                "name": name,
                "status": "FAIL",
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "error_type": type(exc).__name__,
                "error": safe_error,
            })
            return False, None

    try:
        ok, metadata = run_operation(
            "repository_metadata_and_effective_permissions",
            lambda: maintenance.github_request(cfg, "GET", f"/repos/{repo}"),
        )
        if ok:
            permissions = metadata.get("permissions") if isinstance(metadata.get("permissions"), dict) else None
            report["repository_permissions"] = {
                "reported_by_github": permissions is not None,
                "values": {
                    key: permissions.get(key)
                    for key in ("pull", "triage", "push", "maintain", "admin")
                    if permissions is not None and key in permissions
                } if permissions is not None else None,
                "note": (
                    "GitHub did not return a repository permissions object for this token."
                    if permissions is None else
                    "Reported permission flags are descriptive; the operation probes below establish actual access."
                ),
            }
            report["repository_metadata"] = {
                "default_branch": metadata.get("default_branch"),
                "visibility": metadata.get("visibility"),
                "archived": metadata.get("archived"),
            }

        def read_authenticated_user():
            value = maintenance.github_request(cfg, "GET", "/user")
            if not isinstance(value, dict) or not value.get("login"):
                raise RuntimeError("GitHub did not return an authenticated user identity")
            return value

        ok, identity = run_operation(
            "authenticated_user_identity",
            read_authenticated_user,
        )
        if ok:
            report["authenticated_identity"] = {
                "login": str(identity.get("login") or "")[:100],
                "id": identity.get("id"),
                "type": str(identity.get("type") or "")[:40],
            }

        def list_accessible_repositories():
            value = maintenance.github_request(
                cfg,
                "GET",
                "/user/repos?per_page=100&visibility=all&affiliation=owner,collaborator,organization_member&sort=updated",
            )
            if not isinstance(value, list):
                raise RuntimeError("GitHub accessible-repository response was not an array")
            return value

        def list_accessible_repositories():
            page_size = 100
            max_pages = 100
            rows = []
            first_page_count = 0
            pagination_complete = False
            for page in range(1, max_pages + 1):
                value = maintenance.github_request(
                    cfg,
                    "GET",
                    (
                        "/user/repos?per_page=100"
                        f"&page={page}&visibility=all"
                        "&affiliation=owner,collaborator,organization_member&sort=updated"
                    ),
                )
                if not isinstance(value, list):
                    raise RuntimeError("GitHub accessible-repository response was not an array")
                if page == 1:
                    first_page_count = len(value)
                rows.extend(value)
                if len(value) < page_size:
                    pagination_complete = True
                    break
            return {
                "rows": rows,
                "pages_fetched": min(page, max_pages),
                "first_page_count": first_page_count,
                "pagination_complete": pagination_complete,
                "page_limit": max_pages,
            }

        ok, repository_inventory = run_operation(
            "accessible_repositories_all_pages",
            list_accessible_repositories,
        )
        sampled_repository = None
        if ok:
            normalized_repositories = []
            for row in repository_inventory["rows"]:
                if not isinstance(row, dict):
                    continue
                full_name = str(row.get("full_name") or "")
                if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", full_name):
                    normalized_repositories.append({
                        "full_name": full_name,
                        "private": row.get("private") is True,
                        "archived": row.get("archived") is True,
                        "default_branch": str(row.get("default_branch") or "")[:120],
                        "permissions": {
                            key: (row.get("permissions") or {}).get(key)
                            for key in ("pull", "push", "admin")
                            if isinstance(row.get("permissions"), dict) and key in row.get("permissions", {})
                        },
                    })
            report["accessible_repositories"] = {
                "first_page_count": repository_inventory["first_page_count"],
                "total_repository_count": len(normalized_repositories),
                "pages_fetched": repository_inventory["pages_fetched"],
                "pagination_complete": repository_inventory["pagination_complete"],
                "page_limit": repository_inventory["page_limit"],
                "first_page_sample": normalized_repositories[:20],
                "complete_account_inventory_claimed": repository_inventory["pagination_complete"],
                "scope_note": "This is the set returned by /user/repos for the configured token; repositories outside the token's grants and organization policy are not visible.",
            }
            sampled_repository = next(
                (
                    row for row in normalized_repositories
                    if row["full_name"].casefold() != repo.casefold()
                ),
                None,
            )

        if sampled_repository:
            sample_name = sampled_repository["full_name"]
            ok, cross_repo_metadata = run_operation(
                "cross_repository_metadata_read_sample",
                lambda: maintenance.github_request(cfg, "GET", f"/repos/{sample_name}"),
            )
            if ok:
                permissions = cross_repo_metadata.get("permissions") if isinstance(cross_repo_metadata, dict) else None
                report["cross_repository_probe"] = {
                    "repository": sample_name,
                    "visibility": cross_repo_metadata.get("visibility"),
                    "permissions": {
                        key: permissions.get(key)
                        for key in ("pull", "triage", "push", "maintain", "admin")
                    } if isinstance(permissions, dict) else None,
                }
        else:
            report["operations"].append({
                "name": "cross_repository_metadata_read_sample",
                "status": "BLOCKED",
                "error": "No second authorized repository was returned on the first user-repositories page; multi-repository access was not demonstrated.",
            })

        if sampled_repository:
            sample_name = sampled_repository["full_name"]
            base_branch = sampled_repository.get("default_branch") or "main"
            probe_branch = f"ai-infinity-access-probe-{run_marker}"

            def create_cross_repository_probe_branch():
                base_ref = maintenance.github_request(
                    cfg,
                    "GET",
                    f"/repos/{sample_name}/git/ref/heads/{quote(base_branch, safe='')}",
                )
                base_sha = str((base_ref.get("object") or {}).get("sha") or "")
                if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
                    raise RuntimeError("GitHub did not return a valid second-repository base SHA")
                maintenance.github_request(
                    cfg,
                    "POST",
                    f"/repos/{sample_name}/git/refs",
                    {"ref": "refs/heads/" + probe_branch, "sha": base_sha},
                )
                return {"branch": probe_branch, "base_sha": base_sha}

            ok, cross_branch = run_operation(
                "cross_repository_temporary_branch_create",
                create_cross_repository_probe_branch,
            )
            if ok:
                created_cross_repository_branch = (sample_name, probe_branch)
                report["cross_repository_write_probe"] = {
                    "repository": sample_name,
                    "branch_created": True,
                    "commit_created": False,
                    "default_branch_modified": False,
                }
        else:
            report["operations"].append({
                "name": "cross_repository_temporary_branch_create",
                "status": "BLOCKED",
                "error": "No second authorized repository was available for a reversible cross-repository write probe.",
            })

        ok, organizations = run_operation(
            "organization_memberships_first_page",
            lambda: maintenance.github_request(cfg, "GET", "/user/orgs?per_page=100"),
        )
        if ok:
            if not isinstance(organizations, list):
                report["operations"][-1]["status"] = "FAIL"
                report["operations"][-1]["error"] = "GitHub organization-membership response was not an array"
            else:
                report["organization_memberships"] = {
                    "first_page_count": len(organizations),
                    "names": [
                        str(row.get("login") or "")[:100]
                        for row in organizations[:20]
                        if isinstance(row, dict) and row.get("login")
                    ],
                    "complete_account_inventory_claimed": False,
                }

        def read_authenticated_graphql_viewer():
            value = maintenance.github_request(
                cfg,
                "POST",
                "/graphql",
                {"query": "query { viewer { login } }", "variables": {}},
            )
            viewer = ((value.get("data") or {}).get("viewer") or {}) if isinstance(value, dict) else {}
            if not viewer.get("login"):
                errors = value.get("errors") if isinstance(value, dict) else None
                raise RuntimeError(
                    "GitHub GraphQL viewer query did not return a login"
                    + (f" ({len(errors)} GraphQL errors)" if isinstance(errors, list) else "")
                )
            return viewer

        ok, viewer = run_operation(
            "authenticated_graphql_viewer_read",
            read_authenticated_graphql_viewer,
        )
        if ok:
            report["graphql_identity"] = {"login": str(viewer.get("login") or "")[:100]}

        ok, rate = run_operation(
            "authenticated_rate_limit_read",
            lambda: maintenance.github_request(cfg, "GET", "/rate_limit"),
        )
        if ok:
            core = (rate.get("resources") or {}).get("core") or {}
            report["rate_limit"] = {
                "limit": core.get("limit"),
                "remaining": core.get("remaining"),
                "reset_epoch": core.get("reset"),
            }

        goal = (
            "Inspect complete repository source, workflows, tests and execution modules; "
            "measure GitHub-backed self-maintenance discovery and candidate operations."
        )
        ok, discovery = run_operation(
            "repository_wide_recursive_tree_discovery",
            lambda: maintenance.discover_sources(cfg, goal, limit=40),
        )
        if ok:
            report["discovery"] = {
                "matched_count": discovery.get("matched_count"),
                "tree_truncated": discovery.get("tree_truncated"),
                "selected_paths": discovery.get("suggested_paths") or [],
                "includes_github_workflows": any(
                    str(row.get("path", "")).startswith(".github/workflows/")
                    for row in discovery.get("matches", [])
                ),
            }

        inspection_paths = [
            ".github/workflows/creator-studio-ci.yml",
            "main.py",
            "tests/test_project_status_truth_latency.py",
            "render.yaml",
        ]
        ok, inspected = run_operation(
            "cross_directory_source_inspection",
            lambda: maintenance.inspect_sources(cfg, inspection_paths, goal=goal),
        )
        if ok:
            report["inspection"] = {
                "files": [
                    {
                        "path": row.get("path"),
                        "size_bytes": row.get("size_bytes"),
                        "content_mode": row.get("content_mode"),
                        "sha256": row.get("sha256"),
                    }
                    for row in inspected.get("files", [])
                ],
                "total_source_bytes": inspected.get("total_source_bytes"),
                "total_context_bytes": inspected.get("total_context_bytes"),
            }

        if head_sha and len(head_sha) == 40:
            ok, ci = run_operation(
                "exact_candidate_workflow_and_job_inspection",
                lambda: maintenance.inspect_ci(cfg, commit_sha=head_sha, limit=10),
            )
            if ok:
                report["commit_bound_ci"] = {
                    "commit_sha_filter": ci.get("commit_sha_filter"),
                    "run_count": ci.get("run_count"),
                    "passed_runs": ci.get("passed_runs"),
                    "failed_runs": ci.get("failed_runs"),
                    "in_progress_runs": ci.get("in_progress_runs"),
                    "runs": ci.get("runs", [])[:10],
                }
        else:
            report["operations"].append({
                "name": "exact_candidate_workflow_and_job_inspection",
                "status": "BLOCKED",
                "error": "No full candidate SHA was supplied to the benchmark.",
            })

        # This uses a deterministic, non-generative writer fixture. It measures
        # AI Infinity's own source-inspection, content-write, branch and PR code,
        # not the intelligence/quality of a language model.
        ok, result = run_operation(
            "self_maintenance_source_to_commit_to_draft_pr",
            lambda: maintenance.improve_repository(
                cfg,
                "Run a temporary, reversible GitHub write-permission benchmark and add a marker comment to the inspected self-maintenance regression test.",
                ["tests/test_project_status_truth_latency.py"],
                _benchmark_model(run_marker),
            ),
        )
        if ok:
            created_pr = int((result.get("pull_request") or {}).get("pull_request_number") or 0) or None
            created_branch = str((result.get("pull_request") or {}).get("branch") or "")
            report["temporary_write_probe"] = {
                "pull_request_number": created_pr,
                "pull_request_url": (result.get("pull_request") or {}).get("pull_request_url"),
                "branch_prefix_valid": created_branch.startswith("ai-infinity/self-maintenance/"),
                "changed_files": result.get("changed_files"),
                "writer_provider": result.get("provider"),
            }

        if created_pr and created_branch:
            ok, pr = run_operation(
                "temporary_pull_request_readback",
                lambda: maintenance.github_request(cfg, "GET", f"/repos/{repo}/pulls/{created_pr}"),
            )
            if ok:
                report["temporary_pull_request_readback"] = {
                    "draft": pr.get("draft") is True,
                    "state": pr.get("state"),
                    "head_branch": (pr.get("head") or {}).get("ref"),
                }
            else:
                report["temporary_pull_request_readback"] = {"status": "unverified"}

        # Always close/delete the temporary resources. Never merge or deploy them.
        if created_pr:
            run_operation(
                "temporary_pull_request_close",
                lambda: maintenance.github_request(cfg, "PATCH", f"/repos/{repo}/pulls/{created_pr}", {"state": "closed"}),
            )
        if created_branch:
            ok, _ = run_operation(
                "temporary_branch_delete_cleanup",
                lambda: maintenance.github_request(cfg, "DELETE", f"/repos/{repo}/git/refs/heads/{created_branch}"),
            )
            if ok:
                created_branch = None

        if created_cross_repository_branch:
            sample_name, probe_branch = created_cross_repository_branch
            ok, _ = run_operation(
                "cross_repository_temporary_branch_delete_cleanup",
                lambda: maintenance.github_request(
                    cfg,
                    "DELETE",
                    f"/repos/{sample_name}/git/refs/heads/{quote(probe_branch, safe='')}",
                ),
            )
            if ok:
                created_cross_repository_branch = None
        else:
            report["operations"].append({
                "name": "cross_repository_temporary_branch_delete_cleanup",
                "status": "BLOCKED",
                "error": "No temporary cross-repository branch was created; no cleanup request was necessary.",
            })

        required_operations = {
            "repository_metadata_and_effective_permissions",
            "authenticated_user_identity",
            "accessible_repositories_all_pages",
            "cross_repository_metadata_read_sample",
            "authenticated_graphql_viewer_read",
            "cross_repository_temporary_branch_create",
            "cross_repository_temporary_branch_delete_cleanup",
            "authenticated_rate_limit_read",
            "repository_wide_recursive_tree_discovery",
            "cross_directory_source_inspection",
            "self_maintenance_source_to_commit_to_draft_pr",
            "temporary_pull_request_readback",
            "temporary_pull_request_close",
            "temporary_branch_delete_cleanup",
        }
        operation_states = {item["name"]: item["status"] for item in report["operations"]}
        report["required_operations"] = {
            name: operation_states.get(name, "NOT_RUN")
            for name in sorted(required_operations)
        }
        exit_code = 0 if all(report["required_operations"][name] == "PASS" for name in required_operations) else 1
    except Exception as exc:
        report["unexpected_error_type"] = type(exc).__name__
        report["unexpected_error"] = str(getattr(exc, "detail", None) or "Unexpected benchmark failure.")[:300]
    finally:
        # Best-effort cleanup in case a later benchmark step raises unexpectedly.
        if created_pr:
            try:
                maintenance.github_request(cfg, "PATCH", f"/repos/{repo}/pulls/{created_pr}", {"state": "closed"})
                report["emergency_cleanup_pr"] = "PASS"
            except Exception:
                report["emergency_cleanup_pr"] = "FAIL"
        if created_branch:
            try:
                maintenance.github_request(cfg, "DELETE", f"/repos/{repo}/git/refs/heads/{created_branch}")
                report["emergency_cleanup_branch"] = "PASS"
            except Exception:
                report["emergency_cleanup_branch"] = "FAIL"
        if created_cross_repository_branch:
            sample_name, probe_branch = created_cross_repository_branch
            try:
                maintenance.github_request(
                    cfg,
                    "DELETE",
                    f"/repos/{sample_name}/git/refs/heads/{quote(probe_branch, safe='')}",
                )
                report["emergency_cleanup_cross_repository_branch"] = "PASS"
            except Exception:
                report["emergency_cleanup_cross_repository_branch"] = "FAIL"
        report["completed_at_epoch"] = time.time()
        report["duration_seconds"] = round(report["completed_at_epoch"] - report["started_at_epoch"], 3)
        passed = sum(op["status"] == "PASS" for op in report["operations"])
        failed = sum(op["status"] == "FAIL" for op in report["operations"])
        blocked = sum(op["status"] == "BLOCKED" for op in report["operations"])
        attempted = passed + failed
        report["operation_pass_count"] = passed
        report["operation_fail_count"] = failed
        report["operation_blocked_count"] = blocked
        report["operation_attempt_count"] = attempted
        report["operation_pass_rate"] = round(passed / attempted, 4) if attempted else None
        report["status"] = (
            "PASS" if exit_code == 0
            else "BLOCKED" if failed == 0 and blocked > 0
            else "FAIL"
        )
        report["token_value_recorded"] = False
        report["secrets_returned"] = False
        report["temporary_pull_request_merged"] = False
        report["production_deployment_performed"] = False
        _save(output, report)

    print(json.dumps({
        "status": report["status"],
        "repository": repo,
        "operation_pass_count": report["operation_pass_count"],
        "operation_fail_count": report["operation_fail_count"],
        "operation_blocked_count": report["operation_blocked_count"],
        "operation_attempt_count": report["operation_attempt_count"],
        "operation_pass_rate": report["operation_pass_rate"],
        "duration_seconds": report["duration_seconds"],
        "repository_permissions": report.get("repository_permissions", {}),
        "required_operations": report.get("required_operations", {}),
        "report": str(output),
    }, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
