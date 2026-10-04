from pathlib import Path
import importlib
import uvicorn

BASE = Path(__file__).resolve().parent
main = importlib.import_module("main")

# Assemble the verified 3700 overlay without replacing the mature foundation.
main.FINAL3700_UI = (BASE / "ui_3700.html").read_text(encoding="utf-8")
overlay = "".join((BASE / name).read_text(encoding="utf-8") for name in (
    "backend_3700_01.part", "backend_3700_02.part", "backend_3700_03.part"
))
exec(compile(overlay, str(BASE / "backend_3700_overlay.py"), "exec"), main.__dict__)

def _canonical_root():
    return main.HTMLResponse(main.FINAL3700_UI)

for route in main.app.router.routes:
    if getattr(route, "path", None) == "/":
        route.endpoint = _canonical_root
        break

main.app.title = "AI Infinity"
main.app.version = "TARGET-2050.3700"
main.app.state.ai_infinity_3700 = True

if __name__ == "__main__":
    uvicorn.run(main.app, host="0.0.0.0", port=int(__import__("os").environ.get("PORT", "10000")))
