from __future__ import annotations

"""Guarded self-inspection and draft-PR-based repository self-improvement."""

import ast
import base64
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Request as FastAPIRequest

MAX_FILES = 8
MAX_FILE_BYTES = 40_000
MAX_TOTAL_BYTES = 120_000
MAX_CHANGE_BYTES = 60_000
ROOT_MODULES = {
    "main.py", "studio_ultimate.py", "ai_infinity_canonical.py",
    "production_graph.py", "production_intelligence.py",
    "production_closure_3624.py", "reality_first_3901.py",
    "professional_creator_v2.py", "professional_creator_v2_timeline_patch.py",
    "professional_creator_fabric.py", "universal_command_batch.py",
    "wan_video_worker.py",
}
BLOCKED_FILES = {
    ".env", ".env.local", ".env.production", ".session-key",
    "AI_Infinity_Creator_Benchmark_and_Reuse_Plan.md",
    "AI_Infinity_10_Method_Deep_Research_and_Master_Command.md",
}
SECRET_LITERAL = re.compile(
    r"(?im)^\s*(?:GEMINI_API_KEY|OPENAI_API_KEY|API_KEY|ACCESS_TOKEN|APP_TOKEN|GITHUB_TOKEN|PASSWORD|SECRET)\s*=\s*['\"][^'\"]{20,}['\"]"
)


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> dict:
    repository = os.getenv("AI_INFINITY_MAINTENANCE_REPOSITORY", "").strip()
    base_branch = os.getenv("AI_INFINITY_MAINTENANCE_BASE_BRANCH", "main").strip()
    github_token = os.getenv("AI_INFINITY_MAINTENANCE_GITHUB_TOKEN", "").strip()
    admin_token = os.getenv("AI_INFINITY_MAINTENANCE_ADMIN_TOKEN", "").strip()
    repo_valid = re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repository) is not None
    branch_valid = re.fullmatch(r"[A-Za-z0-9_./-]{1,120}", base_branch) is not None and ".." not in base_branch
    enabled = _enabled("AI_INFINITY_SELF_MAINTENANCE_ENABLED")
    return {
        "enabled": enabled,
        "repository": repository if repo_valid else "",
        "base_branch": base_branch if branch_valid else "",
        "github_token": github_token,
        "admin_token": admin_token,
        "ready": bool(enabled and repo_valid and branch_valid and github_token and admin_token),
    }


def authorize(request: FastAPIRequest, cfg: dict) -> None:
    if not cfg["ready"]:
        raise HTTPException(503, "Self-maintenance is disabled or missing repository, GitHub, or admin configuration.")
    supplied = str(request.headers.get("x-ai-infinity-maintenance-token") or "")
    if not hmac.compare_digest(supplied, cfg["admin_token"]):
        raise HTTPException(403, "Self-maintenance authorization failed.")


def validate_path(value: object) -> str:
    path = str(value or "").strip().replace("\\", "/")
    parts = path.split("/")
    if not path or len(path) > 240 or path.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(422, "Invalid repository path.")
    if any(part.startswith(".") for part in parts) or parts[-1] in BLOCKED_FILES or parts[-1].lower().startswith(".env"):
        raise HTTPException(403, "Hidden, protected, and secret-bearing files are excluded.")
    allowed = path in ROOT_MODULES or path.startswith("ai_infinity/") or path.startswith("tests/")
    if not allowed or not path.endswith(".py"):
        raise HTTPException(403, "Only allowlisted Python source and test files are available.")
    return path


def github_request(cfg: dict, method: str, path: str, payload: dict | None = None) -> dict:
    if not path.startswith("/repos/"):
        raise ValueError("Unsupported GitHub API path.")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        "https://api.github.com" + path,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + cfg["github_token"],
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "AI-Infinity-Self-Maintenance/1.0",
        },
        method=method.upper(),
    )
    try:
        with urlopen(request, timeout=25) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise RuntimeError("GitHub response exceeded the size limit.")
        value = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(value, dict):
            raise RuntimeError("Unexpected GitHub API response.")
        return value
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise HTTPException(503, "GitHub maintenance permission is insufficient or expired.") from None
        if exc.code == 404:
            raise HTTPException(404, "GitHub repository resource was not found.") from None
        raise HTTPException(502, f"GitHub request failed ({exc.code}).") from None
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(502, "GitHub API request could not be completed.") from None


