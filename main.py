from fastapi import FastAPI
import ai_infinity_canonical
import reality_first_3901
import studio_ultimate

# Render's existing service command is "uvicorn main:app".
# Route that stable entrypoint to the canonical creator application.
app = FastAPI(
    title="AI Infinity — Universal Creator Platform",
    version=ai_infinity_canonical.VERSION,
    docs_url="/infinity/canonical/docs",
    redoc_url=None,
)

reality_first_3901.install()
ai_infinity_canonical.register(app)
studio_ultimate.register(app)
reality_first_3901.register(app)

application = app

if not callable(app):
    raise RuntimeError("main:app is not an ASGI callable")
