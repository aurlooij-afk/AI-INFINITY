# Final production verification checkpoint: persisted Reality Kernel delivery gate.
from pathlib import Path
from fastapi import FastAPI, Request, Response
import ai_infinity_canonical
import reality_first_3901
import studio_ultimate
import production_graph
import production_intelligence
import production_openai_video
import production_closure_3624
import professional_creator_v2_timeline_patch
import professional_creator_v2
import professional_creator_fabric

# Render invokes "uvicorn main:app"; preserve that stable entrypoint.
app = FastAPI(
    title="AI Infinity — Universal Creator Platform",
    version=ai_infinity_canonical.VERSION,
    docs_url="/infinity/canonical/docs",
    redoc_url=None,
)

# TARGET-2050.3704 is a shared-namespace integration layer, not a standalone
# Python router module: its routes bind APP from globals()["app"] and use the
# same SQLite connection/lock as Creator Studio. Load it in the serving
# main:app namespace only after the actual FastAPI app exists. A normal import
# would bind a different module namespace and leave these endpoints unmounted.
_db_lock = studio_ultimate.DB_LOCK
db = studio_ultimate._connect
now = studio_ultimate.now
uid = studio_ultimate._get_user_id
_storage_fabric_path = Path(__file__).resolve().with_name("ai3704_storage_fabric.py")
exec(
    compile(
        _storage_fabric_path.read_text(encoding="utf-8"),
        str(_storage_fabric_path),
        "exec",
    ),
    globals(),
)
_required_storage_routes = {
    "/infinity/storage/v1/status",
    "/infinity/storage/v1/verify/{asset_id}",
}
_registered_storage_routes = {
    str(getattr(route, "path", "")) for route in app.router.routes
}
_missing_storage_routes = _required_storage_routes - _registered_storage_routes
if _missing_storage_routes:
    raise RuntimeError(
        "TARGET-2050.3704 storage route registration failed: "
        + ", ".join(sorted(_missing_storage_routes))
    )
app.state.ai_infinity_storage_routes_registered = True
del _storage_fabric_path, _required_storage_routes, _registered_storage_routes, _missing_storage_routes

reality_first_3901.install()
production_graph.install()
production_openai_video.install()
production_closure_3624.install()
ai_infinity_canonical.register(app)
studio_ultimate.register(app)
reality_first_3901.register(app)
production_graph.register(app)
production_intelligence.register(app)
production_openai_video.register(app)
production_closure_3624.register(app)
professional_creator_v2_timeline_patch.install(app)
professional_creator_v2.install(app)
professional_creator_fabric.register(app)

# Canonical compatibility endpoint used by the production proof and legacy clients.
# It delegates to the same real Creator Studio enqueue path; no simulated output.
async def _divine_create_compat(request: Request, response: Response):
    payload = await request.json()
    uid_value = studio_ultimate._get_user_id(request)
    req = dict(payload or {})
    req["professional_pipeline"] = True
    if not req.get("title"):
        req["title"] = str(req.get("objective") or req.get("topic") or "AI Infinity production")[:180]
    studio_ultimate._set_session(response, request, uid_value)
    return studio_ultimate.enqueue(req, uid_value, None)

app.add_api_route("/infinity/divine/create", _divine_create_compat, methods=["POST"], include_in_schema=False)

application = app

if not callable(app):
    raise RuntimeError("main:app is not an ASGI callable")
