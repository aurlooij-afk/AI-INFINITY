from __future__ import annotations

"""Truthful project-state durability contract.

Object-storage artifact replication and local filesystem writability are not, by
themselves, proof that the project's SQLite state will survive replacement.
The application therefore reports durable project state only when an operator
explicitly enables the durable contract and confirms a real persistent volume
or a durable state backend.
"""

import os
from pathlib import Path


TRUE = {"1", "true", "yes", "on"}


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in TRUE


def project_state_durability(root: Path | str) -> dict:
    path = Path(root).resolve()
    mode = os.getenv("AI_INFINITY_PERSISTENCE_MODE", "volatile").strip().lower()
    confirmed_volume = _flag("AI_INFINITY_PERSISTENT_VOLUME_CONFIRMED")
    confirmed_backend = _flag("AI_INFINITY_PROJECT_STATE_DURABLE")
    remote_backend = os.getenv("AI_INFINITY_PROJECT_STATE_BACKEND", "").strip().lower()

    # This application currently uses its local SQLite file for active project
    # state. A future durable backend can opt in explicitly; R2 artifact
    # replication alone does not qualify.
    backend_ready = confirmed_backend and remote_backend in {"sqlite_persistent_volume", "durable_state_backend"}
    local_volume_ready = confirmed_volume and not str(path).startswith("/tmp")

    persistent = mode == "durable" and (local_volume_ready or backend_ready)
    if persistent:
        reason = "explicit_durable_contract"
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
        "reason": reason,
        "truthful": True,
    }
