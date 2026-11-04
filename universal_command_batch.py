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
from typing import Any, Callable

from fastapi import HTTPException, Request, Response

import ai_infinity_canonical as canonical


MAX_COMMANDS = 20
MAX_COMMAND_LENGTH = 4000
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|application[_-]?key|secret|token|password|authorization)", re.I)
SENSITIVE_VALUE = re.compile(
    r"(?i)\b(?:api[_-]?key|application[_-]?key|secret|token|password|authorization)"
    r"\s*[:=]\s*['\"]?[^,\s'\"}]+"
)


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
    if not isinstance(raw_commands, list) or not raw_commands:
        raise HTTPException(422, "commands must be a non-empty ordered array.")
    if len(raw_commands) > MAX_COMMANDS:
        raise HTTPException(422, f"A command batch is limited to {MAX_COMMANDS} steps.")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_chars = 0

    for index, raw in enumerate(raw_commands, 1):
        if not isinstance(raw, dict):
            raise HTTPException(422, f"commands[{index - 1}] must be an object.")
        step_id = str(raw.get("id") or f"step-{index}").strip()
        if not SAFE_ID.fullmatch(step_id) or step_id in seen:
            raise HTTPException(422, f"commands[{index - 1}] has an invalid or duplicate id.")
        command = str(raw.get("command") or raw.get("instruction") or "").strip()
        if len(command) < 3 or len(command) > MAX_COMMAND_LENGTH:
            raise HTTPException(422, f"commands[{index - 1}] command length must be 3..{MAX_COMMAND_LENGTH}.")
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        if any(SENSITIVE_KEY.search(str(key)) for key in payload):
            raise HTTPException(422, "Do not include credentials in command payloads; use server-side secret configuration.")
        if SENSITIVE_VALUE.search(command):
            raise HTTPException(422, "Do not put credentials in command text.")
        depends = raw.get("depends_on") or []
        if not isinstance(depends, list) or any(not isinstance(item, str) for item in depends):
            raise HTTPException(422, f"commands[{index - 1}].depends_on must be an array of step ids.")
        # Earlier-only dependencies make the order deterministic and prevent cycles.
        if any(item not in seen for item in depends):
            raise HTTPException(422, f"commands[{index - 1}] may depend only on earlier steps.")
        project_id_from = raw.get("project_id_from")
        if project_id_from is not None:
            project_id_from = str(project_id_from).strip()
            if project_id_from not in depends:
                raise HTTPException(422, f"commands[{index - 1}].project_id_from must also appear in depends_on.")
        total_chars += len(command) + len(_json(payload))
        if total_chars > 24000:
            raise HTTPException(422, "The combined command batch exceeds the 24,000-character safety limit.")

        normalized.append({
            "id": step_id,
            "command": command,
            "payload": _safe(payload),
            "depends_on": list(dict.fromkeys(depends)),
            "project_id_from": project_id_from,
            "continue_on_error": raw.get("continue_on_error") is True,
        })
        seen.add(step_id)
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
    commands: list[dict[str, Any]],
    *,
    user_id: str,
    batch_id: str,
    dispatch: Callable[[str, str, dict[str, Any]], dict[str, Any]],
    checkpoint: Callable[[list[dict[str, Any]], str], None],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    halted = False

    for sequence, step in enumerate(commands, 1):
        dependency_failures = [
            dependency for dependency in step["depends_on"]
            if by_id.get(dependency, {}).get("state") not in {"completed", "accepted"}
        ]
        if halted or dependency_failures:
            record = {
                "id": step["id"], "sequence": sequence,
                "state": "dependency_blocked" if dependency_failures else "not_run",
                "reason": (
                    "A required earlier command did not complete."
                    if dependency_failures else
                    "Batch stopped after an earlier command failed or required authorization."
                ),
                "blocked_by": dependency_failures,
                "executed": False,
            }
            results.append(record)
            by_id[step["id"]] = record
            checkpoint(results, "running")
            continue

        payload = dict(step["payload"])
        if step.get("project_id_from"):
            source = by_id.get(step["project_id_from"], {})
            project_id = _find_project_id(source.get("result"))
            if not project_id:
                record = {
                    "id": step["id"], "sequence": sequence, "state": "dependency_blocked",
                    "reason": "The dependency did not return a project_id.", "executed": False,
                }
                results.append(record)
                by_id[step["id"]] = record
                checkpoint(results, "running")
                halted = not step["continue_on_error"]
                continue
            payload["project_id"] = project_id

        # Every create step receives a stable batch-scoped idempotency key.
        payload.setdefault("idempotency_key", f"batch-{batch_id}-{step['id']}")
        payload.setdefault("api_anything_idempotency_key", f"batch-{batch_id}-{step['id']}")
        record = {
            "id": step["id"], "sequence": sequence, "state": "running",
            "command": step["command"], "started_at": time.time(),
            "executed": False,
        }
        results.append(record)
        by_id[step["id"]] = record
        checkpoint(results, "running")

        try:
            dispatched = dispatch(step["command"], user_id, payload)
            dispatched = _safe(dispatched if isinstance(dispatched, dict) else {"value": dispatched})
            status = str(dispatched.get("status") or "").strip().lower()
            executed = dispatched.get("executed") is True
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
            record.update({
                "state": state, "status": status or "unknown",
                "result": dispatched, "executed": executed,
                "completed_at": time.time(),
            })
        except HTTPException as exc:
            record.update({
                "state": "failed", "http_status": int(exc.status_code),
                "error": _safe(exc.detail), "completed_at": time.time(),
                "executed": False,
            })
        except Exception as exc:
            record.update({
                "state": "failed", "error_type": type(exc).__name__,
                "error": "Command dispatch raised an unhandled error; inspect server logs.",
                "completed_at": time.time(), "executed": False,
            })

        checkpoint(results, "running")
        if record["state"] in {"failed", "authorization_required"} and not step["continue_on_error"]:
            halted = True

    states = [row.get("state") for row in results]
    if any(state == "failed" for state in states):
        overall = "partial" if any(state in {"completed", "accepted"} for state in states) else "failed"
    elif any(state in {"authorization_required", "dependency_blocked", "not_run"} for state in states):
        overall = "blocked"
    elif any(state == "accepted" for state in states):
        overall = "accepted"
    else:
        overall = "completed"
    return {
        "batch_id": batch_id, "status": overall, "step_count": len(commands),
        "completed_steps": sum(state == "completed" for state in states),
        "accepted_steps": sum(state == "accepted" for state in states),
        "failed_steps": sum(state == "failed" for state in states),
        "blocked_steps": sum(state in {"authorization_required", "dependency_blocked", "not_run"} for state in states),
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
                updated_at REAL NOT NULL
            )
            """
        )
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
                "SELECT batch_id,user_id,request_sha256,commands_json,results_json,status,created_at,updated_at "
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
            "truthful": True,
        }

    @app.post("/infinity/canonical/batches")
    async def canonical_command_batch(request: Request, response: Response):
        user_id = canonical.current_user(request, response)
        body = await request.json()
        commands = normalize_commands(body.get("commands"))
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", idempotency_key):
            raise HTTPException(422, "idempotency_key is required and must be 8..128 safe characters.")
        request_hash = hashlib.sha256(_json(commands).encode("utf-8")).hexdigest()

        with canonical.studio().DB_LOCK, canonical.db() as connection:
            prior = connection.execute(
                "SELECT batch_id,request_sha256 FROM canonical_command_batches WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
            if prior:
                if prior[1] != request_hash:
                    raise HTTPException(409, "idempotency_key is already bound to a different command batch.")
                existing_id = str(prior[0])
            else:
                existing_id = ""

        if existing_id:
            existing = load(existing_id, user_id)
            existing["replayed"] = True
            return existing

        batch_id = uuid.uuid4().hex
        now = time.time()
        with canonical.studio().DB_LOCK, canonical.db() as connection:
            connection.execute(
                "INSERT INTO canonical_command_batches "
                "(batch_id,user_id,idempotency_key,request_sha256,commands_json,results_json,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (batch_id, user_id, idempotency_key, request_hash, _json(commands), "[]", "running", now, now),
            )

        result = execute_commands(
            commands, user_id=user_id, batch_id=batch_id,
            dispatch=canonical.dispatch_command,
            checkpoint=lambda rows, state: save(batch_id, rows, state),
        )
        save(batch_id, result["steps"], result["status"])
        result["replayed"] = False
        return result

    @app.get("/infinity/canonical/batches/{batch_id}")
    def canonical_get_command_batch(batch_id: str, request: Request, response: Response):
        user_id = canonical.current_user(request, response)
        return load(batch_id, user_id)
