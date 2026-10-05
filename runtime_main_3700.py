import importlib
from pathlib import Path

foundation = importlib.import_module("foundation")
BASE = Path(__file__).resolve().parent
foundation.FINAL3700_UI = (BASE / "ui_3700.html").read_text(encoding="utf-8")
overlay = (BASE / "overlay_final_3700.py").read_text(encoding="utf-8")
overlay = overlay.replace('uid("project")', 'os.urandom(8).hex()').replace('uid("publish")', 'os.urandom(8).hex()')
exec(compile("import os\n" + overlay, str(BASE / "overlay_final_3700.py"), "exec"), foundation.__dict__)

def _canonical_root():
    response = foundation.HTMLResponse(foundation.FINAL3700_UI)
    response.headers["cache-control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["pragma"] = "no-cache"
    response.headers["expires"] = "0"
    response.headers["x-ai-infinity-ui"] = "3706-canonical"
    return response

# Remove legacy UI entry routes so route order cannot select the old 3623 shell.
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
    """Response-level guard preventing legacy HTML from becoming the public shell."""
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "lifespan":
            await self.inner(scope, receive, send)
            return
        path = scope.get("path") or ""
        if scope.get("type") == "http" and path in {"/", "/home", "/studio", "/infinity/studio"}:
            body = foundation.FINAL3700_UI.encode("utf-8")
            if scope.get("method") == "HEAD":
                await send({"type":"http.response.start","status":200,"headers":[
                    (b"content-type",b"text/html; charset=utf-8"),
                    (b"content-length",str(len(body)).encode("ascii")),
                    (b"cache-control",b"no-store, no-cache, must-revalidate, max-age=0"),
                    (b"x-ai-infinity-ui",b"3706-canonical"),
                ]})
                await send({"type":"http.response.body","body":b"","more_body":False})
                return
            await send({"type":"http.response.start","status":200,"headers":[
                (b"content-type",b"text/html; charset=utf-8"),
                (b"content-length",str(len(body)).encode("ascii")),
                (b"cache-control",b"no-store, no-cache, must-revalidate, max-age=0"),
                (b"pragma",b"no-cache"),
                (b"expires",b"0"),
                (b"x-ai-infinity-ui",b"3706-canonical"),
            ]})
            await send({"type":"http.response.body","body":body,"more_body":False})
            return
        await self.inner(scope, receive, send)

foundation.app.title = "AI Infinity"
foundation.app.version = "TARGET-2050.3706"
foundation.app.state.ai_infinity_3700 = True
foundation.app.state.ai_infinity_3702 = True
foundation.app.state.ai_infinity_3703 = True
foundation.app.state.ai_infinity_3704 = True
foundation.app.state.ai_infinity_3705 = True
foundation.app.state.ai_infinity_3706 = True
app = _CanonicalAIInfinityASGI(foundation.app)
