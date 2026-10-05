import importlib
from pathlib import Path

foundation = importlib.import_module("foundation")
BASE = Path(__file__).resolve().parent
foundation.FINAL3700_UI = (BASE / "ui_3700.html").read_text(encoding="utf-8")
overlay = (BASE / "overlay_final_3700.py").read_text(encoding="utf-8")
overlay = overlay.replace('uid("project")', 'os.urandom(8).hex()').replace('uid("publish")', 'os.urandom(8).hex()')
exec(compile("import os\n" + overlay, str(BASE / "overlay_final_3700.py"), "exec"), foundation.__dict__)

def _canonical_root():
    return foundation.HTMLResponse(foundation.FINAL3700_UI)

for route in foundation.app.router.routes:
    if getattr(route, "path", None) == "/":
        route.endpoint = _canonical_root
        break

# Render probes and browsers commonly issue HEAD / before GET /.
# Register an explicit lightweight HEAD route so the platform shell is probe-safe.
try:
    foundation.app.add_api_route("/", _canonical_root, methods=["HEAD"], include_in_schema=False)
except Exception:
    pass

foundation.app.title = "AI Infinity"
foundation.app.version = "TARGET-2050.3704"
foundation.app.state.ai_infinity_3700 = True
foundation.app.state.ai_infinity_3702 = True
foundation.app.state.ai_infinity_3703 = True
foundation.app.state.ai_infinity_3704 = True
app = foundation.app
