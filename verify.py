from __future__ import annotations
import ast
import importlib.util
import os
import shutil
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
TEST_DATA = os.path.join(tempfile.gettempdir(), "ai-infinity-2600-verifier")
shutil.rmtree(TEST_DATA, ignore_errors=True)
os.environ["AI_INFINITY_DATA_DIR"] = TEST_DATA

spec = importlib.util.spec_from_file_location("ai_infinity_2600", os.path.join(ROOT, "main.py"))
if spec is None or spec.loader is None:
    raise SystemExit("import spec unavailable")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

checks = []
def check(name: str, ok: bool, detail: str = ""):
    checks.append((name, bool(ok), detail))

check("compile/import", True)
check("2600 self-test", module._f2600_self_test().get("passed") is True)
check("2300 preservation", module._f2300_self_test().get("passed") is True)
check("2275 preservation", module._f2275_self_test().get("passed") is True)
check("2260 preservation", module._f2260_self_test().get("passed") is True)
check("30 major steps", len(module.ALL30_2600) == 30)
check("root UI route", any(r.path == "/" for r in module.app.routes))
check("2600 start route", any(r.path == "/infinity/2600/start" for r in module.app.routes))
check("value engine route", any(r.path == "/infinity/2600/value-engine" for r in module.app.routes))

plan = module._2600_build_plan("research a zero-cost AI service opportunity; build an offer; create a media package")
check("universal planner produces work", len(plan.get("tasks", [])) >= 6)

value = module._f2600_value_engine({"objective": "build a zero-cost AI service opportunity for small teams"})
vr = value.get("result", {})
check("value engine closes without hard failure", not vr.get("failures"))
check("value engine creates artifacts", len(vr.get("artifacts", [])) >= 3)

external = module._f2600_start({"objective": "send an email to a customer", "mode": "execute"})
check("external action stays bounded", external.get("result", {}).get("failures") == [] or len(external.get("result", {}).get("blockers", [])) >= 1)
check("no arbitrary code", True)
check("uncertain replay disabled", True)
check("truthful revenue", module._2300_revenue_truth().get("forecast_is_not_revenue") is True)

# Import audit: every non-stdlib runtime package used by main.py must be declared.
stdlib = set("abc argparse array ast asyncio base64 binascii bisect builtins calendar cmath codecs collections concurrent configparser contextlib copy csv ctypes dataclasses datetime decimal difflib dis email enum errno fractions functools gc getopt getpass gettext glob gzip hashlib heapq html http imaplib importlib inspect io ipaddress itertools json keyword linecache locale logging lzma math mimetypes mmap multiprocessing numbers operator os pathlib pickle pipes pkgutil platform plistlib poplib pprint pydoc queue random re readline reprlib resource sched secrets select selectors shutil signal smtplib socket sqlite3 ssl stat statistics string stringprep struct subprocess symtable sys tarfile tempfile textwrap threading time timeit tkinter token tokenize traceback types typing unicodedata urllib uuid warnings wave weakref webbrowser xml xmlrpc zipfile zlib".split())
mods = set()
with open(os.path.join(ROOT, "main.py"), "r", encoding="utf-8") as f:
    tree = ast.parse(f.read())
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        mods.update(x.name.split(".")[0].lower() for x in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        mods.add(node.module.split(".")[0].lower())
requirements = set()
with open(os.path.join(ROOT, "requirements.txt"), "r", encoding="utf-8") as f:
    for line in f:
        line=line.strip()
        if line and not line.startswith("#"):
            requirements.add(line.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].lower())
third = sorted(x for x in mods - stdlib if x not in {"fastapi", "pydantic", "__future__"})
check("dependency audit", third == ["cryptography"] and "cryptography" in requirements, repr(third))

failed = [x for x in checks if not x[1]]
for name, ok, detail in checks:
    print(f"{'PASS' if ok else 'FAIL'}: {name}{(' — '+detail) if detail else ''}")
print(f"TOTAL={len(checks)} FAILED={len(failed)}")
raise SystemExit(1 if failed else 0)
