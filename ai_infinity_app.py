from __future__ import annotations
"""Single production ASGI entrypoint for AI Infinity."""
from fastapi import FastAPI
import ai_infinity_canonical
import reality_first_3901
import studio_ultimate
import production_graph

app = FastAPI(
    title="AI Infinity — Universal Creator Platform",
    version=ai_infinity_canonical.VERSION,
    docs_url="/infinity/canonical/docs",
    redoc_url=None,
)

# Reality evidence and production graph patch the real Studio before routes mount.
reality_first_3901.install()
production_graph.install()

# Preserve the existing creator/Studio surface.
ai_infinity_canonical.register(app)
studio_ultimate.register(app)
reality_first_3901.register(app)

# Additive professional production-control surface.
production_graph.register(app)

application = app
if not callable(app):
    raise RuntimeError("ai_infinity_app:app is not an ASGI callable")
