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
    assert "/infinity/studio/self-maintenance/runtime" in paths
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
    assert "/infinity/studio/self-maintenance/ci" in paths


def test_autonomous_source_selection_always_includes_relevant_tests():
    candidates = [
        {"path": "studio_ultimate.py", "match_score": 5},
        {"path": "production_intelligence.py", "match_score": 4},
        {"path": "tests/test_longform_production_planner.py", "match_score": 8},
        {"path": "tests/test_runtime_truth.py", "match_score": 2},
        {"path": ".github/workflows/final-acceptance-suite.yml", "match_score": 20},
    ]
    selected = maintenance.select_autonomous_sources(candidates)
    assert "studio_ultimate.py" in selected
    assert "tests/test_longform_production_planner.py" in selected
    assert all(not path.startswith(".github/") for path in selected)
    assert len(selected) <= maintenance.MAX_FILES


def test_autonomous_source_selection_fails_when_no_relevant_regression_test_exists():
    with pytest.raises(HTTPException, match="regression-test"):
        maintenance.select_autonomous_sources([
            {"path": "studio_ultimate.py", "match_score": 5},
            {"path": "production_intelligence.py", "match_score": 3},
        ])


def test_ci_summary_is_commit_bound_and_exposes_failed_step_names_without_logs():
    runs = [
        {
            "id": 101, "name": "Creator Guard", "head_sha": "a" * 40,
            "head_branch": "main", "status": "completed", "conclusion": "failure",
            "html_url": "https://github.com/example/repo/actions/runs/101",
        },
        {
            "id": 102, "name": "Other Commit", "head_sha": "b" * 40,
            "head_branch": "main", "status": "completed", "conclusion": "success",
        },
    ]
    jobs = {
        101: [{
            "name": "Python tests", "status": "completed", "conclusion": "failure",
            "steps": [
                {"name": "Checkout", "conclusion": "success"},
                {"name": "Longform planner regression", "conclusion": "failure"},
            ],
        }],
        102: [],
    }
    result = maintenance.summarize_ci_runs(runs, jobs, commit_sha="a" * 40)
    assert result["run_count"] == 1
    assert result["runs"][0]["run_id"] == 101
    assert result["runs"][0]["failed_jobs"][0]["failed_steps"] == ["Longform planner regression"]
    assert result["raw_logs_included"] is False
    assert "Other Commit" not in str(result)


def test_inspect_ci_queries_exact_head_sha_and_summarizes_jobs(monkeypatch):
    calls = []

    def fake_github(_cfg, method, path, payload=None):
        calls.append((method, path))
        if path.startswith("/repos/example/repo/actions/runs?"):
            return {"workflow_runs": [{
                "id": 77, "name": "Acceptance", "head_sha": "c" * 40,
                "head_branch": "feature", "status": "completed", "conclusion": "success",
            }]}
        if path.endswith("/actions/runs/77/jobs?per_page=100"):
            return {"jobs": [{"name": "Acceptance", "status": "completed", "conclusion": "success", "steps": []}]}
        raise AssertionError(path)

    monkeypatch.setattr(maintenance, "github_request", fake_github)
    cfg = {"repository": "example/repo", "base_branch": "main"}
    result = maintenance.inspect_ci(cfg, commit_sha="c" * 40, limit=5)
    assert result["run_count"] == 1
    assert "head_sha=" + "c" * 40 in calls[0][1]
    assert "branch=main" not in calls[0][1]

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

def test_production_creator_ui_exposes_protected_self_maintenance_console():
    root = Path(__file__).resolve().parents[1]
    html = (root / "creator_studio_2030.html").read_text(encoding="utf-8")
    studio = (root / "studio_ultimate.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")
    assert '<section id="maintenance" class="section">' in html
    assert 'data-target="maintenance"' in html
    assert 'id="maintenanceToken2030" class="field" type="password" autocomplete="off"' in html
    assert 'X-AI-Infinity-Maintenance-Token' in html
    assert 'function maintenanceDiscover2030()' in html
    assert 'function maintenanceInspect2030()' in html
    assert 'function maintenanceImprove2030()' in html
    assert 'function maintenanceCI2030()' in html
    assert 'window.location.protocol!=="https:"' in html
    script_start = html.index("let maintenanceCandidates2030=")
    script_end = html.index("function fillCmd(", script_start)
    maintenance_script = html[script_start:script_end]
    assert "localStorage" not in maintenance_script
    assert "sessionStorage" not in maintenance_script
    # Bind the UI test to the real production root, not the legacy 3800 shell.
    assert '_CREATOR_2030_UI_PATH = Path(__file__).with_name("creator_studio_2030.html")' in studio
    assert 'HTMLResponse(CREATOR_STUDIO_2030_UI)' in studio
    assert 'studio_ultimate.register(app)' in main
    assert 'self_maintenance.register(app)' in main

