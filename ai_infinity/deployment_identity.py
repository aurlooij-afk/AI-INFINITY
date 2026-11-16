from __future__ import annotations

"""Read trusted deployment identity from Render's runtime-provided environment."""

import os
import re
from collections.abc import Mapping
from typing import Any


def render_runtime_identity(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    values = os.environ if environ is None else environ
    is_render = str(values.get("RENDER", "")).strip().lower() == "true"
    if not is_render:
        return {
            "platform": None,
            "source": None,
            "commit_sha": None,
            "branch": None,
        }

    raw_commit = str(values.get("RENDER_GIT_COMMIT", "")).strip().lower()
    commit = raw_commit if re.fullmatch(r"[0-9a-f]{40}", raw_commit) else None
    branch = str(values.get("RENDER_GIT_BRANCH", "")).strip() or None
    return {
        "platform": "render",
        "source": "render_runtime_environment" if commit else None,
        "commit_sha": commit,
        "branch": branch,
    }
