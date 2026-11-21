from __future__ import annotations

"""Dispatch and verify a second exact-commit acceptance workflow run."""

import argparse
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from typing import Any, Callable


def _github_json(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "AI-Infinity-Repeat-Acceptance/1.0",
        },
        method=method,
    )
    try:
        with opener(request, timeout=30) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise RuntimeError("GitHub Actions response exceeded the configured size limit.")
        decoded = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(decoded, dict):
            raise RuntimeError("GitHub Actions returned an unexpected response.")
        return decoded
    except HTTPError as exc:
        # Do not include raw provider responses or request headers in CI output.
        raise RuntimeError(f"GitHub Actions API returned HTTP {int(exc.code)}.") from None
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("GitHub Actions API request failed.") from None


def dispatch_and_wait(
    *,
    repository: str,
    workflow: str,
    ref: str,
    candidate_sha: str,
    token: str,
    timeout_seconds: int = 21600,
    poll_seconds: int = 20,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repository):
        raise ValueError("Repository must use owner/name form.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}\.ya?ml", workflow):
        raise ValueError("Workflow must be a YAML filename.")
    if not ref or len(ref) > 240 or ref.startswith("-") or ".." in ref:
        raise ValueError("A valid workflow dispatch branch or tag is required.")
    if not re.fullmatch(r"[0-9a-f]{40}", candidate_sha.lower()):
        raise ValueError("Candidate SHA must be a full 40-character commit SHA.")
    if not token:
        raise ValueError("GITHUB_TOKEN is required to dispatch the independent acceptance run.")
    timeout_seconds = max(60, min(int(timeout_seconds), 21600))
    poll_seconds = max(2, min(int(poll_seconds), 120))

    base = "https://api.github.com/repos/" + repository
    dispatch = _github_json(
        "POST",
        f"{base}/actions/workflows/{quote(workflow, safe='')}/dispatches",
        token,
        {"ref": ref, "inputs": {"acceptance_role": "repeat"}},
        opener,
    )
    run_id = str(dispatch.get("workflow_run_id") or "")
    if not run_id.isdigit():
        raise RuntimeError("GitHub did not return the independent acceptance workflow run ID.")

    deadline = time.monotonic() + timeout_seconds
    last_status = "queued"
    while time.monotonic() < deadline:
        run = _github_json("GET", f"{base}/actions/runs/{run_id}", token, opener=opener)
        if str(run.get("id") or "") != run_id:
            raise RuntimeError("GitHub returned a different workflow run than the dispatched run.")
        if str(run.get("head_sha") or "").lower() != candidate_sha.lower():
            raise RuntimeError("Repeat acceptance checked out a different commit; certification is blocked.")
        if str(run.get("event") or "") != "workflow_dispatch":
            raise RuntimeError("Repeat acceptance was not created as a workflow_dispatch run.")
        last_status = str(run.get("status") or "")
        if last_status == "completed":
            if str(run.get("conclusion") or "") != "success":
                raise RuntimeError("Independent repeat acceptance did not complete successfully.")
            return {
                "workflow_run_id": int(run_id),
                "head_sha": candidate_sha.lower(),
                "status": "completed",
                "conclusion": "success",
                "truthful": True,
            }
        if last_status not in {"queued", "in_progress", "waiting", "requested", "pending"}:
            raise RuntimeError("Repeat acceptance entered an unexpected workflow state.")
        sleeper(poll_seconds)

    raise TimeoutError(
        f"Independent repeat acceptance did not complete within {timeout_seconds} seconds (last status: {last_status})."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=21600)
    parser.add_argument("--poll-seconds", type=int, default=20)
    args = parser.parse_args()
    result = dispatch_and_wait(
        repository=args.repository,
        workflow=args.workflow,
        ref=args.ref,
        candidate_sha=args.candidate_sha,
        token=os.getenv("GITHUB_TOKEN", ""),
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    output_path = os.getenv("GITHUB_OUTPUT", "")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"run_id={result['workflow_run_id']}\n")
    print("REPEAT_ACCEPTANCE_RUN=" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
