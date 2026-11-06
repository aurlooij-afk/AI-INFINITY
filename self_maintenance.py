from __future__ import annotations

"""Guarded self-inspection and draft-PR-based repository self-improvement."""

import ast
import base64
import hashlib
import hmac
import json
import os
import platform
import shutil
import sys
import re
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Request as FastAPIRequest

MAX_FILES = 8
# GitHub's Contents API supports source files up to 1 MB. Large modules are read
# internally but only bounded, hash-addressed function windows enter model context.
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 2_000_000
MAX_CONTEXT_FILE_BYTES = 28_000
MAX_TOTAL_CONTEXT_BYTES = 80_000
MAX_CHANGE_BYTES = 60_000
MAX_WINDOW_LINES = 240
MAX_WINDOW_COUNT = 10
ROOT_MODULES = {
    "main.py", "studio_ultimate.py", "ai_infinity_canonical.py",
    "production_graph.py", "production_intelligence.py",
    "production_closure_3624.py", "reality_first_3901.py",
    "professional_creator_v2.py", "professional_creator_v2_timeline_patch.py",
    "professional_creator_fabric.py", "universal_command_batch.py",
    "wan_video_worker.py", "self_maintenance.py",
}
ROOT_TEXT_FILES = {"creator_studio_2030.html", "ui_3800.html"}
BLOCKED_FILES = {
    ".env", ".env.local", ".env.production", ".session-key",
    "AI_Infinity_Creator_Benchmark_and_Reuse_Plan.md",
    "AI_Infinity_10_Method_Deep_Research_and_Master_Command.md",
}
SECRET_ASSIGNMENT = re.compile(
    r"""(?im)^\s*(?:(?:export|const|let|var)\s+)?"""
    r"""(?:[A-Z0-9_]*(?:API[_-]?KEY|ACCESS[_-]?KEY|APPLICATION[_-]?KEY|"""
    r"""SERVICE[_-]?ROLE[_-]?KEY|PRIVATE[_-]?KEY)[A-Z0-9_]*)"""
    r"""\s*[:=]\s*(['"])([^'"\r\n]{16,})\1"""
)
GENERIC_CREDENTIAL_ASSIGNMENT = re.compile(
    r"""(?im)^\s*(?:(?:export|const|let|var)\s+)?"""
    r"""(?:[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*)"""
    r"""\s*[:=]\s*(['"])([^'"\r\n]{28,})\1"""
)
SECRET_ENV_ASSIGNMENT = re.compile(
    r"""(?im)(?:os\.)?environ\s*\[\s*['"]"""
    r"""[A-Z0-9_]*(?:API[_-]?KEY|ACCESS[_-]?KEY|APPLICATION[_-]?KEY|"""
    r"""SERVICE[_-]?ROLE[_-]?KEY|PRIVATE[_-]?KEY)[A-Z0-9_]*"""
    r"""['"]\s*\]\s*=\s*(['"])([^'"\r\n]{16,})\1"""
)
GENERIC_ENV_CREDENTIAL_ASSIGNMENT = re.compile(
    r"""(?im)(?:os\.)?environ\s*\[\s*['"]"""
    r"""[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*"""
    r"""['"]\s*\]\s*=\s*(['"])([^'"\r\n]{28,})\1"""
)
KNOWN_TOKEN_FORMAT = re.compile(
    r"""(?i)(?:\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"""
    r"""|\bgh[pousr]_[A-Za-z0-9]{20,}\b"""
    r"""|\bgithub_pat_[A-Za-z0-9_]{20,}\b"""
    r"""|\bhf_[A-Za-z0-9]{20,}\b"""
    r"""|\bxox[baprs]-[A-Za-z0-9-]{20,}\b"""
    r"""|\bAKIA[0-9A-Z]{16}\b"""
    r"""|\bAIza[0-9A-Za-z_-]{30,}\b)"""
)


