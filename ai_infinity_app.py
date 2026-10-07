from __future__ import annotations
"""Production ASGI entrypoint for AI Infinity.

Canonical Creator is the public root. The historical foundation remains reachable
for legacy command/execution endpoints, but is no longer the public root UI.
"""
import main as foundation
from fastapi import FastAPI
import reality_first_3901
import ai_infinity_canonical
import studio_ultimate

reality_first_3901.install()

creator=FastAPI(
    title="AI Infinity — Canonical Creator",
    version=ai_infinity_canonical.VERSION,
    docs_url="/infinity/canonical/docs",
    redoc_url=None,
)

# Canonical routes are registered first; the mature Studio remains the real engine.
ai_infinity_canonical.register(creator)
studio_ultimate.register(creator, model_fn=getattr(foundation,"_2700_model",None))
reality_first_3901.register(creator)

# main.py is a Python module, not itself an ASGI callable. Resolve the actual
# legacy application object explicitly so non-canonical routes remain reachable.
legacy_app = next(
    (
        candidate
        for candidate in (
            getattr(foundation, "app", None),
            getattr(foundation, "application", None),
        )
        if callable(candidate)
    ),
    None,
)
if legacy_app is None and callable(foundation):
    legacy_app = foundation
if legacy_app is None:
    raise RuntimeError("AI Infinity legacy ASGI application was not found in main.py")

class CanonicalApplication:
    async def __call__(self,scope,receive,send):
        path=str(scope.get("path") or "")
        if scope.get("type")=="lifespan":
            await creator(scope,receive,send)
            return
        creator_path=(
            path in {"/","/home","/studio","/health"}
            or path.startswith("/infinity/studio/")
            or path.startswith("/infinity/canonical")
            or path.startswith("/infinity/reality")
        )
        if creator_path:
            await creator(scope,receive,send)
        else:
            await legacy_app(scope,receive,send)

app=CanonicalApplication()
application=app
