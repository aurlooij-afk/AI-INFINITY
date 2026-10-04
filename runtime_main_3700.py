import importlib
from pathlib import Path

foundation = importlib.import_module("foundation")
BASE = Path(__file__).resolve().parent

foundation.FINAL3700_UI = (BASE / "ui_3700.html").read_text(encoding="utf-8")
overlay = "".join((BASE / name).read_text(encoding="utf-8") for name in (
    "backend_3700_01.part", "backend_3700_02.part", "backend_3700_03.part"
))
exec(compile(overlay, str(BASE / "backend_3700_overlay.py"), "exec"), foundation.__dict__)

def _canonical_root():
    return foundation.HTMLResponse(foundation.FINAL3700_UI)

for route in foundation.app.router.routes:
    if getattr(route, "path", None) == "/":
        route.endpoint = _canonical_root
        break

foundation.app.title = "AI Infinity"
foundation.app.version = "TARGET-2050.3700"
foundation.app.state.ai_infinity_3700 = True

app = foundation.app
