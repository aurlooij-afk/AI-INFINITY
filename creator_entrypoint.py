"""AI Infinity production entrypoint.

Keeps the canonical foundation application intact while routing the professional
Creator Studio surface to the richer studio_ultimate backend. This avoids a UI-only
upgrade: creator routes have a real FastAPI backend, queued production worker,
artifacts, QC, publishing adapters, research, analytics and creator workspace.

The dispatcher also delegates the ASGI lifespan to the foundation application so
existing startup/shutdown hooks continue to run.
"""
from __future__ import annotations

import main as foundation
from fastapi import FastAPI

from studio_ultimate import register as register_creator_studio

foundation_app = foundation.app
creator_app = FastAPI(title="AI Infinity Creator Studio", docs_url=None, redoc_url=None)

# Reuse the canonical model/router function when the foundation exposes it.
# The Creator Studio remains functional without an AI provider because its
# production engine has local/free-first fallbacks and reports unavailable
# external providers honestly.
model_fn = getattr(foundation, "_2700_model", None)
register_creator_studio(creator_app, model_fn=model_fn)


class AIInfinityApplication:
    """Small ASGI dispatcher preserving every historical foundation route."""

    async def __call__(self, scope, receive, send):
        scope_type = scope.get("type")
        path = scope.get("path") or ""

        # Uvicorn's lifespan must reach the canonical app; otherwise any existing
        # startup/shutdown hooks in main.py would be bypassed.
        if scope_type == "lifespan":
            await foundation_app(scope, receive, send)
            return

        # The professional Creator Studio owns these paths. Keeping the routing
        # boundary here means old /run, /command, wallet, execution, health and
        # other foundation APIs stay untouched.
        if scope_type == "http" and (
            path == "/studio"
            or path.startswith("/studio/")
            or path == "/infinity/studio"
            or path.startswith("/infinity/studio/")
        ):
            await creator_app(scope, receive, send)
            return

        await foundation_app(scope, receive, send)


application = AIInfinityApplication()

# Expose the foundation app under the conventional name for tooling that imports
# this module and inspects the module variable app. Production uses application.
app = foundation_app
