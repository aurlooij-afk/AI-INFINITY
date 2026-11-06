from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException

import self_maintenance as maintenance


def test_source_allowlist_permits_application_and_test_files():
    assert maintenance.validate_path("studio_ultimate.py") == "studio_ultimate.py"
    assert maintenance.validate_path("ai_infinity/native_exports.py") == "ai_infinity/native_exports.py"
    assert maintenance.validate_path("tests/test_runtime_truth.py") == "tests/test_runtime_truth.py"
    assert maintenance.validate_path("self_maintenance.py") == "self_maintenance.py"


@pytest.mark.parametrize("path", [
    "../main.py", "/etc/passwd.py", ".env", "secrets/.env.production",
    "render.yaml", ".github/workflows/final-acceptance-suite.yml",
    "AI_Infinity_Creator_Benchmark_and_Reuse_Plan.md", "requirements.txt",
])
def test_source_allowlist_rejects_paths_outside_safe_scope(path):
    with pytest.raises(HTTPException):
        maintenance.validate_path(path)


def test_change_validation_requires_test_file_and_valid_python():
    requested = {"studio_ultimate.py", "tests/test_example.py"}
    valid = [
        {"path": "studio_ultimate.py", "content": "VALUE = 2\n"},
        {"path": "tests/test_example.py", "content": "def test_value():\n    assert 2 == 2\n"},
    ]
    assert maintenance.validate_changes(valid, requested) == valid
    with pytest.raises(HTTPException, match="regression-test"):
        maintenance.validate_changes([valid[0]], requested)
    with pytest.raises(HTTPException, match="syntax error"):
        maintenance.validate_changes([valid[0], {"path": "tests/test_example.py", "content": "def broken(:\n"}], requested)


def test_change_validation_rejects_uninspected_paths_and_hard_coded_secrets():
    requested = {"studio_ultimate.py", "tests/test_example.py"}
    with pytest.raises(HTTPException, match="uninspected"):
        maintenance.validate_changes([
            {"path": "main.py", "content": "VALUE = 1\n"},
            {"path": "tests/test_example.py", "content": "def test_ok(): pass\n"},
        ], requested)
    with pytest.raises(HTTPException, match="secret"):
        maintenance.validate_changes([
            {"path": "studio_ultimate.py", "content": 'GEMINI_API_KEY = "123456789012345678901234"\n'},
            {"path": "tests/test_example.py", "content": "def test_ok(): pass\n"},
        ], requested)


def test_draft_pr_uses_isolated_branch_and_never_merges_or_deploys(monkeypatch):
    calls = []

    def fake_github(_cfg, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET":
            return {"object": {"sha": "a" * 40}}
        if path.endswith("/pulls"):
            return {"number": 456, "html_url": "https://github.com/example/repo/pull/456", "draft": True}
        return {"ok": True}

    monkeypatch.setattr(maintenance, "github_request", fake_github)
    cfg = {"repository": "example/repo", "base_branch": "main", "github_token": "private"}
    changes = [
        {"path": "studio_ultimate.py", "content": "VALUE = 2\n"},
        {"path": "tests/test_example.py", "content": "def test_value():\n    assert 2 == 2\n"},
    ]
    originals = {
        "studio_ultimate.py": {"sha": "b" * 40},
        "tests/test_example.py": {"sha": "c" * 40},
    }
    result = maintenance.create_draft_pr(cfg, changes, originals, "Repair a verified routing defect", "Added regression coverage.")
    assert result["draft"] is True
    assert result["pull_request_number"] == 456
    assert result["branch"].startswith("ai-infinity/self-maintenance/")
    assert result["branch"] != "main"
    assert calls[1][0] == "POST" and calls[1][1].endswith("/git/refs")
    assert all(call[2].get("branch") == result["branch"] for call in calls if call[0] == "PUT")
    pull_payload = calls[-1][2]
    assert pull_payload["draft"] is True
    assert pull_payload["base"] == "main"
    assert all("merge" not in str(call[1]).lower() for call in calls)


def test_register_exposes_explicit_maintenance_routes():
    app = FastAPI()
    maintenance.register(app)
    paths = {route.path for route in app.routes}
    assert "/infinity/studio/self-maintenance/status" in paths
    assert "/infinity/studio/self-maintenance/inspect" in paths
    assert "/infinity/studio/self-maintenance/improve" in paths
