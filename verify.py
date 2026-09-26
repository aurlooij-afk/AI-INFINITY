from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "main.py"
BASELINE = ROOT / "main_2800_baseline.py"


def fail(message: str) -> None:
    raise SystemExit(f"VERIFY FAILED: {message}")


def route_set(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    routes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            if not (isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"):
                continue
            if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                routes.add(dec.args[0].value)
    return routes


def load_module(data_dir: Path):
    os.environ["AI_INFINITY_DATA_DIR"] = str(data_dir)
    os.environ["AI_INFINITY_OPERATOR_TOKEN"] = "verify-operator"
    os.environ["AI_INFINITY_WALLET_KEY"] = "verify-wallet-key"
    spec = importlib.util.spec_from_file_location("ai_infinity_verify_2801", MAIN)
    if spec is None or spec.loader is None:
        fail("could not load main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def http_json(base: str, path: str, method: str = "GET", body=None, operator: str | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if operator:
        headers["X-AI-Infinity-Operator"] = operator
    req = Request(base + path, data=data, method=method, headers=headers)
    with urlopen(req, timeout=20) as r:
        raw = r.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw
        return r.status, payload


def main() -> int:
    subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], check=True)
    tests = []
    def T(name, ok, detail=None):
        item = {"name": name, "passed": bool(ok)}
        if detail is not None:
            item["detail"] = detail
        tests.append(item)

    if BASELINE.exists():
        old = route_set(BASELINE)
        new = route_set(MAIN)
        missing = sorted(old - new)
        T("legacy routes preserved", not missing, missing[:50])
    else:
        T("baseline route comparison", False, "main_2800_baseline.py not present; route comparison skipped")

    with tempfile.TemporaryDirectory(prefix="ai-infinity-2801-") as td:
        module = load_module(Path(td))
        r = module.infinity_2801_self_test()
        T("internal self-test", r.get("passed") is True, r)
        T("2801 health contract", module.infinity_2801_health().get("version") == "TARGET-2050.2801")
        T("work routing", module._2801_command_route("find software engineering freelance work").get("area") == "work")
        T("finance routing", module._2801_command_route("prepare a finance report").get("area") == "finance")
        T("media routing", module._2801_command_route("make a video").get("area") == "media")
        T("wallet is not custody", module._2801_security_status().get("real_money_custody") is False)
        T("token not persisted in UI", "localStorage.setItem" not in module.INFINITY_2801_UI and "sessionStorage.setItem" not in module.INFINITY_2801_UI)

        # Full HTTP smoke test against a real ASGI process.
        port = "8911"
        env = os.environ.copy()
        env.update({"AI_INFINITY_DATA_DIR": str(Path(td) / "serverdb"), "AI_INFINITY_OPERATOR_TOKEN": "verify-operator", "AI_INFINITY_WALLET_KEY": "verify-wallet-key"})
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--app-dir", str(ROOT), "--host", "127.0.0.1", "--port", port], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        try:
            base = f"http://127.0.0.1:{port}"
            ready = False
            for _ in range(60):
                try:
                    status, payload = http_json(base, "/health")
                    if status == 200:
                        ready = True
                        break
                except Exception:
                    time.sleep(0.25)
            T("ASGI health", ready)
            if not ready:
                fail("ASGI server did not become ready")
            status, ui = http_json(base, "/api/health")
            T("API health", status == 200 and ui.get("version") == "TARGET-2050.2801")
            with urlopen(base + "/", timeout=10) as r0:
                html = r0.read().decode("utf-8")
            T("root interface", r0.status == 200 and "AI Infinity" in html and "Work" in html and "Finance" in html and "Create" in html)
            status, cmd = http_json(base, "/api/command", "POST", {"command":"find software engineering freelance work","execute":False})
            T("command API", status == 200 and cmd.get("area") == "work")
            status, opp = http_json(base, "/api/work/opportunities", "POST", {"source":"verify","title":"Verification Python automation project","description":"Python automation","skills":["python","automation"],"compensation":100}, operator="verify-operator")
            T("opportunity ingest", status == 200 and opp.get("title") == "Verification Python automation project")
            status, matches = http_json(base, "/api/work/matches?limit=5")
            T("opportunity matching", status == 200 and len(matches.get("matches", [])) >= 1)
            status, media = http_json(base, "/api/media/projects", "POST", {"title":"Verification Media","brief":"A short test production","format":"16:9","duration_seconds":12}, operator="verify-operator")
            mid = media.get("project_id")
            T("media project", status == 200 and bool(mid))
            status, mplan = http_json(base, f"/api/media/projects/{mid}/plan", "POST", {}, operator="verify-operator")
            T("media planning", status == 200 and len(mplan.get("plan", {}).get("scenes", [])) >= 3)
            status, fs = http_json(base, "/api/finance/summary?period=30d")
            T("finance summary", status == 200 and "net_minor" in fs)
            status, wc = http_json(base, "/api/wallet/credit", "POST", {"amount":5,"currency":"USD","category":"verify","description":"verification","idempotency_key":"verify-wallet-1"}, operator="verify-operator")
            T("wallet credit", status == 200 and wc.get("status") == "recorded")
            status, wc2 = http_json(base, "/api/wallet/credit", "POST", {"amount":5,"currency":"USD","category":"verify","description":"verification","idempotency_key":"verify-wallet-1"}, operator="verify-operator")
            T("wallet idempotency", status == 200 and wc2.get("status") == "idempotent_replay")
            status, wd = http_json(base, "/api/wallet/debit", "POST", {"amount":2,"currency":"USD","category":"verify","description":"verification","idempotency_key":"verify-wallet-2"}, operator="verify-operator")
            T("wallet debit", status == 200 and wd.get("balance_minor") == 300)
            status, audit = http_json(base, "/api/wallet")
            T("wallet chain", status == 200 and audit.get("audit", {}).get("verified") is True)
            status, fin = http_json(base, "/api/finance/forecast?months=3")
            T("cashflow forecast", status == 200 and len(fin.get("projected_net_minor", [])) == 3)
            status, backup = http_json(base, "/api/backup")
            T("backup export", status == 200 and backup.get("format") == "AI_INFINITY_BACKUP_2801")
            status, st = http_json(base, "/infinity/2801/self-test")
            T("HTTP self-test", status == 200 and st.get("passed") is True)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    result = {
        "status":"completed",
        "version":"TARGET-2050.2801",
        "build":"UNIVERSAL-WORK-EARNINGS-FINANCE-MEDIA-OPERATING-FABRIC",
        "passed":all(t["passed"] for t in tests),
        "tests":tests,
        "truthful":True,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
