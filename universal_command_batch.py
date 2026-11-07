from __future__ import annotations

"""Durable, ordered orchestration for multiple existing AI Infinity commands.

This module deliberately reuses ai_infinity_canonical.dispatch_command. It is
not a second creator engine and never treats an accepted project as completed
media. Each step is checkpointed before and after dispatch so a lost response
can be inspected instead of silently replaying an uncertain side effect.
"""

import hashlib
import json
import re
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable

from fastapi import HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

import ai_infinity_canonical as canonical


MAX_COMMANDS = 20
MAX_COMMAND_LENGTH = 4000
MAX_CONCURRENCY = 4
DEFAULT_CONCURRENCY = 3
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|application[_-]?key|secret|token|password|authorization)", re.I)
SENSITIVE_VALUE = re.compile(
    r"(?i)\b(?:api[_-]?key|application[_-]?key|secret|token|password|authorization)"
    r"\s*[:=]\s*['\"]?[^,\s'\"}]+"
)

def _contains_sensitive_key(value: Any) -> bool:
    """Reject credential-like fields recursively before a batch is persisted."""
    if isinstance(value, dict):
        for key, item in value.items():
            if SENSITIVE_KEY.search(str(key)) or _contains_sensitive_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if SENSITIVE_KEY.search(str(key)) else _safe(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        return SENSITIVE_VALUE.sub("[REDACTED]", value)[:12000]
    return value


def normalize_commands(raw_commands: Any) -> list[dict[str, Any]]:
    """Validate a dependency-aware DAG while preserving request/display order."""
    if not isinstance(raw_commands, list) or not raw_commands:
        raise HTTPException(422, "commands must be a non-empty task array.")
    if len(raw_commands) > MAX_COMMANDS:
        raise HTTPException(422, f"A command batch is limited to {MAX_COMMANDS} tasks.")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_chars = 0
    for index, raw in enumerate(raw_commands, 1):
        if not isinstance(raw, dict):
            raise HTTPException(422, f"commands[{index - 1}] must be an object.")
        task_id = str(raw.get("id") or f"step-{index}").strip()
        if not SAFE_ID.fullmatch(task_id) or task_id in seen:
            raise HTTPException(422, f"commands[{index - 1}] has an invalid or duplicate id.")
        command = str(raw.get("command") or raw.get("instruction") or "").strip()
        if len(command) < 3 or len(command) > MAX_COMMAND_LENGTH:
            raise HTTPException(422, f"commands[{index - 1}] command length must be 3..{MAX_COMMAND_LENGTH}.")
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        if _contains_sensitive_key(payload):
            raise HTTPException(422, "Do not include credentials in command payloads; use server-side secret configuration.")
        if SENSITIVE_VALUE.search(command):
            raise HTTPException(422, "Do not put credentials in command text.")
        depends = raw.get("depends_on") or []
        if not isinstance(depends, list) or any(not isinstance(x, str) for x in depends):
            raise HTTPException(422, f"commands[{index - 1}].depends_on must be an array of task ids.")
        depends = list(dict.fromkeys(x.strip() for x in depends))
        source_id = raw.get("project_id_from")
        if source_id is not None:
            source_id = str(source_id).strip()
            if source_id not in depends:
                raise HTTPException(422, f"commands[{index - 1}].project_id_from must also appear in depends_on.")
        total_chars += len(command) + len(_json(payload))
        if total_chars > 24000:
            raise HTTPException(422, "The combined command graph exceeds the 24,000-character safety limit.")
        normalized.append({
            "id": task_id, "command": command, "payload": _safe(payload),
            "depends_on": depends, "project_id_from": source_id,
            "continue_on_error": raw.get("continue_on_error") is True,
        })
        seen.add(task_id)

    by_id = {item["id"]: item for item in normalized}
    order = {item["id"]: n for n, item in enumerate(normalized)}
    children = {item["id"]: [] for item in normalized}
    indegree = {}
    for item in normalized:
        task_id = item["id"]
        for dep in item["depends_on"]:
            if dep not in by_id or dep == task_id:
                raise HTTPException(422, f"Task {task_id!r} has an unknown or self-referencing dependency.")
            children[dep].append(task_id)
        indegree[task_id] = len(item["depends_on"])
    ready = sorted((key for key, degree in indegree.items() if degree == 0), key=order.get)
    visited = 0
    while ready:
        node = ready.pop(0)
        visited += 1
        for child in sorted(children[node], key=order.get):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort(key=order.get)
    if visited != len(normalized):
        raise HTTPException(422, "command dependencies contain a cycle; task graphs must be acyclic.")
    return normalized


def _find_project_id(value: Any) -> str:
    if isinstance(value, dict):
        candidate = value.get("project_id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        # Prefer the canonical create response's nested result before any other payload.
        for key in ("result", "project", "data"):
            found = _find_project_id(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_project_id(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_project_id(item)
            if found:
                return found
    return ""


def execute_commands(
    commands: list[dict[str, Any]], *, user_id: str, batch_id: str,
    dispatch: Callable[[str, str, dict[str, Any]], dict[str, Any]],
    checkpoint: Callable[[list[dict[str, Any]], str], None],
    max_concurrency: int = 1,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run ready DAG nodes through the canonical dispatcher; never auto-replay."""
    if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int):
        raise HTTPException(422, "max_concurrency must be an integer.")
    if not 1 <= max_concurrency <= MAX_CONCURRENCY:
        raise HTTPException(422, f"max_concurrency must be between 1 and {MAX_CONCURRENCY}.")

    steps = {step["id"]: step for step in commands}
    order = {step["id"]: n for n, step in enumerate(commands, 1)}
    success = {"completed", "accepted"}
    failed_terminal = {"failed", "authorization_required", "dependency_blocked", "not_run", "cancelled"}
    records = {}
    results = []
    for step in commands:
        key = f"batch-{batch_id}-{step['id']}"
        row = {
            "id": step["id"], "task_id": f"{batch_id}:{step['id']}",
            "sequence": order[step["id"]], "objective": step["command"],
            "dependencies": list(step["depends_on"]), "state": "created",
            "progress": 0, "attempt": 1, "idempotency_key": key,
            "execution_config": {"max_concurrency": max_concurrency, "automatic_retry": False},
            "verification": {"status": "not_performed_by_batch_coordinator"},
            "evidence_refs": [], "executed": False,
        }
        records[step["id"]] = row
        results.append(row)

    pending = set(steps)
    running = {}
    halt = [False]
    cancellation_observed = [False]
    checkpoint(results, "running")

    def block_failed_dependencies():
        changed = False
        for task_id in sorted(tuple(pending), key=order.get):
            bad = [dep for dep in steps[task_id]["depends_on"] if records[dep]["state"] in failed_terminal]
            if bad:
                records[task_id].update({
                    "state": "dependency_blocked", "blocked_by": bad,
                    "reason": "A required dependency did not complete successfully.",
                    "executed": False, "completed_at": time.time(),
                })
                pending.remove(task_id)
                changed = True
        if changed:
            checkpoint(results, "running")

    def make_payload(step):
        payload = dict(step["payload"])
        source_id = step.get("project_id_from")
        if source_id:
            project_id = _find_project_id(records[source_id].get("result"))
            if not project_id:
                return None, "The dependency did not return a project_id."
            payload["project_id"] = project_id
        key = f"batch-{batch_id}-{step['id']}"
        payload.setdefault("idempotency_key", key)
        payload.setdefault("api_anything_idempotency_key", key)
        return payload, None

    def settle(future, task_id):
        row, step = records[task_id], steps[task_id]
        try:
            data = future.result()
            data = _safe(data if isinstance(data, dict) else {"value": data})
            status = str(data.get("status") or "").strip().lower()
            if status in {"authorization_required", "write_confirmation_required", "dry_run_required"}:
                state = "authorization_required"
            elif status in {"unsupported_or_incomplete", "unsupported", "degraded", "failed", "error",
                            "write_outcome_ambiguous", "write_accepted_not_independently_verified"}:
                state = "failed"
            elif status in {"queued", "existing"}:
                state = "accepted"
            elif status in {"completed", "learned", "dry_run_ready"}:
                state = "completed"
            else:
                state = "failed"
            row.update({
                "state": state, "progress": 100 if state in success else row.get("progress", 0),
                "status": status or "unknown", "result": data,
                "executed": data.get("executed") is True, "completed_at": time.time(),
            })
            if state == "failed":
                row["error"] = data.get("error") or data.get("message") or status or "command_failed"
        except HTTPException as exc:
            row.update({"state": "failed", "http_status": int(exc.status_code),
                        "error": _safe(exc.detail), "completed_at": time.time(), "executed": False})
        except Exception as exc:
            row.update({"state": "failed", "error_type": type(exc).__name__,
                        "error": "Command dispatch raised an unhandled error; inspect server logs.",
                        "completed_at": time.time(), "executed": False})
        if row["state"] in {"failed", "authorization_required"} and not step["continue_on_error"]:
            halt[0] = True
        checkpoint(results, "running")

    with ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="aii-task") as pool:
        while pending or running:
            # Cancellation is durable and may be requested by another process.
            # It prevents tasks that have not been dispatched from starting; it
            # deliberately does not pretend to terminate already-running work.
            if not cancellation_observed[0] and cancel_requested is not None:
                try:
                    requested = bool(cancel_requested())
                except Exception:
                    # Fail closed: if the cancellation state cannot be read,
                    # do not dispatch additional side-effecting commands.
                    requested = True
                if requested:
                    cancellation_observed[0] = True
                    for task_id in sorted(tuple(pending), key=order.get):
                        records[task_id].update({
                            "state": "cancelled",
                            "reason": "Cancellation was requested before this task was dispatched.",
                            "executed": False, "completed_at": time.time(),
                        })
                        pending.remove(task_id)
                    checkpoint(results, "cancellation_requested")
            block_failed_dependencies()
            if halt[0]:
                for task_id in sorted(tuple(pending), key=order.get):
                    records[task_id].update({
                        "state": "not_run",
                        "reason": "Execution halted after an earlier task failed or required authorization.",
                        "executed": False, "completed_at": time.time(),
                    })
                    pending.remove(task_id)
                if pending:
                    checkpoint(results, "running")
            else:
                ready = []
                for task_id in sorted(pending, key=order.get):
                    deps = steps[task_id]["depends_on"]
                    if all(records[dep]["state"] in success for dep in deps):
                        ready.append(task_id)
                    elif records[task_id]["state"] == "created":
                        records[task_id]["state"] = "waiting"
                slots = max_concurrency - len(running)
                for task_id in ready[:max(0, slots)]:
                    step, row = steps[task_id], records[task_id]
                    payload, error = make_payload(step)
                    if error:
                        row.update({"state": "dependency_blocked", "reason": error,
                                    "executed": False, "completed_at": time.time()})
                        pending.remove(task_id)
                        checkpoint(results, "running")
                        if not step["continue_on_error"]:
                            halt[0] = True
                        continue
                    row.update({"state": "ready", "progress": 1, "inputs": _safe(payload),
                                "project_id": payload.get("project_id")})
                    checkpoint(results, "running")
                    row.update({"state": "running", "progress": 5, "started_at": time.time()})
                    checkpoint(results, "running")
                    pending.remove(task_id)
                    running[pool.submit(dispatch, step["command"], user_id, payload)] = task_id

                if pending and not running and not ready:
                    # Validation rejects cycles; this protects against mutated inputs.
                    for task_id in sorted(tuple(pending), key=order.get):
                        records[task_id].update({"state": "dependency_blocked",
                            "reason": "Task graph could not resolve its dependencies.",
                            "executed": False, "completed_at": time.time()})
                        pending.remove(task_id)
                    checkpoint(results, "running")

            if running:
                done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in done:
                    settle(future, running.pop(future))
            elif not pending:
                break

    states = [row["state"] for row in results]
    completed_or_accepted = sum(state in success for state in states)
    cancelled_steps = sum(state == "cancelled" for state in states)
    if cancelled_steps:
        other_terminal_work = any(state in {"failed", "authorization_required"} for state in states)
        overall = "partially_cancelled" if completed_or_accepted or other_terminal_work else "cancelled"
    elif "failed" in states:
        overall = "partial" if completed_or_accepted else "failed"
    elif any(state in {"authorization_required", "dependency_blocked", "not_run"} for state in states):
        overall = "partial" if completed_or_accepted else "blocked"
    elif "accepted" in states:
        overall = "accepted"
    else:
        overall = "completed"
    checkpoint(results, overall)
    return {
        "batch_id": batch_id, "status": overall, "step_count": len(commands),
        "max_concurrency": max_concurrency,
        "completed_steps": sum(state == "completed" for state in states),
        "accepted_steps": sum(state == "accepted" for state in states),
        "failed_steps": sum(state == "failed" for state in states),
        "blocked_steps": sum(state in {"authorization_required", "dependency_blocked", "not_run"} for state in states),
        "cancelled_steps": cancelled_steps,
        "cancellation_observed": cancellation_observed[0],
        "executed": any(row.get("executed") is True for row in results),
        "steps": results, "truthful": True,
    }


def register(app: Any) -> None:
    """Register the durable ordered-batch endpoints on the canonical app."""
    with canonical.studio().DB_LOCK, canonical.db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_command_batches (
                batch_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                idempotency_key TEXT,
                request_sha256 TEXT NOT NULL,
                commands_json TEXT NOT NULL,
                results_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                max_concurrency INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(canonical_command_batches)").fetchall()}
        if "max_concurrency" not in columns:
            connection.execute("ALTER TABLE canonical_command_batches ADD COLUMN max_concurrency INTEGER NOT NULL DEFAULT 1")
        if "cancel_requested_at" not in columns:
            connection.execute("ALTER TABLE canonical_command_batches ADD COLUMN cancel_requested_at REAL")
        if "cancel_reason" not in columns:
            connection.execute("ALTER TABLE canonical_command_batches ADD COLUMN cancel_reason TEXT")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_canonical_command_batches_idempotency
            ON canonical_command_batches(user_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )

    def save(batch_id: str, results: list[dict[str, Any]], status: str) -> None:
        with canonical.studio().DB_LOCK, canonical.db() as connection:
            connection.execute(
                "UPDATE canonical_command_batches SET results_json=?, status=?, updated_at=? WHERE batch_id=?",
                (_json(_safe(results)), status, time.time(), batch_id),
            )

    def load(batch_id: str, user_id: str) -> dict[str, Any]:
        with canonical.studio().DB_LOCK, canonical.db() as connection:
            row = connection.execute(
                "SELECT batch_id,user_id,request_sha256,commands_json,results_json,status,created_at,updated_at,max_concurrency,cancel_requested_at,cancel_reason "
                "FROM canonical_command_batches WHERE batch_id=? AND user_id=?",
                (batch_id, user_id),
            ).fetchone()
        if not row:
            raise HTTPException(404, "command batch not found")
        try:
            commands = json.loads(row[3])
            results = json.loads(row[4])
        except Exception:
            commands, results = [], []
        return {
            "batch_id": row[0], "status": row[5], "commands": commands,
            "steps": results, "created_at": row[6], "updated_at": row[7],
            "max_concurrency": int(row[8] or 1),
            "cancel_requested": row[9] is not None,
            "cancel_requested_at": row[9],
            "cancel_reason": row[10] or "",
            "reconciliation_required": bool(row[5] == "running" and any(item.get("state") == "running" for item in results if isinstance(item, dict))),
            "truthful": True,
        }

    def cancellation_requested(batch_id: str) -> bool:
        with canonical.studio().DB_LOCK, canonical.db() as connection:
            row = connection.execute(
                "SELECT cancel_requested_at FROM canonical_command_batches WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
        return bool(row and row[0] is not None)

    @app.post("/infinity/canonical/batches")
    async def canonical_command_batch(request: Request, response: Response):
        user_id = canonical.current_user(request, response)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "The command batch body must be a JSON object.")
        commands = normalize_commands(body.get("commands"))
        raw_concurrency = body.get("max_concurrency", DEFAULT_CONCURRENCY)
        if isinstance(raw_concurrency, bool):
            raise HTTPException(422, "max_concurrency must be an integer.")
        try:
            max_concurrency = int(raw_concurrency)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(422, "max_concurrency must be an integer.")
        if not 1 <= max_concurrency <= MAX_CONCURRENCY:
            raise HTTPException(422, f"max_concurrency must be between 1 and {MAX_CONCURRENCY}.")
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", idempotency_key):
            raise HTTPException(422, "idempotency_key is required and must be 8..128 safe characters.")
        request_hash = hashlib.sha256(_json(commands).encode("utf-8")).hexdigest()

        # Serialize the idempotency lookup and insert with a SQLite write lock.
        # The unique index remains the cross-process invariant. BEGIN IMMEDIATE
        # prevents concurrent retries from dispatching the same side-effecting batch twice.
        batch_id = uuid.uuid4().hex
        now = time.time()
        existing_id = ""
        with canonical.studio().DB_LOCK, canonical.db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT batch_id,request_sha256 FROM canonical_command_batches WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
            if prior:
                if prior[1] != request_hash:
                    raise HTTPException(409, "idempotency_key is already bound to a different command batch.")
                existing_id = str(prior[0])
            else:
                connection.execute(
                    "INSERT INTO canonical_command_batches "
                    "(batch_id,user_id,idempotency_key,request_sha256,commands_json,results_json,status,created_at,updated_at,max_concurrency) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (batch_id, user_id, idempotency_key, request_hash, _json(commands), "[]", "running", now, now, max_concurrency),
                )

        if existing_id:
            existing = load(existing_id, user_id)
            existing["replayed"] = True
            return existing

        result = await run_in_threadpool(
            execute_commands,
            commands,
            user_id=user_id,
            batch_id=batch_id,
            dispatch=canonical.dispatch_command,
            checkpoint=lambda rows, state: save(batch_id, rows, state),
            max_concurrency=max_concurrency,
            cancel_requested=lambda: cancellation_requested(batch_id),
        )
        save(batch_id, result["steps"], result["status"])
        result["replayed"] = False
        return result

    @app.post("/infinity/canonical/batches/{batch_id}/cancel")
    def canonical_cancel_command_batch(batch_id: str, request: Request, response: Response):
        user_id = canonical.current_user(request, response)
        with canonical.studio().DB_LOCK, canonical.db() as connection:
            row = connection.execute(
                "SELECT status,cancel_requested_at FROM canonical_command_batches WHERE batch_id=? AND user_id=?",
                (batch_id, user_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, "command batch not found")
            if row[0] == "running" and row[1] is None:
                connection.execute(
                    "UPDATE canonical_command_batches SET cancel_requested_at=?,cancel_reason=?,updated_at=? "
                    "WHERE batch_id=? AND user_id=? AND status='running' AND cancel_requested_at IS NULL",
                    (time.time(), "owner_requested", time.time(), batch_id, user_id),
                )
        result = load(batch_id, user_id)
        result["cancellation_state"] = (
            "requested" if result["cancel_requested"] and result["status"] == "running"
            else "already_requested" if result["cancel_requested"]
            else "not_running"
        )
        result["cancellation_semantics"] = (
            "Prevents commands not yet dispatched from starting. Already-running commands are allowed to settle; "
            "their side effects are not forcibly interrupted. Reconcile any running state after process interruption."
        )
        return result

    @app.get("/infinity/canonical/batches/{batch_id}")
    def canonical_get_command_batch(batch_id: str, request: Request, response: Response):
        user_id = canonical.current_user(request, response)
        return load(batch_id, user_id)
