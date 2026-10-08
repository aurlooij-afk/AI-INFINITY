from __future__ import annotations

"""Truthful project-state durability contract.

Object-storage artifact replication and local filesystem writability are not, by
themselves, proof that the project's SQLite state will survive replacement. The
application therefore reports durable project state only when an operator
explicitly enables the durable contract and confirms a real persistent volume
or a durable state backend.
"""

import os
from pathlib import Path


TRUE = {"1", "true", "yes", "on"}
DURABLE_REMOTE_BACKENDS = {"cloudflare_r2", "backblaze_b2", "b2"}


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in TRUE


def project_state_durability(root: Path | str) -> dict:
    path = Path(root).resolve()
    mode = os.getenv("AI_INFINITY_PERSISTENCE_MODE", "volatile").strip().lower()
    confirmed_volume = _flag("AI_INFINITY_PERSISTENT_VOLUME_CONFIRMED")
    confirmed_backend = _flag("AI_INFINITY_PROJECT_STATE_DURABLE")
    remote_backend = os.getenv("AI_INFINITY_PROJECT_STATE_BACKEND", "").strip().lower()

    backend_status = {}
    try:
        from ai_infinity import project_state_backend
        backend_status = project_state_backend.status()
    except Exception as exc:
        backend_status = {
            "configured": False,
            "active": False,
            "verified": False,
            "backend": remote_backend or None,
            "last_error": f"{type(exc).__name__}: {str(exc)[:240]}",
            "truthful": True,
        }

    backend_ready = bool(
        mode == "durable"
        and confirmed_backend
        and remote_backend in DURABLE_REMOTE_BACKENDS
        and backend_status.get("contract_enabled")
        and backend_status.get("active")
        and backend_status.get("verified")
    )
    local_volume_ready = confirmed_volume and not str(path).startswith("/tmp")

    persistent = bool(backend_ready or (mode == "durable" and local_volume_ready))
    if persistent:
        reason = "verified_durable_state_backend" if backend_ready else "explicit_persistent_volume_contract"
    elif mode == "durable" and remote_backend:
        reason = "durable_mode_requested_but_backend_not_verified"
    elif mode == "durable":
        reason = "durable_mode_requested_but_storage_not_confirmed"
    else:
        reason = "volatile_mode_or_ephemeral_storage"

    return {
        "persistent": persistent,
        "mode": mode,
        "path": str(path),
        "persistent_volume_confirmed": confirmed_volume,
        "project_state_backend_confirmed": confirmed_backend,
        "project_state_backend": remote_backend or None,
        "project_state_backend_status": backend_status,
        "reason": reason,
        "truthful": True,
    }
