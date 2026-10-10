# Final production verification checkpoint: persisted Reality Kernel delivery gate.
import importlib.util
from pathlib import Path
import sys
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
import editorial_review_3625

# Render invokes "uvicorn main:app"; preserve that stable entrypoint.
app = FastAPI(
    title="AI Infinity — Universal Creator Platform",
    version=ai_infinity_canonical.VERSION,
    docs_url="/infinity/canonical/docs",
    redoc_url=None,
)

# TARGET-2050.3704 must be loaded as the importable storage module because
# Creator Studio calls "import ai3704_storage_fabric" while saving/rehydrating
# real artifacts. Inject the live application and its existing database/lock
# before executing the module; otherwise decorators can bind to another app or
# the import can fail after a production job has finished.
_db_lock = studio_ultimate.DB_LOCK
db = studio_ultimate._connect
now = studio_ultimate.now
uid = studio_ultimate._get_user_id
_storage_fabric_path = Path(__file__).resolve().with_name("ai3704_storage_fabric.py")
_storage_spec = importlib.util.spec_from_file_location("ai3704_storage_fabric", _storage_fabric_path)
if _storage_spec is None or _storage_spec.loader is None:
    raise RuntimeError("TARGET-2050.3704 storage fabric module could not be loaded")
_storage_module = importlib.util.module_from_spec(_storage_spec)
_storage_module.app = app
_storage_module.db = db
_storage_module._db_lock = _db_lock
_storage_module.now = now
_storage_module.uid = uid
sys.modules[_storage_spec.name] = _storage_module
try:
    _storage_spec.loader.exec_module(_storage_module)
except Exception:
    sys.modules.pop(_storage_spec.name, None)
    raise

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
del _storage_fabric_path, _storage_spec, _storage_module, _required_storage_routes, _registered_storage_routes, _missing_storage_routes

reality_first_3901.install()
production_graph.install()
production_openai_video.install()
production_closure_3624.install()
editorial_review_3625.install()
ai_infinity_canonical.register(app)
studio_ultimate.register(app)
reality_first_3901.register(app)
production_graph.register(app)
production_intelligence.register(app)
production_openai_video.register(app)
production_closure_3624.register(app)
editorial_review_3625.register(app)
professional_creator_v2_timeline_patch.install(app)
professional_creator_v2.install(app)
professional_creator_fabric.register(app)
# Reapply after creator add-ons so the final served UI retains the review panel.
editorial_review_3625.install()

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