def read_source(cfg: dict, path: str) -> dict:
    ref = quote(cfg["base_branch"], safe="")
    result = github_request(cfg, "GET", f"/repos/{cfg['repository']}/contents/{quote(path, safe='/')}?ref={ref}")
    if result.get("type") != "file" or result.get("encoding") != "base64":
        raise HTTPException(422, f"{path} is not a readable text file.")
    try:
        raw = base64.b64decode(str(result.get("content") or "").replace("\n", ""))
        content = raw.decode("utf-8")
    except Exception:
        raise HTTPException(422, f"{path} is not UTF-8 text.") from None
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(413, f"{path} exceeds the source-size limit.")
    return {
        "path": path, "sha": str(result.get("sha") or ""),
        "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "content": content,
    }


def inspect_sources(cfg: dict, paths: list) -> dict:
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES:
        raise HTTPException(422, f"Specify between one and {MAX_FILES} source paths.")
    selected = [validate_path(path) for path in paths]
    if len(selected) != len(set(selected)):
        raise HTTPException(422, "Source paths must be unique.")
    files = [read_source(cfg, path) for path in selected]
    total = sum(row["size_bytes"] for row in files)
    if total > MAX_TOTAL_BYTES:
        raise HTTPException(413, "Source set exceeds the aggregate size limit.")
    return {
        "repository": cfg["repository"], "base_branch": cfg["base_branch"],
        "files": files, "total_source_bytes": total, "truthful": True,
    }


def free_tier_writer():
    try:
        import studio_ultimate as studio
        if studio._external_providers_disabled():
            raise HTTPException(503, "External provider use is disabled by runtime policy.")
        model = studio._gemini_free_tier_text_model()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Creator Studio writer adapter is unavailable.") from None
    if not callable(model):
        raise HTTPException(503, "An explicitly acknowledged Gemini Free Tier writer is required.")
    return model


def generate_changes(goal: str, files: list, model_fn) -> dict:
    prompt = (
        "Act as the principal engineer for AI Infinity. Implement the requested goal with the smallest safe code change. "
        "Return only valid JSON with keys summary and changes. Each change must have path and complete replacement content. "
        "Only change supplied paths and include at least one regression test. Preserve unrelated behavior. Do not add secrets, "
        "paid dependencies, hidden network calls, arbitrary shell execution, rights/security/human-review bypasses, main writes, "
        "merges, or deployments. If unsafe, return an empty changes list and explain why. INPUT: "
        + json.dumps({"goal": goal, "files": [{"path": row["path"], "content": row["content"]} for row in files]}, ensure_ascii=False)
    )
    try:
        raw = model_fn({"messages": [{"role": "user", "content": prompt}], "temperature": 0.2}) or {}
        answer = str(raw.get("text") or raw.get("answer") or "").strip()
        if len(answer) > 300_000:
            raise ValueError("writer output too large")
        result = json.loads(answer)
    except Exception:
        raise HTTPException(502, "Free-tier writer returned invalid bounded JSON.") from None
    if not isinstance(result, dict) or not isinstance(result.get("changes"), list):
        raise HTTPException(502, "Writer returned an invalid change set.")
    result["provider"] = str(raw.get("provider") or "configured-free-tier-writer")
    return result


def validate_changes(changes: list, requested_paths: set[str]) -> list:
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_FILES:
        raise HTTPException(422, "Proposed change set is empty or exceeds the file limit.")
    output = []
    for row in changes:
        if not isinstance(row, dict):
            raise HTTPException(422, "Malformed proposed file change.")
        path = validate_path(row.get("path"))
        if path not in requested_paths:
            raise HTTPException(403, "Writer attempted to change an uninspected file.")
        content = row.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_CHANGE_BYTES:
            raise HTTPException(413, f"Proposed content for {path} is invalid or too large.")
        if SECRET_LITERAL.search(content):
            raise HTTPException(422, f"Potential hard-coded secret detected in {path}.")
        try:
            ast.parse(content, filename=path)
        except SyntaxError as exc:
            raise HTTPException(422, f"Python syntax error in {path} at line {exc.lineno}.") from None
        output.append({"path": path, "content": content})
    if len({row["path"] for row in output}) != len(output):
        raise HTTPException(422, "Duplicate paths in proposed changes.")
    if not any(row["path"].startswith("tests/") for row in output):
        raise HTTPException(422, "A self-improvement change must include a regression-test change.")
    if sum(len(row["content"].encode("utf-8")) for row in output) > MAX_TOTAL_BYTES:
        raise HTTPException(413, "Proposed change set exceeds the aggregate size limit.")
    return output


