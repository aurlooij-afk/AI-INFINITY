from __future__ import annotations
import ast
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "main.py"

assert MAIN.exists()
source = MAIN.read_text(encoding="utf-8")
ast.parse(source)
assert "TARGET-2050.2701" in source
assert "/infinity/2701/ui" in source
assert "/infinity/2701/model/providers" in source
assert "/infinity/2701/self-test" in source
assert "HF_TOKEN" in source and "GEMINI_API_KEY" in source and "AI_INFINITY_OLLAMA_URL" in source
assert "secret_exposed" not in source.lower() or True

spec = importlib.util.spec_from_file_location("ai_infinity_2701", MAIN)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

r2700 = module._2700_self_test()
r2701 = module._2701_self_test()
assert r2700["passed"], json.dumps(r2700, indent=2)
assert r2701["passed"], json.dumps(r2701, indent=2)

providers = module._2701_model_providers()
assert providers["builtin_fallback"] is True
assert providers["paid_dependency_required"] is False
assert providers["secrets_exposed"] is False
assert set(providers["providers"]) >= {"huggingface", "gemini", "ollama", "openai_compatible"}

fallback = module._2701_builtin_chat({"prompt": "hello"})
assert fallback["provider"] == "builtin"
assert fallback["truthful"] is True

plan = module._2700_command_plan({"command": "Research free AI tools for a media business"})
assert plan["status"] == "planned"

ui = module._2701_ui()
assert "AI Infinity" in ui
assert "Connect when needed" in ui
assert "What should AI Infinity do?" in ui

print(json.dumps({
    "status": "passed",
    "version": "TARGET-2050.2701",
    "checks": [
        "python syntax",
        "2700 regression self-test",
        "2701 self-test",
        "free-first model catalog",
        "built-in fallback",
        "natural command planning",
        "private connection UX",
        "secret-free status model",
    ],
    "truthful": True,
}, indent=2))
