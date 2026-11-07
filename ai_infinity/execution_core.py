from __future__ import annotations

"""Shared execution control plane for the canonical natural-language runtime.

The registry binds only routes that the canonical dispatcher actually implements.
The domain engines remain authoritative for provider access, authorization,
artifact generation, and independent verification. This module supplies the
common capability identity, dispatch boundary, durable execution ledger, and
truthful lifecycle record; it is not a replacement creator engine.
"""

import hashlib
import json
import re
import sqlite3
import time
import uuid
from typing import Any

from fastapi import HTTPException, Request, Response


# route, capability_id, domain, mode, side_effect_policy, verification_contract
_DEFINITIONS = (
    ("research", "knowledge.research", "research_knowledge", "execute", "read_only", "source_provenance"),
    ("create", "creative.create", "creative_production", "execute", "project_creation", "artifact_qc_and_project_truth"),
    ("edit", "creative.edit", "creative_production", "execute", "project_owned_mutation", "before_after_project_truth"),
    ("project_status", "creative.project_status", "creative_production", "execute", "read_only", "project_reconciliation"),
    ("capabilities", "system.capabilities", "system_operations", "execute", "read_only", "runtime_probe"),
    ("script", "creative.script", "creative_production", "execute", "read_only", "editorial_review_required"),
    ("strategy", "business.strategy", "business_operations", "execute", "read_only", "evidence_and_human_review"),
    ("marketing", "business.marketing", "business_operations", "execute", "read_only", "evidence_and_human_review"),
    ("seo", "business.seo", "business_operations", "execute", "read_only", "metadata_review"),
    ("analytics", "analytics.read", "data_analytics", "execute", "read_only", "source_data_truth"),
    ("publisher", "publishing.options", "publishing_operations", "execute", "read_only_no_publish", "connection_and_authorization_state"),
    ("monetization", "business.monetization_options", "business_operations", "execute", "read_only_no_financial_action", "source_data_truth"),
    ("public_api", "tools.public_api", "tool_runtime", "execute", "connector_policy_enforced", "provider_response_and_readback"),
    ("github_api", "software.github_api", "software_engineering", "execute", "credential_scope_and_github_policy", "github_resource_readback"),
    ("authorization_required", "control.authorization_gate", "autonomy_control", "guard", "no_dispatch", "action_specific_authorization"),
    ("unsupported_deliverable", "control.unsupported_deliverable", "autonomy_control", "guard", "no_dispatch", "explicit_unsupported_result"),
    ("project_id_required", "control.project_context_required", "autonomy_control", "guard", "no_dispatch", "project_ownership_check"),
    ("deliverable_required", "control.deliverable_required", "autonomy_control", "guard", "no_dispatch", "explicit_user_intent"),
    ("unsupported", "control.unsupported_intent", "autonomy_control", "guard", "no_dispatch", "explicit_unsupported_result"),
)

CAPABILITIES = tuple(
    {
        "route": route,
        "id": capability_id,
        "domain": domain,
        "mode": mode,
        "side_effect_policy": side_effect_policy,
        "verification_contract": verification_contract,
        "handler": "ai_infinity_canonical.dispatch_command",
        "retry_policy": "never_auto_replay",
        "authorization_policy": "preserve_domain_authorization_gates",
    }
    for route, capability_id, domain, mode, side_effect_policy, verification_contract in _DEFINITIONS
)
_BY_ROUTE = {item["route"]: item for item in CAPABILITIES}
_BY_ID = {item["id"]: item for item in CAPABILITIES}


def _canonical():
    import ai_infinity_canonical
    return ai_infinity_canonical


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(command: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json({"command": command, "payload": payload}).encode("utf-8")).hexdigest()