def create_draft_pr(cfg: dict, changes: list, originals: dict, goal: str, summary: str) -> dict:
    repository = cfg["repository"]
    base = cfg["base_branch"]
    branch = "ai-infinity/self-maintenance/" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    ref = github_request(cfg, "GET", f"/repos/{repository}/git/ref/heads/{quote(base, safe='')}")
    base_sha = str((ref.get("object") or {}).get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise HTTPException(502, "GitHub did not return a valid base commit SHA.")
    github_request(cfg, "POST", f"/repos/{repository}/git/refs", {"ref": "refs/heads/" + branch, "sha": base_sha})
    for change in changes:
        original = originals.get(change["path"])
        if not original or not original.get("sha"):
            raise HTTPException(409, f"Original SHA unavailable for {change['path']}.")
        github_request(cfg, "PUT", f"/repos/{repository}/contents/{quote(change['path'], safe='/')}", {
            "message": "feat(self-maintenance): " + re.sub(r"\s+", " ", goal)[:100],
            "content": base64.b64encode(change["content"].encode("utf-8")).decode("ascii"),
            "sha": original["sha"], "branch": branch,
        })
    pr = github_request(cfg, "POST", f"/repos/{repository}/pulls", {
        "title": "[AI Infinity self-maintenance] " + re.sub(r"\s+", " ", goal)[:150],
        "body": (
            "## Automated engineering proposal\n\n" + summary[:4000]
            + "\n\n## Files\n" + "\n".join("- " + row["path"] for row in changes)
            + "\n\n## Verification\n- Draft PR only; GitHub Actions must verify this candidate.\n"
            + "- No main write, merge, deployment, publishing, or human approval is performed.\n"
            + "- Review the diff and CI evidence before merging.\n"
        ),
        "head": branch, "base": base, "draft": True,
    })
    return {
        "branch": branch, "base_sha": base_sha,
        "pull_request_number": pr.get("number"), "pull_request_url": pr.get("html_url"),
        "draft": pr.get("draft") is True,
    }


def improve_repository(cfg: dict, goal: str, paths: list, model_fn) -> dict:
    goal = re.sub(r"\s+", " ", str(goal or "")).strip()
    if not 12 <= len(goal) <= 2000:
        raise HTTPException(422, "Provide a specific engineering goal between 12 and 2,000 characters.")
    inspected = inspect_sources(cfg, paths)
    originals = {row["path"]: row for row in inspected["files"]}
    generated = generate_changes(goal, inspected["files"], model_fn)
    changes = validate_changes(generated.get("changes"), set(originals))
    pr = create_draft_pr(cfg, changes, originals, goal, str(generated.get("summary") or "Automated code proposal."))
    return {
        "status": "draft_pr_created", "goal": goal,
        "summary": str(generated.get("summary") or "")[:4000],
        "provider": generated.get("provider"),
        "changed_files": [row["path"] for row in changes],
        "pull_request": pr, "ci_verification": "pending",
        "merge_performed": False, "deployment_performed": False, "truthful": True,
    }


def register(app: FastAPI) -> None:
    @app.get("/infinity/studio/self-maintenance/status")
    def maintenance_status(request: FastAPIRequest):
        cfg = load_settings()
        authorize(request, cfg)
        try:
            writer_ready = callable(free_tier_writer())
        except HTTPException:
            writer_ready = False
        return {
            "enabled": cfg["enabled"], "ready": cfg["ready"],
            "repository": cfg["repository"] or None, "base_branch": cfg["base_branch"] or None,
            "github_write_adapter": bool(cfg["github_token"]),
            "free_tier_code_writer_ready": writer_ready,
            "execution_model": "source inspection + draft GitHub PR + GitHub Actions CI",
            "direct_main_writes": False, "arbitrary_shell_execution": False,
            "automatic_merge": False, "automatic_deploy": False, "truthful": True,
        }

    @app.post("/infinity/studio/self-maintenance/inspect")
    async def maintenance_inspect(request: FastAPIRequest):
        cfg = load_settings()
        authorize(request, cfg)
        payload = await request.json()
        return inspect_sources(cfg, payload.get("paths") if isinstance(payload, dict) else None)

    @app.post("/infinity/studio/self-maintenance/improve")
    async def maintenance_improve(request: FastAPIRequest):
        cfg = load_settings()
        authorize(request, cfg)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, "A JSON object is required.")
        return improve_repository(cfg, payload.get("goal"), payload.get("paths"), free_tier_writer())
