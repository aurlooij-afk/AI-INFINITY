from __future__ import annotations

import pytest
from pathlib import Path
from fastapi import FastAPI, HTTPException

import self_maintenance as maintenance


def test_source_allowlist_permits_application_and_test_files():
    assert maintenance.validate_path("studio_ultimate.py") == "studio_ultimate.py"
    assert maintenance.validate_path("ai_infinity/native_exports.py") == "ai_infinity/native_exports.py"
    assert maintenance.validate_path("tests/test_runtime_truth.py") == "tests/test_runtime_truth.py"
    assert maintenance.validate_path("self_maintenance.py") == "self_maintenance.py"
    assert maintenance.validate_path("scripts/verify_live_deployment.py") == "scripts/verify_live_deployment.py"
    assert maintenance.validate_path("new_root_runtime_module.py") == "new_root_runtime_module.py"
    assert maintenance.validate_path("creator_studio_2030.html") == "creator_studio_2030.html"
    assert maintenance.validate_path("ui_3800.html") == "ui_3800.html"


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

def test_repository_discovery_ranks_relevant_python_modules_and_excludes_unsafe_paths():
    candidates = maintenance.rank_source_candidates(
        [
            {"path": "studio_ultimate.py", "size": 790000, "sha": "a" * 40},
            {"path": "tests/test_longform_production_planner.py", "size": 12000, "sha": "b" * 40},
            {"path": "scripts/verify_live_deployment.py", "size": 8000, "sha": "c" * 40},
            {"path": ".github/workflows/final-acceptance-suite.yml", "size": 5000, "sha": "d" * 40},
            {"path": "requirements.txt", "size": 1000, "sha": "e" * 40},
        ],
        "repair long-form narration script preflight and add regression tests",
        limit=10,
    )
    paths = [row["path"] for row in candidates]
    assert "tests/test_longform_production_planner.py" in paths
    assert "scripts/verify_live_deployment.py" in paths
    assert "studio_ultimate.py" in paths
    assert ".github/workflows/final-acceptance-suite.yml" not in paths
    assert "requirements.txt" not in paths
    assert candidates[0]["match_score"] >= candidates[-1]["match_score"]


def test_repository_discovery_route_is_registered():
    app = FastAPI()
    maintenance.register(app)
    paths = {route.path for route in app.routes}
    assert "/infinity/studio/self-maintenance/discover" in paths

def test_production_docker_image_copies_and_compiles_self_maintenance_module():
    root = Path(__file__).resolve().parents[1]
    docker_lines = (root / "Dockerfile").read_text(encoding="utf-8").splitlines()
    copy_lines = [line for line in docker_lines if line.startswith("COPY ")]
    compile_start = next(i for i, line in enumerate(docker_lines) if line.strip().startswith("RUN python -m py_compile"))
    compile_lines = docker_lines[compile_start:compile_start + 7]
    assert any("self_maintenance.py" in line and line.rstrip().endswith("./") for line in copy_lines)
    assert compile_lines[-1].strip().endswith("self_maintenance.py")
    assert "self_maintenance.py" not in " ".join(compile_lines[1:-1])
    assert all(line.endswith(chr(92)) for line in compile_lines[:-1])

def _large_core_source() -> str:
    prefix = []
    suffix = []
    for index in range(900):
        prefix.extend([f"def unrelated_before_{index}():", f"    return {index}"])
        suffix.extend([f"def unrelated_after_{index}():", f"    return {index}"])
    target = [
        "def generate_longform_narration():",
        "    return 'original narration planner'",
    ]
    return "\n".join(prefix + target + suffix) + "\n"


def test_large_core_module_uses_bounded_hash_pinned_source_windows():
    source = _large_core_source()
    context, ranges, mode = maintenance._bounded_source_context(
        source, "repair longform narration generator", "studio_ultimate.py"
    )
    assert mode == "line_ranges"
    assert len(context.encode("utf-8")) < len(source.encode("utf-8"))
    assert len(context.encode("utf-8")) <= maintenance.MAX_CONTEXT_FILE_BYTES
    assert len(ranges) == 1
    assert "generate_longform_narration" in context
    assert ranges[0]["start_line"] > 1
    assert ranges[0]["end_line"] < len(source.splitlines())


def test_large_core_module_edit_replaces_only_the_inspected_hash_pinned_range():
    source = _large_core_source()
    context, ranges, mode = maintenance._bounded_source_context(
        source, "repair longform narration generator", "studio_ultimate.py"
    )
    assert mode == "line_ranges"
    original = {
        "path": "studio_ultimate.py",
        "sha": "a" * 40,
        "sha256": __import__("hashlib").sha256(source.encode("utf-8")).hexdigest(),
        "content": source,
    }
    inspected = [{
        "path": "studio_ultimate.py",
        "content": context,
        "content_mode": mode,
        "editable_ranges": ranges,
    }]
    changed = maintenance.apply_window_edits(
        [{
            "path": "studio_ultimate.py",
            "edits": [{
                "start_line": ranges[0]["start_line"],
                "end_line": ranges[0]["end_line"],
                "expected_sha256": ranges[0]["sha256"],
                "replacement_text": (
                    "def generate_longform_narration():\n"
                    "    return 'revised, evidence-aware narration planner'"
                ),
            }],
        }],
        {"studio_ultimate.py": original},
        inspected,
    )
    updated = changed[0]["content"]
    assert "unrelated_before_0" in updated
    assert "unrelated_after_899" in updated
    assert "revised, evidence-aware narration planner" in updated
    assert "original narration planner" not in updated
    import ast
    ast.parse(updated)


def test_large_core_module_edit_rejects_stale_or_uninspected_range_hash():
    source = _large_core_source()
    context, ranges, mode = maintenance._bounded_source_context(
        source, "repair longform narration generator", "studio_ultimate.py"
    )
    original = {"path": "studio_ultimate.py", "sha": "a" * 40, "content": source}
    inspected = [{
        "path": "studio_ultimate.py",
        "content": context,
        "content_mode": mode,
        "editable_ranges": ranges,
    }]
    with pytest.raises(HTTPException, match="hash-pinned"):
        maintenance.apply_window_edits(
            [{
                "path": "studio_ultimate.py",
                "edits": [{
                    **ranges[0],
                    "expected_sha256": "0" * 64,
                    "replacement_text": "def generate_longform_narration():\n    return 'unsafe'",
                }],
            }],
            {"studio_ultimate.py": original},
            inspected,
        )

def test_self_maintenance_validates_allowlisted_ui_html_and_requires_python_regression_test():
    requested = {"creator_studio_2030.html", "tests/test_ui_revision.py"}
    changes = [
        {
            "path": "creator_studio_2030.html",
            "content": "<!doctype html>\n<html><head><title>AI Infinity</title></head><body><main>Creator Studio</main></body></html>\n",
        },
        {
            "path": "tests/test_ui_revision.py",
            "content": "def test_creator_studio_revision():\n    assert 'Creator Studio' in 'Creator Studio'\n",
        },
    ]
    assert maintenance.validate_changes(changes, requested) == changes
    with pytest.raises(HTTPException, match="regression-test"):
        maintenance.validate_changes([changes[0]], {"creator_studio_2030.html"})