def contains_embedded_secret(content: str) -> bool:
    """Reject likely hard-coded credentials by assignment name or known token shape."""
    text = str(content or "")
    return bool(
        SECRET_ASSIGNMENT.search(text)
        or GENERIC_CREDENTIAL_ASSIGNMENT.search(text)
        or SECRET_ENV_ASSIGNMENT.search(text)
        or GENERIC_ENV_CREDENTIAL_ASSIGNMENT.search(text)
        or KNOWN_TOKEN_FORMAT.search(text)
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
    if path in ROOT_TEXT_FILES:
        return path
    allowed = (
        path in ROOT_MODULES
        or ("/" not in path and path.endswith(".py"))
        or path.startswith("ai_infinity/")
        or path.startswith("tests/")
        or path.startswith("scripts/")
    )
    if not allowed or not path.endswith(".py"):
        raise HTTPException(403, "Only allowlisted root Python, application, script, test, and UI files are available.")
    return path


def rank_source_candidates(paths: list[str], goal: str, limit: int = 20) -> list[dict]:
    """Rank allowlisted Python source paths using goal terms without reading file bodies."""
    tokens = list(dict.fromkeys(
        token.casefold()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", str(goal or ""))
        if len(token) >= 3
    ))
    ranked = []
    for row in paths:
        path = str(row.get("path") or "")
        try:
            validate_path(path)
        except HTTPException:
            continue
        lowered = path.casefold()
        score = sum(4 for token in tokens if token in lowered)
        if path in ROOT_MODULES:
            score += 1
        # Keep tests discoverable even when the user's goal omits the word "test".
        # Topic-specific filename matches still outrank these general test candidates.
        if path.startswith("tests/"):
            score += 2
        if score:
            ranked.append({
                "path": path,
                "size_bytes": int(row.get("size") or 0),
                "sha": str(row.get("sha") or ""),
                "match_score": score,
            })
    ranked.sort(key=lambda row: (-row["match_score"], row["path"]))
    try:
        requested_limit = int(limit or 20)
    except (TypeError, ValueError):
        raise HTTPException(422, "Discovery limit must be an integer between 1 and 40.") from None
    if requested_limit < 1 or requested_limit > 40:
        raise HTTPException(422, "Discovery limit must be between 1 and 40.")
    return ranked[:requested_limit]


def discover_sources(cfg: dict, goal: str, limit: int = 20) -> dict:
    goal = re.sub(r"\s+", " ", str(goal or "")).strip()
    if not 12 <= len(goal) <= 2000:
        raise HTTPException(422, "Provide a specific source-discovery goal between 12 and 2,000 characters.")
    tree_ref = quote(cfg["base_branch"], safe="")
    response = github_request(
        cfg, "GET",
        f"/repos/{cfg['repository']}/git/trees/{tree_ref}?recursive=1",
    )
    entries = response.get("tree") if isinstance(response.get("tree"), list) else []
    candidates = rank_source_candidates(
        [row for row in entries if isinstance(row, dict) and row.get("type") == "blob"],
        goal,
        limit,
    )
    try:
        suggested_paths = select_autonomous_sources(candidates)
    except HTTPException:
        suggested_paths = []
    return {
        "repository": cfg["repository"],
        "base_branch": cfg["base_branch"],
        "goal": goal,
        "matches": candidates,
        "matched_count": len(candidates),
        "suggested_paths": suggested_paths,
        "tree_truncated": response.get("truncated") is True,
        "truthful": True,
    }


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
        raise HTTPException(413, f"{path} exceeds the {MAX_FILE_BYTES}-byte source limit.")
    return {
        "path": path, "sha": str(result.get("sha") or ""),
        "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "content": content,
    }


def _range_digest(lines: list[str], start_line: int, end_line: int) -> str:
    block = "\n".join(lines[start_line - 1:end_line]) + "\n"
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def _bounded_source_context(content: str, goal: str, path: str) -> tuple[str, list[dict], str]:
    lines = content.splitlines()
    if not lines:
        raise HTTPException(422, f"{path} is empty and cannot be safely revised.")
    if len(content.encode("utf-8")) <= MAX_CONTEXT_FILE_BYTES:
        editable = [{
            "start_line": 1,
            "end_line": len(lines),
            "sha256": _range_digest(lines, 1, len(lines)),
        }]
        return content, editable, "full_file"

    terms = list(dict.fromkeys(
        token.casefold()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", str(goal or ""))
        if len(token) >= 4
    ))
    if not terms:
        raise HTTPException(422, f"{path} is larger than the direct-inspection limit; supply a specific inspection goal.")

    hits = []
    for number, line in enumerate(lines, 1):
        lowered = line.casefold()
        score = sum(1 for term in terms if term in lowered)
        if score:
            hits.append((score, number))
    if not hits:
        raise HTTPException(422, f"No source window in {path} matched the requested engineering goal.")

    enclosing_nodes = []
    try:
        tree = ast.parse(content, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and getattr(node, "end_lineno", None):
                enclosing_nodes.append((int(node.lineno), int(node.end_lineno), node))
    except SyntaxError:
        # A currently broken file can still be inspected; subsequent complete-file
        # syntax validation will prevent a proposed repair from making it worse.
        enclosing_nodes = []

    candidates = []
    for score, number in sorted(hits, key=lambda row: (-row[0], row[1])):
        containers = [row for row in enclosing_nodes if row[0] <= number <= row[1]]
        if containers:
            chosen = min(containers, key=lambda row: row[1] - row[0])
            first, last = chosen[0], chosen[1]
        else:
            first = max(1, number - 18)
            last = min(len(lines), number + 18)
        if last - first + 1 > MAX_WINDOW_LINES:
            first = max(1, number - MAX_WINDOW_LINES // 2)
            last = min(len(lines), first + MAX_WINDOW_LINES - 1)
        candidates.append((score, first, last))

    selected = []
    seen = set()
    for score, first, last in candidates:
        key = (first, last)
        if key in seen:
            continue
        seen.add(key)
        if any(not (last < old_first or first > old_last) for old_first, old_last in selected):
            continue
        block_bytes = len("\n".join(lines[first - 1:last]).encode("utf-8"))
        if block_bytes > 18_000:
            continue
        selected.append((first, last))
        if len(selected) >= MAX_WINDOW_COUNT:
            break

    # Imports and module-level constants are read-only context; edits are restricted
    # to the exact, hash-pinned function/class blocks listed below.
    sections = ["REFERENCE CONTEXT (read-only; do not replace these lines):"]
    for number, line in enumerate(lines[:min(45, len(lines))], 1):
        sections.append(f"{number:06d}: {line}")
    editable_ranges = []
    for index, (first, last) in enumerate(sorted(selected), 1):
        digest = _range_digest(lines, first, last)
        editable_ranges.append({
            "start_line": first,
            "end_line": last,
            "sha256": digest,
        })
        sections.append(
            f"\n--- EDITABLE RANGE {index}: lines {first}-{last}; sha256={digest} ---"
        )
        sections.extend(f"{number:06d}: {lines[number - 1]}" for number in range(first, last + 1))
        sections.append(f"--- END EDITABLE RANGE {index} ---")
    context = "\n".join(sections)
    if not editable_ranges or len(context.encode("utf-8")) > MAX_TOTAL_CONTEXT_BYTES:
        raise HTTPException(413, f"Relevant source windows for {path} exceed the bounded inspection budget.")
    return context, editable_ranges, "line_ranges"


def inspect_sources(cfg: dict, paths: list, goal: str = "") -> dict:
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES:
        raise HTTPException(422, f"Specify between one and {MAX_FILES} source paths.")
    selected = [validate_path(path) for path in paths]
    if len(selected) != len(set(selected)):
        raise HTTPException(422, "Source paths must be unique.")
    files = []
    total_source_bytes = 0
    total_context_bytes = 0
    for path in selected:
        source = read_source(cfg, path)
        total_source_bytes += source["size_bytes"]
        context, editable_ranges, content_mode = _bounded_source_context(
            source["content"], goal, path
        )
        total_context_bytes += len(context.encode("utf-8"))
        files.append({
            "path": path,
            "sha": source["sha"],
            "sha256": source["sha256"],
            "size_bytes": source["size_bytes"],
            "line_count": len(source["content"].splitlines()),
            "content_mode": content_mode,
            "editable_ranges": editable_ranges,
            "content": context,
        })
    if total_source_bytes > MAX_TOTAL_BYTES:
        raise HTTPException(413, "Source set exceeds the aggregate source-size limit.")
    if total_context_bytes > MAX_TOTAL_CONTEXT_BYTES:
        raise HTTPException(413, "Source set exceeds the aggregate model-context limit.")
    return {
        "repository": cfg["repository"], "base_branch": cfg["base_branch"],
        "files": files,
        "total_source_bytes": total_source_bytes,
        "total_context_bytes": total_context_bytes,
        "truthful": True,
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
        "Return only valid JSON with keys summary and changes. Include at least one regression-test change. "
        "For Python source and the two allowlisted UI HTML files, when content_mode is full_file, return {path, content} with the complete replacement file. "
        "For a file whose content_mode is line_ranges, return {path, edits:[{start_line,end_line,expected_sha256,replacement_text}]}. "
        "A line_ranges edit MUST replace one entire listed editable range, using its exact start_line, end_line and sha256. "
        "Never edit reference-only lines, invent line numbers, or change a range whose hash was not supplied. "
        "Only change supplied paths. Preserve unrelated behavior. Do not add secrets, paid dependencies, hidden network calls, "
        "arbitrary shell execution, rights/security/human-review bypasses, main writes, merges, or deployments. "
        "Keep each replacement block under 60 KB. If unsafe or the evidence is insufficient, return an empty changes list. INPUT: "
        + json.dumps({"goal": goal, "files": files}, ensure_ascii=False)
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


def apply_window_edits(changes: list, originals: dict, inspected_files: list) -> list:
    """Expand hash-pinned range edits into complete source files without regenerating whole modules."""
    inspected = {row["path"]: row for row in inspected_files}
    if not isinstance(changes, list):
        raise HTTPException(422, "Malformed self-maintenance change set.")
    output = []
    total_replacement_bytes = 0
    for change in changes:
        if not isinstance(change, dict):
            raise HTTPException(422, "Malformed proposed file change.")
        path = validate_path(change.get("path"))
        if path not in inspected or path not in originals:
            raise HTTPException(403, "Writer attempted to modify a file that was not inspected.")
        if "content" in change:
            output.append({"path": path, "content": change.get("content")})
            continue
        edits = change.get("edits")
        if not isinstance(edits, list) or not edits or len(edits) > MAX_WINDOW_COUNT:
            raise HTTPException(422, f"{path} requires one or more bounded line-range edits.")
        source = originals[path]["content"]
        source_lines = source.splitlines()
        newline = "\r\n" if "\r\n" in source else "\n"
        final_newline = source.endswith(("\n", "\r"))
        allowed = {
            (int(row["start_line"]), int(row["end_line"])): str(row["sha256"])
            for row in inspected[path].get("editable_ranges", [])
        }
        normalized = []
        for edit in edits:
            if not isinstance(edit, dict):
                raise HTTPException(422, f"Malformed range edit for {path}.")
            try:
                first = int(edit.get("start_line"))
                last = int(edit.get("end_line"))
            except (TypeError, ValueError):
                raise HTTPException(422, f"Invalid line range for {path}.") from None
            digest = str(edit.get("expected_sha256") or "")
            replacement = edit.get("replacement_text")
            if (first, last) not in allowed or allowed[(first, last)] != digest:
                raise HTTPException(409, f"Edit for {path} is outside inspected, hash-pinned source ranges.")
            if not 1 <= first <= last <= len(source_lines):
                raise HTTPException(422, f"Edit for {path} contains an invalid line range.")
            if _range_digest(source_lines, first, last) != digest:
                raise HTTPException(409, f"Source range for {path} changed after inspection.")
            if not isinstance(replacement, str) or len(replacement.encode("utf-8")) > MAX_CHANGE_BYTES:
                raise HTTPException(413, f"Replacement block for {path} is invalid or too large.")
            if contains_embedded_secret(replacement):
                raise HTTPException(422, f"Potential hard-coded secret detected in {path}.")
            total_replacement_bytes += len(replacement.encode("utf-8"))
            normalized.append((first, last, replacement))
        if total_replacement_bytes > MAX_CHANGE_BYTES:
            raise HTTPException(413, "Aggregate source-window replacement exceeds the change-size limit.")
        ordered = sorted(normalized, key=lambda item: item[0])
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] <= previous[1]:
                raise HTTPException(422, f"Overlapping source-window edits are not allowed for {path}.")
        updated_lines = list(source_lines)
        for first, last, replacement in sorted(normalized, key=lambda item: item[0], reverse=True):
            updated_lines[first - 1:last] = replacement.splitlines()
        updated = newline.join(updated_lines) + (newline if final_newline else "")
        output.append({"path": path, "content": updated, "_bounded_edit": True})
    return output


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
        content_limit = MAX_FILE_BYTES if row.get("_bounded_edit") is True else MAX_CHANGE_BYTES
        if not isinstance(content, str) or len(content.encode("utf-8")) > content_limit:
            raise HTTPException(413, f"Proposed content for {path} is invalid or too large.")
        if contains_embedded_secret(content):
            raise HTTPException(422, f"Potential hard-coded secret detected in {path}.")
        if path.endswith(".py"):
            try:
                ast.parse(content, filename=path)
            except SyntaxError as exc:
                raise HTTPException(422, f"Python syntax error in {path} at line {exc.lineno}.") from None
        elif path.endswith(".html"):
            try:
                from html.parser import HTMLParser
                parser = HTMLParser(convert_charrefs=True)
                parser.feed(content)
                parser.close()
            except Exception:
                raise HTTPException(422, f"HTML structure validation failed for {path}.") from None
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


def summarize_ci_runs(runs: list[dict], jobs_by_run: dict[int, list[dict]], commit_sha: str | None = None) -> dict:
    """Summarize GitHub Actions outcomes without exposing raw logs or secret-bearing output."""
    output = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        sha = str(run.get("head_sha") or "")
        if commit_sha and sha != commit_sha:
            continue
        run_id = int(run.get("id") or 0)
        jobs = jobs_by_run.get(run_id, [])
        failed_jobs = []
        active_jobs = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_status = str(job.get("status") or "")
            conclusion = str(job.get("conclusion") or "")
            failed_steps = [
                str(step.get("name") or "unnamed step")
                for step in (job.get("steps") or [])
                if isinstance(step, dict) and str(step.get("conclusion") or "").lower() == "failure"
            ]
            if conclusion == "failure":
                failed_jobs.append({
                    "name": str(job.get("name") or "unnamed job"),
                    "failed_steps": failed_steps,
                })
            elif job_status not in {"completed"}:
                active_jobs.append(str(job.get("name") or "unnamed job"))
        output.append({
            "run_id": run_id,
            "name": str(run.get("name") or ""),
            "head_sha": sha,
            "branch": str(run.get("head_branch") or ""),
            "status": str(run.get("status") or ""),
            "conclusion": run.get("conclusion"),
            "html_url": str(run.get("html_url") or ""),
            "failed_jobs": failed_jobs,
            "active_jobs": active_jobs,
            "truthful": True,
        })
    return {
        "commit_sha_filter": commit_sha,
        "run_count": len(output),
        "runs": output,
        "raw_logs_included": False,
        "truthful": True,
    }


def inspect_ci(cfg: dict, commit_sha: str | None = None, limit: int = 10) -> dict:
    if commit_sha and not re.fullmatch(r"[0-9a-f]{40}", str(commit_sha)):
        raise HTTPException(422, "Commit SHA must be a full 40-character Git SHA.")
    try:
        requested_limit = int(limit or 10)
    except (TypeError, ValueError):
        raise HTTPException(422, "CI run limit must be an integer between 1 and 20.") from None
    if not 1 <= requested_limit <= 20:
        raise HTTPException(422, "CI run limit must be between 1 and 20.")
    selector = (
        f"head_sha={quote(commit_sha, safe='')}"
        if commit_sha
        else f"branch={quote(cfg['base_branch'], safe='')}"
    )
    query = f"/repos/{cfg['repository']}/actions/runs?{selector}&per_page={requested_limit}"
    response = github_request(cfg, "GET", query)
    runs = response.get("workflow_runs") if isinstance(response.get("workflow_runs"), list) else []
    jobs_by_run = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        run_id = int(run.get("id") or 0)
        if not run_id:
            continue
        job_response = github_request(
            cfg, "GET",
            f"/repos/{cfg['repository']}/actions/runs/{run_id}/jobs?per_page=100",
        )
        jobs_by_run[run_id] = job_response.get("jobs") if isinstance(job_response.get("jobs"), list) else []
    summary = summarize_ci_runs(runs, jobs_by_run, commit_sha)
    summary.update({
        "repository": cfg["repository"],
        "base_branch": cfg["base_branch"],
        "queried_run_count": len(runs),
        "truthful": True,
    })
    return summary


def select_autonomous_sources(matches: list[dict], max_code: int = 4, max_tests: int = 3) -> list[str]:
    """Select relevant application and regression-test paths from repository discovery."""
    code_paths = []
    test_paths = []
    for row in matches:
        path = str(row.get("path") or "")
        try:
            validate_path(path)
        except HTTPException:
            continue
        if path.startswith("tests/"):
            if path not in test_paths:
                test_paths.append(path)
        elif path not in code_paths:
            code_paths.append(path)
    selected = code_paths[:max(1, min(int(max_code), 5))]
    selected_tests = test_paths[:max(1, min(int(max_tests), 4))]
    if not selected:
        raise HTTPException(422, "Repository discovery found no relevant allowlisted application source. Supply explicit paths.")
    if not selected_tests:
        raise HTTPException(422, "Repository discovery found no matching regression-test file. Supply a relevant test path.")
    result = selected + selected_tests
    if len(result) > MAX_FILES:
        result = result[:MAX_FILES - 1] + [selected_tests[0]]
    return result


def improve_repository(cfg: dict, goal: str, paths: list | None, model_fn) -> dict:
    goal = re.sub(r"\s+", " ", str(goal or "")).strip()
    if not 12 <= len(goal) <= 2000:
        raise HTTPException(422, "Provide a specific engineering goal between 12 and 2,000 characters.")
    discovery = None
    explicit_paths = isinstance(paths, list) and len(paths) > 0
    if explicit_paths:
        selected_paths = paths
    else:
        discovery = discover_sources(cfg, goal, limit=40)
        selected_paths = select_autonomous_sources(discovery.get("matches") or [])
    inspected = inspect_sources(cfg, selected_paths, goal=goal)
    originals = {}
    for row in inspected["files"]:
        original = read_source(cfg, row["path"])
        if original["sha"] != row["sha"] or original["sha256"] != row["sha256"]:
            raise HTTPException(409, f"{row['path']} changed during inspection; restart from a fresh source snapshot.")
        originals[row["path"]] = original
    generated = generate_changes(goal, inspected["files"], model_fn)
    expanded = apply_window_edits(generated.get("changes"), originals, inspected["files"])
    changes = validate_changes(expanded, set(originals))
    pr = create_draft_pr(cfg, changes, originals, goal, str(generated.get("summary") or "Automated code proposal."))
    return {
        "status": "draft_pr_created", "goal": goal,
        "summary": str(generated.get("summary") or "")[:4000],
        "provider": generated.get("provider"),
        "source_discovery": {
            "automatic": not explicit_paths,
            "tree_truncated": discovery.get("tree_truncated") if discovery else None,
            "candidate_paths": [row["path"] for row in (discovery or {}).get("matches", [])],
            "selected_paths": [row["path"] for row in inspected["files"]],
        },
        "changed_files": [row["path"] for row in changes],
        "pull_request": pr, "ci_verification": "pending",
        "merge_performed": False, "deployment_performed": False, "truthful": True,
    }


def build_runtime_report(
    *,
    python_version: str,
    platform_name: str,
    executables: dict,
    studio_health: dict,
    project_state: dict,
    asset_storage: dict,
    app_state: dict,
    maintenance_ready: bool,
    writer_ready: bool,
) -> dict:
    """Return a deliberately redacted live-runtime snapshot; never return secrets or raw errors."""
    health_keys = (
        "status", "media_core", "ffmpeg", "ffprobe", "offline_tts",
        "queue_online", "queue_persistent", "real_motion_video",
        "ai_video_generation_provider_available",
    )
    health = {
        key: studio_health.get(key)
        for key in health_keys
        if key in studio_health and isinstance(studio_health.get(key), (str, bool, int, float, type(None)))
    }
    project_keys = (
        "enabled", "contract_enabled", "active", "verified",
        "snapshot_verified", "last_snapshot_status", "last_restore_status",
    )
    persistence = {
        key: project_state.get(key)
        for key in project_keys
        if key in project_state and isinstance(project_state.get(key), (str, bool, int, float, type(None)))
    }
    configured = asset_storage.get("configured_providers")
    if not isinstance(configured, list):
        configured = []
    provider_names = sorted({
        str(name) for name in configured
        if str(name) in {"cloudflare_r2", "oracle_object_storage", "backblaze_b2", "tigris", "supabase"}
    })
    return {
        "schema": "ai-infinity-runtime-inspection.v1",
        "runtime": {
            "python_version": str(python_version).split()[0][:40],
            "platform": str(platform_name)[:120],
            "executables": {
                name: bool(executables.get(name))
                for name in ("ffmpeg", "ffprobe", "espeak", "espeak-ng")
            },
        },
        "creator_studio": health,
        "project_state": persistence,
        "asset_storage": {
            "configured_provider_count": len(provider_names),
            "configured_providers": provider_names,
            "primary_provider": (
                str(asset_storage.get("primary_provider"))
                if str(asset_storage.get("primary_provider")) in provider_names else None
            ),
            "durability_claimed": False,
            "note": "Configuration is not proof of successful asset writes or read-back.",
        },
        "application": {
            "storage_routes_registered": app_state.get("ai_infinity_storage_routes_registered") is True,
            "creator_3800_layer_loaded": app_state.get("ai_infinity_3800") is True,
            "production_hardening_applied": bool(app_state.get("production_hardening")),
        },
        "self_maintenance": {
            "configured_and_ready": bool(maintenance_ready),
            "free_tier_writer_ready": bool(writer_ready),
            "repository_discovery": True,
            "bounded_source_edits": True,
            "commit_bound_ci_inspection": True,
        },
        "secrets_returned": False,
        "raw_logs_returned": False,
        "truthful": True,
    }


def inspect_runtime(cfg: dict, app: FastAPI) -> dict:
    try:
        import studio_ultimate as studio
        health = studio.studio_health()
        if not isinstance(health, dict):
            health = {}
    except Exception:
        health = {"status": "unavailable"}
    try:
        from ai_infinity import project_state_backend
        persistence = project_state_backend.status()
        if not isinstance(persistence, dict):
            persistence = {}
    except Exception:
        persistence = {"enabled": False, "active": False, "verified": False}
    try:
        storage_module = __import__("sys").modules.get("ai3704_storage_fabric")
        configured = (
            storage_module._configured_provider_names()
            if storage_module and callable(getattr(storage_module, "_configured_provider_names", None))
            else []
        )
        primary = getattr(storage_module, "PRIMARY", None) if storage_module else None
        asset_storage = {"configured_providers": configured, "primary_provider": primary}
    except Exception:
        asset_storage = {"configured_providers": []}
    state = getattr(app, "state", None)
    app_state = {
        "ai_infinity_storage_routes_registered": bool(getattr(state, "ai_infinity_storage_routes_registered", False)),
        "ai_infinity_3800": bool(getattr(state, "ai_infinity_3800", False)),
        "production_hardening": getattr(state, "production_hardening", None),
    }
    return build_runtime_report(
        python_version=sys.version,
        platform_name=platform.platform(),
        executables={
            name: shutil.which(name)
            for name in ("ffmpeg", "ffprobe", "espeak", "espeak-ng")
        },
        studio_health=health,
        project_state=persistence,
        asset_storage=asset_storage,
        app_state=app_state,
        maintenance_ready=bool(cfg.get("ready")),
        writer_ready=callable(free_tier_writer()) if cfg.get("ready") else False,
    )


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
            "execution_model": "repository discovery + bounded source inspection + hash-pinned edits + draft GitHub PR + commit-bound CI inspection",
            "repository_discovery": True,
            "ci_inspection": True,
            "source_file_limit_bytes": MAX_FILE_BYTES,
            "aggregate_source_limit_bytes": MAX_TOTAL_BYTES,
            "model_context_limit_bytes": MAX_TOTAL_CONTEXT_BYTES,
            "large_module_edit_mode": "exact_sha256_pinned_line_ranges",
            "allowed_source_roots": ["root_python_modules", "ai_infinity/", "scripts/", "tests/", "creator_studio_2030.html", "ui_3800.html"],
            "regression_test_required": True,
            "direct_main_writes": False, "arbitrary_shell_execution": False,
            "automatic_merge": False, "automatic_deploy": False, "truthful": True,
        }

    @app.get("/infinity/studio/self-maintenance/runtime")
    def maintenance_runtime(request: FastAPIRequest):
        cfg = load_settings()
        authorize(request, cfg)
        return inspect_runtime(cfg, app)

    @app.post("/infinity/studio/self-maintenance/discover")
    async def maintenance_discover(request: FastAPIRequest):
        cfg = load_settings()
        authorize(request, cfg)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, "A JSON object is required.")
        return discover_sources(cfg, payload.get("goal"), payload.get("limit", 20))

    @app.post("/infinity/studio/self-maintenance/ci")
    async def maintenance_ci(request: FastAPIRequest):
        cfg = load_settings()
        authorize(request, cfg)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, "A JSON object is required.")
        return inspect_ci(cfg, payload.get("commit_sha"), payload.get("limit", 10))

    @app.post("/infinity/studio/self-maintenance/inspect")
    async def maintenance_inspect(request: FastAPIRequest):
        cfg = load_settings()
        authorize(request, cfg)
        payload = await request.json()
        return inspect_sources(
            cfg,
            payload.get("paths") if isinstance(payload, dict) else None,
            goal=payload.get("goal", "") if isinstance(payload, dict) else "",
        )

    @app.post("/infinity/studio/self-maintenance/improve")
    async def maintenance_improve(request: FastAPIRequest):
        cfg = load_settings()
        authorize(request, cfg)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, "A JSON object is required.")
        return improve_repository(cfg, payload.get("goal"), payload.get("paths"), free_tier_writer())
