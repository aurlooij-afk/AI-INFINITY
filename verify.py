from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "main.py"


def fail(message: str) -> None:
    raise SystemExit(f"VERIFY FAILED: {message}")


def main() -> int:
    subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], check=True)

    spec = importlib.util.spec_from_file_location("ai_infinity_2800_verify", MAIN)
    if spec is None or spec.loader is None:
        fail("could not load main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    checks: list[str] = []

    r2700 = module._2700_self_test()
    if not r2700.get("passed"):
        fail("2700 regression self-test failed")
    checks.append("2700 regression self-test")

    r2701 = module._2701_self_test()
    if not r2701.get("passed"):
        fail("2701 regression self-test failed")
    checks.append("2701 regression self-test")

    r2800 = module.final_2800_self_test()
    if not r2800.get("passed"):
        fail("2800 self-test failed: " + json.dumps(r2800, ensure_ascii=False))
    checks.append("2800 self-test")

    health = module.final_health()
    if health.get("version") != "TARGET-2050.2800":
        fail("final health version mismatch")
    if health.get("truthful") is not True:
        fail("final health is not truthful")
    if not health.get("interface", {}).get("desktop") or not health.get("interface", {}).get("mobile"):
        fail("responsive interface flags missing")
    checks.append("final health")

    ui = module.FINAL_INFINITY_UI
    required_ui = [
        "Home", "Chat", "Command", "Plans", "Missions", "Work", "Create",
        "Memory", "Connections", "Settings", "SpeechRecognition", "speechSynthesis",
        "Media Studio", "openMore()", "loadActivity", "#F8F8F5", "/infinity/2800/health",
    ]
    missing = [x for x in required_ui if x not in ui]
    if missing:
        fail("UI missing: " + ", ".join(missing))
    if "sessionStorage.setItem('aiInfinityToken'" in ui or 'sessionStorage.setItem("aiInfinityToken"' in ui:
        fail("operator token is persisted in browser storage")
    checks.append("desktop/mobile workspace UI")

    chat = module.final_chat(module.FinalChatRequest(message="Hello AI Infinity"))
    if chat.get("status") != "completed" or chat.get("truthful") is not True:
        fail("built-in chat fallback failed")
    if not chat.get("message"):
        fail("chat returned empty response")
    checks.append("chat fallback")

    mem = module.final_memory_add(module.FinalMemoryRequest(text="TARGET-2050.2800 verification"))
    if mem.get("status") != "stored":
        fail("memory store failed")
    checks.append("persistent memory")

    conn = module.final_connections()
    if conn.get("truthful") is not True:
        fail("connection response not marked truthful")
    if "aiInfinityToken" in json.dumps(conn, ensure_ascii=False):
        fail("connection payload contains browser token")
    checks.append("secret-safe connections")

    paths = {getattr(route, "path", "") for route in module.app.routes}
    for path in [
        "/infinity/2800/health",
        "/infinity/2800/self-test",
        "/infinity/final/ui",
        "/infinity/final/chat",
        "/infinity/final/command/plan",
        "/infinity/final/command/execute",
        "/infinity/final/mission/start",
        "/infinity/final/media",
        "/infinity/final/connections",
    ]:
        if path not in paths:
            fail(f"missing route {path}")
    checks.append("route catalog")

    print(json.dumps({
        "status": "passed",
        "version": module.FINAL_INFINITY_VERSION,
        "build": module.FINAL_INFINITY_BUILD,
        "checks": checks,
        "truthful": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
