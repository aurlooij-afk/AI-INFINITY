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
            await foundation(scope,receive,send)

app=CanonicalApplication()
application=app