@pytest.mark.parametrize("source", [
    'token = "abcdefghijklmnopqrstuvwxyz123456"',
    'secret = "this-is-a-long-fake-secret-value"',
    'GEMINI_API_KEY = "abcdefghijklmnopqrstuvwxyz123456"',
    'os.environ["HF_TOKEN"] = "abcdefghijklmnopqrstuvwxyz123456"',
    'SUPABASE_SERVICE_ROLE_KEY = "abcdefghijklmnopqrstuvwxyz123456"',
    'worker_access_token = "abcdefghijklmnopqrstuvwxyz123456"',
    'value = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"',
    'value = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"',
    'value = "github_pat_abcdefghijklmnopqrstuvwxyz1234567890"',
    'value = "hf_abcdefghijklmnopqrstuvwxyz1234567890"',
])
def test_embedded_secret_detector_catches_common_credential_shapes(source):
    assert maintenance.contains_embedded_secret(source) is True


@pytest.mark.parametrize("source", [
    'token = "temporary-local-preview"',
    'message = "The secret is never stored in browser localStorage."',
    'description = "Use the GitHub API key setting from the environment."',
    'def build_token_label():\n    return "not a credential"',
])
def test_embedded_secret_detector_avoids_ordinary_non_credential_text(source):
    assert maintenance.contains_embedded_secret(source) is False


def test_change_validation_rejects_generic_token_variable_and_provider_token_prefixes():
    requested = {"studio_ultimate.py", "tests/test_example.py"}
    for source in (
        'token = "abcdefghijklmnopqrstuvwxyz123456"\n',
        'credential = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"\n',
        'os.environ["AI_INFINITY_WAN_WORKER_TOKEN"] = "abcdefghijklmnopqrstuvwxyz123456"\n',
    ):
        with pytest.raises(HTTPException, match="secret"):
            maintenance.validate_changes([
                {"path": "studio_ultimate.py", "content": source},
                {"path": "tests/test_example.py", "content": "def test_ok(): pass\n"},
            ], requested)

def test_runtime_report_is_useful_and_redacts_sensitive_health_and_storage_fields():
    result = maintenance.build_runtime_report(
        python_version="3.11.9 (main, test build)",
        platform_name="Linux-6.8.0-x86_64",
        executables={"ffmpeg": "/usr/bin/ffmpeg", "ffprobe": "/usr/bin/ffprobe", "espeak-ng": None},
        studio_health={
            "status": "healthy",
            "media_core": True,
            "ffmpeg": True,
            "ffprobe": True,
            "offline_tts": False,
            "api_key": "do-not-return-this",
            "last_error": "credential-bearing raw provider exception",
        },
        project_state={
            "enabled": True,
            "contract_enabled": True,
            "active": False,
            "verified": False,
            "snapshot_verified": False,
            "object_key": "private/internal/path",
            "last_error": "private database error",
        },
        asset_storage={
            "configured_providers": ["backblaze_b2", "supabase", "unknown_provider"],
            "primary_provider": "backblaze_b2",
            "access_key": "never-return-this",
        },
        app_state={
            "ai_infinity_storage_routes_registered": True,
            "ai_infinity_3800": False,
            "production_hardening": {"status": "applied", "secret": "no"},
        },
        maintenance_ready=True,
        writer_ready=False,
    )
    encoded = json.dumps(result)
    assert result["creator_studio"]["status"] == "healthy"
    assert result["runtime"]["executables"]["ffmpeg"] is True
    assert result["project_state"]["active"] is False
    assert result["asset_storage"]["configured_providers"] == ["backblaze_b2", "supabase"]
    assert result["asset_storage"]["durability_claimed"] is False
    assert result["application"]["storage_routes_registered"] is True
    assert result["self_maintenance"]["configured_and_ready"] is True
    assert result["self_maintenance"]["free_tier_writer_ready"] is False
    for sensitive in ("do-not-return-this", "credential-bearing raw provider exception",
                      "private/internal/path", "private database error",
                      "never-return-this", "access_key", "api_key", "last_error"):
        assert sensitive not in encoded


def test_runtime_inspection_does_not_report_object_storage_configuration_as_durability(monkeypatch):
    report = maintenance.build_runtime_report(
        python_version="3.11",
        platform_name="test",
        executables={},
        studio_health={"status": "healthy", "media_core": True},
        project_state={"active": True, "verified": True, "snapshot_verified": True},
        asset_storage={"configured_providers": ["backblaze_b2"], "primary_provider": "backblaze_b2"},
        app_state={},
        maintenance_ready=True,
        writer_ready=True,
    )
    assert report["project_state"]["verified"] is True
    assert report["asset_storage"]["configured_providers"] == ["backblaze_b2"]
    assert report["asset_storage"]["durability_claimed"] is False
    assert "successful asset writes or read-back" in report["asset_storage"]["note"]

