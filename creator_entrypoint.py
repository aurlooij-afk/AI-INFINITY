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

        # AI Infinity has one public website interface: the Creator Studio.
        # Historical foundation APIs remain available as backend routes, but the
        # website itself stays unified on desktop and mobile.
        async def send_with_no_cache(message):
            if scope_type == "http" and message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                # The canonical AI Infinity shell must never be trapped behind an old
                # cached Creator Studio HTML document after a deployment.
                headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-store, no-cache, must-revalidate, max-age=0"))
                headers.append((b"pragma", b"no-cache"))
                headers.append((b"expires", b"0"))
                message = dict(message)
                message["headers"] = headers
            await send(message)

        # The public root is the canonical AI Infinity operating shell. The
        # professional Creator Studio remains available under /infinity/studio
        # and continues to use its real production backend.
        if scope_type == "http" and path in {"/", "/home"}:
            await foundation_app(scope, receive, send_with_no_cache)
            return

        if scope_type == "http" and (
            path == "/studio"
            or path.startswith("/studio/")
            or path == "/infinity/studio"
            or path.startswith("/infinity/studio/")
        ):
            await creator_app(scope, receive, send_with_no_cache)
            return

        await foundation_app(scope, receive, send_with_no_cache if scope_type == "http" else send)


application = AIInfinityApplication()

# Expose the foundation app under the conventional name for tooling that imports
# this module and inspects the module variable app. Production uses application.
app = foundation_app
