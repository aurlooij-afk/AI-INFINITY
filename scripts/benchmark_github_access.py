from __future__ import annotations

"""Measure AI Infinity's real GitHub repository access without touching main.

The benchmark uses the configured maintenance token (or the workflow GITHUB_TOKEN),
then exercises AI Infinity's own discovery, inspection, commit and draft-PR path.
Every write is made on a temporary branch; the draft PR is closed and the branch
is deleted in a finally block. No token or secret value is written to the report.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

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
             and item.get("path") == "tests/test_gemini_writer.py"),
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
            "changes": [{"path": "tests/test_gemini_writer.py", "content": updated}],
        }
        return {"text": json.dumps(result), "provider": "deterministic-github-access-benchmark"}

    return invoke


def _save(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="github-access-benchmark.json")
    args = parser.parse_args()
    output = Path(args.output)

    repo = os.getenv("GITHUB_REPOSITORY") or os.getenv("AI_INFINITY_MAINTENANCE_REPOSITORY", "")
    token = os.getenv("AI_INFINITY_MAINTENANCE_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN", "")
    token_source = os.getenv("AI_INFINITY_MAINTENANCE_GITHUB_TOKEN_SOURCE", "configured_github_token")
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
        "access_mode": "repository_full",
        "scope": "configured_repository_only",
        "started_at_epoch": time.time(),
        "operations": [],
        "status": "RUNNING",
        "truthful": True,
    }
    if not repo or "/" not in repo or not token:
        report.update({
            "status": "BLOCKED",
            "error": "GITHUB_REPOSITORY or GitHub token was not configured.",
        })
        _save(output, report)
        return 2

    os.environ["AI_INFINITY_MAINTENANCE_REPOSITORY"] = repo
    os.environ.setdefault("AI_INFINITY_MAINTENANCE_BASE_BRANCH", "main")
    os.environ["AI_INFINITY_MAINTENANCE_GITHUB_TOKEN"] = token
    os.environ["AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE"] = "repository_full"
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
    exit_code = 1
    try:
        started = time.monotonic()
        metadata = maintenance.github_request(cfg, "GET", f"/repos/{repo}")
        permissions = metadata.get("permissions") if isinstance(metadata.get("permissions"), dict) else {}
        report["repository_permissions"] = {
            key: permissions.get(key)
            for key in ("pull", "triage", "push", "maintain", "admin")
            if key in permissions
        }
        record("repository_metadata_and_effective_permissions", started, "PASS", {
            "default_branch": metadata.get("default_branch"),
            "visibility": metadata.get("visibility"),
        })

        started = time.monotonic()
        rate = maintenance.github_request(cfg, "GET", "/rate_limit")
        core = (rate.get("resources") or {}).get("core") or {}
        report["rate_limit"] = {
            "limit": core.get("limit"),
            "remaining": core.get("remaining"),
            "reset_epoch": core.get("reset"),
        }
        record("authenticated_rate_limit_read", started, "PASS")

        started = time.monotonic()
        goal = (
            "Inspect complete repository source, workflows, tests and execution modules; "
            "measure GitHub-backed self-maintenance discovery and candidate operations."
        )
        discovery = maintenance.discover_sources(cfg, goal, limit=40)
        report["discovery"] = {
            "matched_count": discovery.get("matched_count"),
            "tree_truncated": discovery.get("tree_truncated"),
            "selected_paths": discovery.get("suggested_paths") or [],
            "includes_github_workflows": any(
                str(row.get("path", "")).startswith(".github/workflows/")
                for row in discovery.get("matches", [])
            ),
        }
        record("repository_wide_recursive_tree_discovery", started, "PASS")

        started = time.monotonic()
        inspected = maintenance.inspect_sources(
            cfg,
            [
                ".github/workflows/final-acceptance-suite.yml",
                "self_maintenance.py",
                "tests/test_gemini_writer.py",
                "render.yaml",
            ],
            goal=goal,
        )
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
        record("cross_directory_source_inspection", started, "PASS")

        if head_sha and len(head_sha) == 40:
            started = time.monotonic()
            ci = maintenance.inspect_ci(cfg, commit_sha=head_sha, limit=10)
            report["commit_bound_ci"] = {
                "commit_sha_filter": ci.get("commit_sha_filter"),
                "run_count": ci.get("run_count"),
                "passed_runs": ci.get("passed_runs"),
                "failed_runs": ci.get("failed_runs"),
                "in_progress_runs": ci.get("in_progress_runs"),
                "runs": ci.get("runs", [])[:10],
            }
            record("exact_candidate_workflow_and_job_inspection", started, "PASS")
        else:
            record("exact_candidate_workflow_and_job_inspection", time.monotonic(), "BLOCKED", {
                "reason": "No full candidate SHA was supplied to the benchmark.",
            })

        started = time.monotonic()
        result = maintenance.improve_repository(
            cfg,
            "Run a temporary, reversible GitHub write-permission benchmark and add a marker comment to the inspected self-maintenance regression test.",
            ["tests/test_gemini_writer.py"],
            _benchmark_model(run_marker),
        )
        created_pr = int((result.get("pull_request") or {}).get("pull_request_number") or 0) or None
        created_branch = str((result.get("pull_request") or {}).get("branch") or "")
        report["temporary_write_probe"] = {
            "pull_request_number": created_pr,
            "pull_request_url": (result.get("pull_request") or {}).get("pull_request_url"),
            "branch_prefix_valid": created_branch.startswith("ai-infinity/self-maintenance/"),
            "changed_files": result.get("changed_files"),
            "writer_provider": result.get("provider"),
        }
        record("self_maintenance_source_to_commit_to_draft_pr", started, "PASS")

        if not created_pr or not created_branch:
            raise RuntimeError("self-maintenance did not return a verifiable temporary PR and branch")

        started = time.monotonic()
        pr = maintenance.github_request(cfg, "GET", f"/repos/{repo}/pulls/{created_pr}")
        record("temporary_pull_request_readback", started, "PASS", {
            "draft": pr.get("draft") is True,
            "state": pr.get("state"),
            "head_branch": (pr.get("head") or {}).get("ref"),
        })

        # The test PR is never merged. Close it and delete the temporary branch.
        started = time.monotonic()
        maintenance.github_request(cfg, "PATCH", f"/repos/{repo}/pulls/{created_pr}", {"state": "closed"})
        record("temporary_pull_request_close", started, "PASS")

        started = time.monotonic()
        maintenance.github_request(
            cfg, "DELETE",
            f"/repos/{repo}/git/refs/heads/{created_branch}",
        )
        created_branch = None
        record("temporary_branch_delete_cleanup", started, "PASS")
        created_pr = None
        exit_code = 0
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["error"] = str(getattr(exc, "detail", None) or "A GitHub operation failed; inspect the operation results.")
        if created_pr:
            try:
                maintenance.github_request(cfg, "PATCH", f"/repos/{repo}/pulls/{created_pr}", {"state": "closed"})
                record("failure_cleanup_close_pull_request", time.monotonic(), "PASS")
            except Exception:
                record("failure_cleanup_close_pull_request", time.monotonic(), "FAIL")
        if created_branch:
            try:
                maintenance.github_request(cfg, "DELETE", f"/repos/{repo}/git/refs/heads/{created_branch}")
                record("failure_cleanup_delete_branch", time.monotonic(), "PASS")
            except Exception:
                record("failure_cleanup_delete_branch", time.monotonic(), "FAIL")
    finally:
        report["completed_at_epoch"] = time.time()
        report["duration_seconds"] = round(report["completed_at_epoch"] - report["started_at_epoch"], 3)
        passed = sum(op["status"] == "PASS" for op in report["operations"])
        attempted = sum(op["status"] != "BLOCKED" for op in report["operations"])
        report["operation_pass_count"] = passed
        report["operation_attempt_count"] = attempted
        report["operation_pass_rate"] = round(passed / attempted, 4) if attempted else None
        report["status"] = "PASS" if exit_code == 0 else "FAIL"
        report["token_value_recorded"] = False
        report["secrets_returned"] = False
        _save(output, report)

    print(json.dumps({
        "status": report["status"],
        "repository": repo,
        "operation_pass_count": report["operation_pass_count"],
        "operation_attempt_count": report["operation_attempt_count"],
        "operation_pass_rate": report["operation_pass_rate"],
        "duration_seconds": report["duration_seconds"],
        "repository_permissions": report.get("repository_permissions", {}),
        "report": str(output),
    }, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
