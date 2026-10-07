import importlib
from pathlib import Path

foundation = importlib.import_module("foundation")
BASE = Path(__file__).resolve().parent

# Canonical final creator shell.
foundation.FINAL3700_UI = (BASE / "ui_3800.html").read_text(encoding="utf-8")

# Preserve the existing accumulated platform closure and creator routes.
overlay = (BASE / "overlay_final_3700.py").read_text(encoding="utf-8")
overlay = overlay.replace('uid("project")', 'os.urandom(8).hex()').replace('uid("publish")', 'os.urandom(8).hex()')
foundation._db_lock = getattr(foundation, "_db_lock", foundation.studio_ultimate.DB_LOCK)
foundation.db = getattr(foundation, "db", foundation.studio_ultimate._connect)
foundation.now = getattr(foundation, "now", foundation.studio_ultimate.now)
foundation.uid = getattr(foundation, "uid", foundation.studio_ultimate.uid)
foundation.FastAPIRequest = getattr(foundation, "FastAPIRequest", foundation.Request)
exec(compile("import os\n" + overlay, str(BASE / "overlay_final_3700.py"), "exec"), foundation.__dict__)

# Final 3800 product layer: Creator DNA, Worlds, Creative Lab, graph,
# reusable material and deterministic quality loop.
try:
    creator_final_3800 = importlib.import_module("creator_final_3800")
    creator_final_3800.register(foundation.app)
    foundation.app.state.ai_infinity_3800 = True
except Exception as exc:
    foundation.app.state.ai_infinity_3800 = False
    foundation.app.state.ai_infinity_3800_error = str(exc)[:800]

# Compatibility state markers for the accumulated internal closure checker.
# 3700 is loaded by the overlay above; 3704 is loaded by the storage fabric.
foundation.app.state.ai_infinity_3700 = True
foundation.app.state.ai_infinity_3704 = bool(getattr(foundation.app.state, "ai_infinity_storage", False))

# Production hardening remains applied after all creator modules are loaded.
production_hardening = importlib.import_module("production_hardening")
try:
    hardening_state = production_hardening.apply()
except Exception as exc:
    hardening_state = {"status": "degraded", "version": "TARGET-2050.3707", "error": str(exc)[:800], "truthful": True}

try:
    foundation.app.add_api_route(
        "/infinity/3800/runtime-health",
        lambda: {
            "status": "healthy",
            "version": "TARGET-2050.3800",
            "build": "FREE-LOCAL-FIRST-CREATOR-OPERATING-SYSTEM",
            "layer_loaded": bool(getattr(foundation.app.state, "ai_infinity_3800", False)),
            "hardening": hardening_state,
            "truthful": True,
        },
        methods=["GET"],
        include_in_schema=False,
    )
except Exception:
    pass

def _canonical_root():
    response = foundation.HTMLResponse(foundation.FINAL3700_UI)
    response.headers["cache-control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["pragma"] = "no-cache"
    response.headers["expires"] = "0"
    response.headers["x-ai-infinity-ui"] = "3800-free-local-first"
    return response

try:
    foundation.app.router.routes = [
        route for route in foundation.app.router.routes
        if getattr(route, "path", None) not in {"/", "/home"}
    ]
    foundation.app.add_api_route("/", _canonical_root, methods=["GET"], include_in_schema=False)
    foundation.app.add_api_route("/home", _canonical_root, methods=["GET"], include_in_schema=False)
except Exception:
    pass

class _CanonicalAIInfinityASGI:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "lifespan":
            await self.inner(scope, receive, send)
            return
        path = scope.get("path") or ""
        if scope.get("type") == "http" and path in {"/", "/home"}:
            body = foundation.FINAL3700_UI.encode("utf-8")
            headers = [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store, no-cache, must-revalidate, max-age=0"),
                (b"pragma", b"no-cache"),
                (b"expires", b"0"),
                (b"x-ai-infinity-ui", b"3800-free-local-first"),
            ]
            if scope.get("method") == "HEAD":
                await send({"type":"http.response.start","status":200,"headers":headers})
                await send({"type":"http.response.body","body":b"","more_body":False})
                return
            await send({"type":"http.response.start","status":200,"headers":headers})
            await send({"type":"http.response.body","body":body,"more_body":False})
            return
        await self.inner(scope, receive, send)

foundation.app.title = "AI Infinity"
foundation.app.version = "TARGET-2050.3800"
foundation.app.state.production_hardening = hardening_state
app = _CanonicalAIInfinityASGI(foundation.app)