def _project_id(value: Any) -> str:
    if isinstance(value, dict):
        candidate = value.get("project_id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        for key in ("result", "project", "data"):
            found = _project_id(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _project_id(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _project_id(item)
            if found:
                return found
    return ""


def _ensure_schema() -> None:
    canonical = _canonical()
    with canonical.studio().DB_LOCK, canonical.db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_executions (
                execution_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                idempotency_key TEXT,
                project_id TEXT,
                capability_id TEXT NOT NULL,
                route TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                result_summary_json TEXT NOT NULL,
                started_at REAL NOT NULL,
                completed_at REAL
            )
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(canonical_executions)").fetchall()}
        if "idempotency_key" not in columns:
            connection.execute("ALTER TABLE canonical_executions ADD COLUMN idempotency_key TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_canonical_executions_user "
            "ON canonical_executions(user_id, started_at DESC)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_canonical_executions_idempotency "
            "ON canonical_executions(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
        )


def _execution_state(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "").strip().lower()
    if status in {"authorization_required", "write_confirmation_required", "dry_run_required", "unsupported", "unsupported_or_incomplete"}:
        return "blocked"
    if status in {"queued", "existing", "discovery_started"}:
        return "accepted"
    if status in {"completed", "learned", "dry_run_ready"}:
        return "completed"
    if status in {"degraded", "failed", "error", "write_outcome_ambiguous", "write_accepted_not_independently_verified"}:
        return "degraded" if status == "degraded" else "failed"
    return "completed" if result.get("executed") is True else "blocked"


def _summary(result: dict[str, Any], route: str) -> dict[str, Any]:
    nested = result.get("result") if isinstance(result.get("result"), dict) else {}
    project_id = _project_id(result)
    return {
        "status": str(result.get("status") or "unknown")[:80],
        "route": str(result.get("route") or route)[:80],
        "executed": result.get("executed") is True,
        "project_id": project_id or None,
        "provider": str(result.get("provider") or nested.get("provider") or "")[:120] or None,
        "artifact_created": result.get("artifact_created") is True,
    }


def _record(execution_id: str, user_id: str, project_id: str, capability: dict[str, Any],
            request_sha256: str, state: str, summary: dict[str, Any], started_at: float,
            completed_at: float | None, idempotency_key: str | None = None) -> None:
    canonical = _canonical()
    _ensure_schema()
    with canonical.studio().DB_LOCK, canonical.db() as connection:
        connection.execute(
            """
            INSERT INTO canonical_executions (
                execution_id,user_id,idempotency_key,project_id,capability_id,route,request_sha256,
                state,result_summary_json,started_at,completed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(execution_id) DO UPDATE SET
                project_id=excluded.project_id,
                state=excluded.state,
                result_summary_json=excluded.result_summary_json,
                completed_at=excluded.completed_at
            """,
            (
                execution_id, user_id, idempotency_key, project_id or None,
                capability["id"], capability["route"], request_sha256, state,
                _json(summary), started_at, completed_at,
            ),
        )


def _find_idempotent_execution(user_id: str, idempotency_key: str, request_sha256: str) -> dict[str, Any] | None:
    canonical = _canonical()
    _ensure_schema()
    with canonical.studio().DB_LOCK, canonical.db() as connection:
        row = connection.execute(
            "SELECT execution_id,project_id,capability_id,route,request_sha256,state,"
            "result_summary_json,started_at,completed_at FROM canonical_executions "
            "WHERE user_id=? AND idempotency_key=?",
            (user_id, idempotency_key),
        ).fetchone()
    if not row:
        return None
    if row[4] != request_sha256:
        raise HTTPException(409, "idempotency_key is already bound to a different command and payload.")
    try:
        summary = json.loads(row[6] or "{}")
    except Exception:
        summary = {}
    return {
        "execution_id": row[0], "project_id": row[1], "capability_id": row[2],
        "route": row[3], "request_sha256": row[4], "state": row[5],
        "result": summary, "started_at": row[7], "completed_at": row[8],
    }


def _replay_response(existing: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "existing",
        "executed": False,
        "idempotent_replay": True,
        "result": existing.get("result") or {},
        "execution": {
            "execution_id": existing["execution_id"],
            "capability_id": existing["capability_id"],
            "state": existing["state"],
            "request_sha256": existing["request_sha256"],
            "retry_policy": "never_auto_replay",
            "replayed": True,
        },
        "truthful": True,
    }


def _event(user_id: str, project_id: str, name: str, data: dict[str, Any]) -> None:
    try:
        _canonical().event(project_id or None, user_id, name, data)
    except Exception:
        # The dedicated execution ledger is authoritative. Event-stream
        # decoration must not turn an otherwise persisted dispatch into a lie.
        pass


def registry_snapshot() -> dict[str, Any]:
    canonical = _canonical()
    bound = callable(getattr(canonical, "dispatch_command", None))
    items = []
    for item in CAPABILITIES:
        row = dict(item)
        row["registered"] = True
        row["handler_bound"] = bound
        row["executable"] = bound and row["mode"] == "execute"
        row["readiness_state"] = (
            "BOUND_TO_CANONICAL_DISPATCHER" if row["executable"]
            else "GUARD_ONLY" if row["mode"] == "guard"
            else "UNBOUND"
        )
        items.append(row)
    return {
        "schema": "ai-infinity-executable-capability-registry.v1",
        "registry_count": len(items),
        "executable_count": sum(item["executable"] for item in items),
        "guard_count": sum(item["mode"] == "guard" for item in items),
        "handler_binding": "ai_infinity_canonical.dispatch_command",
        "execution_model": "shared_canonical_dispatch_with_durable_execution_ledger",
        "retry_policy": "never_auto_replay",
        "verification_policy": "domain_specific_evidence_required; execution_core_never_claims_independent_verification",
        "capabilities": items,
        "truthful": True,
    }


def dispatch(command: str, user_id: str, payload: dict[str, Any] | None = None,
             request: Request | None = None) -> dict[str, Any]:
    """Resolve one command to a registered canonical capability and record its lifecycle."""
    canonical = _canonical()
    text = str(command or "").strip()
    if len(text) < 3 or len(text) > 4000:
        raise HTTPException(422, "command length must be 3..4000 characters")
    if not str(user_id or "").strip():
        raise HTTPException(401, "authenticated user context is required")
    body = dict(payload) if isinstance(payload, dict) else {}
    decision = canonical.classify_command(text, body)
    route = str(decision.get("route") or "unsupported")
    capability = _BY_ROUTE.get(route)
    if capability is None:
        return {
            "status": "unsupported_or_incomplete",
            "route": route,
            "reason": "No executable capability binding is registered for the resolved route.",
            "executed": False,
            "truthful": True,
        }

    execution_id = "exec_" + uuid.uuid4().hex
    started_at = time.time()
    request_sha256 = _digest(text, body)
    raw_idempotency_key = body.get("idempotency_key") or body.get("execution_idempotency_key")
    idempotency_key = str(raw_idempotency_key).strip() if raw_idempotency_key is not None else ""
    if idempotency_key and not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", idempotency_key):
        raise HTTPException(422, "idempotency_key must be 8..128 safe characters.")
    if idempotency_key:
        existing = _find_idempotent_execution(str(user_id), idempotency_key, request_sha256)
        if existing:
            return _replay_response(existing)
    # Never trust a caller-supplied project_id for cross-project event attribution.
    # The domain dispatcher must first validate ownership and return the canonical
    # project identity before the ledger associates an execution with that project.
    project_id = ""
    try:
        _record(
            execution_id, str(user_id), project_id, capability, request_sha256,
            "running", {}, started_at, None, idempotency_key or None,
        )
    except sqlite3.IntegrityError:
        if not idempotency_key:
            raise
        existing = _find_idempotent_execution(str(user_id), idempotency_key, request_sha256)
        if existing:
            return _replay_response(existing)
        raise
    _event(str(user_id), project_id, "execution_started", {
        "execution_id": execution_id, "capability_id": capability["id"],
        "route": route, "request_sha256": request_sha256,
    })

    try:
        raw = canonical.dispatch_command(text, str(user_id), body, request=request)
        result = dict(raw) if isinstance(raw, dict) else {"status": "completed", "result": raw}
        actual_route = str(result.get("route") or route)
        if actual_route != route:
            raise RuntimeError("canonical dispatcher route did not match resolved capability")
        state = _execution_state(result)
        summary = _summary(result, route)
        completed_at = time.time()
        final_project_id = str(summary.get("project_id") or "")
        _record(
            execution_id, str(user_id), final_project_id, capability, request_sha256,
            state, summary, started_at, completed_at,
        )
        _event(str(user_id), final_project_id, "execution_settled", {
            "execution_id": execution_id, "capability_id": capability["id"],
            "route": route, "state": state, "status": summary["status"],
            "executed": summary["executed"],
        })
        result["execution"] = {
            "execution_id": execution_id,
            "capability_id": capability["id"],
            "domain": capability["domain"],
            "state": state,
            "started_at": started_at,
            "completed_at": completed_at,
            "request_sha256": request_sha256,
            "side_effect_policy": capability["side_effect_policy"],
            "verification": "delegated_to_domain_evidence; not_independently_verified_by_execution_core",
            "retry_policy": "never_auto_replay",
        }
        return result
    except HTTPException as exc:
        completed_at = time.time()
        blocked = int(exc.status_code) in {401, 403}
        failure_status = "authorization_required" if blocked else "dispatch_http_error"
        _record(
            execution_id, str(user_id), project_id, capability, request_sha256,
            "blocked" if blocked else "failed",
            {"status": failure_status, "http_status": int(exc.status_code)},
            started_at, completed_at,
        )
        _event(str(user_id), project_id, "execution_blocked" if blocked else "execution_failed", {
            "execution_id": execution_id, "capability_id": capability["id"],
            "route": route, "http_status": int(exc.status_code),
        })
        raise
    except Exception as exc:
        completed_at = time.time()
        _record(
            execution_id, str(user_id), project_id, capability, request_sha256,
            "failed", {"status": "dispatch_exception", "error_type": type(exc).__name__},
            started_at, completed_at,
        )
        _event(str(user_id), project_id, "execution_failed", {
            "execution_id": execution_id, "capability_id": capability["id"],
            "route": route, "error_type": type(exc).__name__,
        })
        raise


def get_execution(execution_id: str, user_id: str) -> dict[str, Any]:
    canonical = _canonical()
    _ensure_schema()
    with canonical.studio().DB_LOCK, canonical.db() as connection:
        row = connection.execute(
            "SELECT execution_id,project_id,capability_id,route,request_sha256,state,"
            "result_summary_json,started_at,completed_at FROM canonical_executions "
            "WHERE execution_id=? AND user_id=?",
            (execution_id, user_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "execution record not found")
    try:
        summary = json.loads(row[6] or "{}")
    except Exception:
        summary = {}
    return {
        "execution_id": row[0], "project_id": row[1], "capability_id": row[2],
        "route": row[3], "request_sha256": row[4], "state": row[5],
        "result": summary, "started_at": row[7], "completed_at": row[8],
        "verification": "domain_specific_evidence_required",
        "truthful": True,
    }


def register(app: Any) -> None:
    """Register the shared execution-core introspection and ledger endpoints."""
    if getattr(app.state, "ai_infinity_execution_core_registered", False):
        return
    _ensure_schema()
    canonical = _canonical()

    @app.get("/infinity/execution/health")
    def execution_health():
        snapshot = registry_snapshot()
        return {
            "status": "healthy" if snapshot["executable_count"] > 0 else "degraded",
            "registry_count": snapshot["registry_count"],
            "executable_count": snapshot["executable_count"],
            "guard_count": snapshot["guard_count"],
            "ledger": "sqlite_persisted",
            "retry_policy": snapshot["retry_policy"],
            "truthful": True,
        }

    @app.get("/infinity/execution/capabilities")
    def execution_capabilities():
        return registry_snapshot()

    @app.get("/infinity/execution/{execution_id}")
    def execution_status(execution_id: str, request: Request, response: Response):
        user_id = canonical.current_user(request, response)
        return get_execution(execution_id, user_id)

    app.state.ai_infinity_execution_core_registered = True
