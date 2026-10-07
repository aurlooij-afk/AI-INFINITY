from __future__ import annotations
"""Single production ASGI entrypoint for AI Infinity.

The deployed object is a real FastAPI application. Canonical Creator owns the
public root and the mature Creator Studio is registered on the same application,
so there is no module-as-ASGI dispatch layer and no competing root application.
"""

from fastapi import FastAPI

import ai_infinity_canonical
import reality_first_3901
import studio_ultimate

app = FastAPI(
    title="AI Infinity — Universal Creator Platform",
    version=ai_infinity_canonical.VERSION,
    docs_url="/infinity/canonical/docs",
    redoc_url=None,
)

# Install the independent evidence kernel before route registration. install()
# is idempotent and patches the already-loaded Studio implementation in place.
reality_first_3901.install()

# One application owns both the canonical creator experience and the mature
# Studio/API surface. Route order is intentional: canonical root wins, while
# every /infinity/studio/* capability remains directly available.
ai_infinity_canonical.register(app)
studio_ultimate.register(app)
reality_first_3901.register(app)

# Both exported names reference the exact same callable ASGI object.
application = app

if not callable(app):  # pragma: no cover - defensive startup invariant
    raise RuntimeError("ai_infinity_app:app is not an ASGI callable")
