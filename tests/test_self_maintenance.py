from __future__ import annotations

import json
import pytest
from pathlib import Path
from fastapi import FastAPI, HTTPException

import self_maintenance as maintenance


def test_source_allowlist_permits_application_and_test_files():
    assert maintenance.validate_path("studio_ultimate.py") == "studio_ultimate.py"
    assert maintenance.validate_path("ai_infinity/native_exports.py") == "ai_infinity/native_exports.py"
    assert maintenance.validate_path("tests/test_runtime_truth.py") == "tests/test_runtime_truth.py"
    assert maintenance.validate_path("self_maintenance.py") == "self_maintenance.py"
    assert maintenance.validate_path("editorial_review_3625.py") == "editorial_review_3625.py"
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
        application_version="2050.3624",
        application_build="test-build",
        deployed_revision="a" * 40,
        route_paths=["/", "/infinity/studio/bootstrap", "/infinity/studio/health", "/infinity/studio/self-maintenance/runtime"],
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
    assert result["runtime"]["deployed_revision"] == "a" * 40
    assert result["application"]["version"] == "2050.3624"
    assert result["application"]["required_routes"]["/infinity/studio/health"] is True
    assert result["application"]["required_routes"]["/infinity/storage/v1/status"] is False
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
        application_version="test",
        application_build="test-build",
        deployed_revision=None,
        route_paths=["/"],
        executables={},
        studio_health={"status": "healthy", "media_core": True},
        project_state={"active": True, "verified": True, "snapshot_verified": True},
        asset_storage={"configured_providers": ["backblaze_b2"], "primary_provider": "backblaze_b2"},
        app_state={},
        maintenance_ready=True,
        writer_ready=True,
    )
    assert report["project_state"]["verified"] is True
    assert report["runtime"]["deployed_revision"] is None
    assert report["application"]["route_count"] == 1
    assert report["application"]["required_routes"]["/infinity/studio/bootstrap"] is False
    assert report["asset_storage"]["configured_providers"] == ["backblaze_b2"]
    assert report["asset_storage"]["durability_claimed"] is False
    assert "successful asset writes or read-back" in report["asset_storage"]["note"]




def test_repository_full_mode_can_inspect_workflows_configuration_and_docs(monkeypatch):
    monkeypatch.setenv("AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE", "repository_full")
    assert maintenance.validate_path(".github/workflows/final-acceptance-suite.yml") == ".github/workflows/final-acceptance-suite.yml"
    assert maintenance.validate_path(".ai-infinity/STATE.md") == ".ai-infinity/STATE.md"
    assert maintenance.validate_path("render.yaml") == "render.yaml"
    assert maintenance.validate_path("requirements.txt") == "requirements.txt"
    assert maintenance.validate_path("README.md") == "README.md"
    assert maintenance.validate_path("docs/KNOWN-BLOCKERS.md") == "docs/KNOWN-BLOCKERS.md"


@pytest.mark.parametrize("path", [
    "../main.py", ".env", ".env.production", "secrets/provider.txt",
    "credentials/token.json", "private-keys/signing.pem", ".git/config",
    "AI_Infinity_Creator_Benchmark_and_Reuse_Plan.md",
    "AI_Infinity_10_Method_Deep_Research_and_Master_Command.md",
    "assets/final.mp4",
])
def test_repository_full_mode_still_blocks_secrets_protected_docs_and_binary_assets(monkeypatch, path):
    monkeypatch.setenv("AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE", "repository_full")
    with pytest.raises(HTTPException):
        maintenance.validate_path(path)


def test_repository_full_mode_is_not_active_in_restricted_mode(monkeypatch):
    monkeypatch.setenv("AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE", "restricted")
    with pytest.raises(HTTPException):
        maintenance.validate_path(".github/workflows/final-acceptance-suite.yml")
    assert maintenance.validate_path("tests/test_runtime_truth.py") == "tests/test_runtime_truth.py"


def test_github_request_is_limited_to_configured_repository_and_supported_methods(monkeypatch):
    calls = []
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit=0): return b'{"resources":{"core":{"remaining":42}}}'
    def fake_urlopen(request, timeout=0):
        calls.append((request.method, request.full_url))
        return FakeResponse()
    monkeypatch.setattr(maintenance, "_github_urlopen", fake_urlopen)
    cfg = {"repository": "aurlooij-afk/AI-INFINITY", "github_token": "not-a-real-token"}
    result = maintenance.github_request(cfg, "GET", "/rate_limit")
    assert result["resources"]["core"]["remaining"] == 42
    repo_root = maintenance.github_request(cfg, "GET", "/repos/aurlooij-afk/AI-INFINITY")
    assert repo_root["resources"]["core"]["remaining"] == 42
    assert calls[0][0] == "GET"
    with pytest.raises(HTTPException, match="configured repository"):
        maintenance.github_request(cfg, "GET", "/repos/someone/other-repo")
    with pytest.raises(HTTPException, match="configured repository"):
        maintenance.github_request(cfg, "DELETE", "/repos/aurlooij-afk/OTHER-REPO/git/refs/heads/main")
    with pytest.raises(HTTPException, match="HTTP method"):
        maintenance.github_request(cfg, "TRACE", "/rate_limit")


def test_generic_github_response_redacts_credentials_and_source_content_when_needed():
    redacted = maintenance.redact_github_response({
        "repository": "aurlooij-afk/AI-INFINITY",
        "token": "never-return-this",
        "nested": {"private_key": "never-return-this-either", "ok": True},
    })
    encoded = json.dumps(redacted)
    assert "never-return-this" not in encoded
    assert redacted["nested"]["ok"] is True

    source = 'GEMINI_API_KEY = "abcdefghijklmnopqrstuvwxyz123456"'
    import base64
    row = {"encoding": "base64", "content": base64.b64encode(source.encode()).decode()}
    safe = maintenance.validate_github_content_response("/repos/owner/repo/contents/.env", row)
    assert safe["credential_content_redacted"] is True
    assert safe["content"] == "[REDACTED_CREDENTIAL_DETECTED]"


def test_register_exposes_repository_scoped_github_rest_execution_route():
    app = FastAPI()
    maintenance.register(app)
    paths = {route.path for route in app.routes}
    assert "/infinity/studio/self-maintenance/github/execute" in paths



def test_github_contents_api_guard_rejects_protected_and_secret_files():
    for path in (
        "/repos/aurlooij-afk/AI-INFINITY/contents/.env",
        "/repos/aurlooij-afk/AI-INFINITY/contents/.github/workflows/../.env.production",
        "/repos/aurlooij-afk/AI-INFINITY/contents/AI_Infinity_Creator_Benchmark_and_Reuse_Plan.md",
        "/repos/aurlooij-afk/AI-INFINITY/contents/credentials/provider-key.txt",
    ):
        with pytest.raises(HTTPException):
            maintenance.validate_github_contents_path(path)


def test_github_contents_api_guard_allows_repository_full_text_and_workflow_paths():
    assert maintenance.validate_github_contents_path(
        "/repos/aurlooij-afk/AI-INFINITY/contents/.github/workflows/final-acceptance-suite.yml"
    ) is None
    assert maintenance.validate_github_contents_path(
        "/repos/aurlooij-afk/AI-INFINITY/contents/docs/SELF_MAINTENANCE.md"
    ) is None


def test_github_full_is_default_and_narrower_modes_are_selectable(monkeypatch):
    monkeypatch.setenv("AI_INFINITY_SELF_MAINTENANCE_ENABLED", "true")
    monkeypatch.setenv("AI_INFINITY_MAINTENANCE_REPOSITORY", "aurlooij-afk/AI-INFINITY")
    monkeypatch.setenv("AI_INFINITY_MAINTENANCE_BASE_BRANCH", "main")
    monkeypatch.setenv("AI_INFINITY_MAINTENANCE_GITHUB_TOKEN", "placeholder")
    monkeypatch.setenv("AI_INFINITY_MAINTENANCE_ADMIN_TOKEN", "placeholder-admin")
    monkeypatch.delenv("AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE", raising=False)
    assert maintenance.load_settings()["github_access_mode"] == "github_full"
    monkeypatch.setenv("AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE", "repository_full")
    assert maintenance.load_settings()["github_access_mode"] == "repository_full"
    monkeypatch.setenv("AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE", "github_full")
    assert maintenance.load_settings()["github_access_mode"] == "github_full"


def test_github_full_mode_forwards_rest_and_graphql_requests_across_github_resources(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit=0):
            return self.body

    def fake_urlopen(request, timeout=0):
        calls.append(request)
        if request.full_url.endswith("/user/repos?per_page=100"):
            return FakeResponse(b'[{"full_name":"other-owner/other-repo"}]')
        if request.full_url.endswith("/graphql"):
            return FakeResponse(b'{"data":{"viewer":{"login":"owner"}}}')
        return FakeResponse(b'{"login":"owner","id":123}')

    monkeypatch.setattr(maintenance, "_github_urlopen", fake_urlopen)
    cfg = {
        "repository": "aurlooij-afk/AI-INFINITY",
        "github_token": "owner-token-placeholder",
        "github_access_mode": "github_full",
    }

    user = maintenance.github_request(cfg, "GET", "/user")
    repositories = maintenance.github_request(cfg, "GET", "/user/repos?per_page=100")
    organization = maintenance.github_request(cfg, "GET", "/orgs/example-org")
    graphql = maintenance.github_request(
        cfg, "POST", "/graphql", {"query": "query { viewer { login } }"}
    )

    assert user["login"] == "owner"
    assert repositories == [{"full_name": "other-owner/other-repo"}]
    assert organization["login"] == "owner"
    assert graphql["data"]["viewer"]["login"] == "owner"
    assert calls[0].full_url == "https://api.github.com/user"
    assert calls[2].full_url == "https://api.github.com/orgs/example-org"
    assert calls[3].data == b'{"query": "query { viewer { login } }"}'
    assert all(request.get_header("Authorization") == "Bearer owner-token-placeholder" for request in calls)


@pytest.mark.parametrize("path", [
    "https://attacker.example/repos/owner/repo",
    "//attacker.example/repos/owner/repo",
    "/repos/owner/../other",
    "/repos/owner/%2e%2e/other",
    "/repos/owner/repo#fragment",
])
def test_github_full_mode_rejects_external_hosts_and_path_traversal(path):
    cfg = {
        "repository": "aurlooij-afk/AI-INFINITY",
        "github_token": "owner-token-placeholder",
        "github_access_mode": "github_full",
    }
    with pytest.raises(HTTPException):
        maintenance.github_request(cfg, "GET", path)


def test_github_full_mode_allows_self_maintenance_targeting_another_authorized_repository():
    cfg = {
        "repository": "aurlooij-afk/AI-INFINITY",
        "base_branch": "main",
        "github_access_mode": "github_full",
    }
    target = maintenance.target_repository_config(
        cfg, {"repository": "another-owner/another-repo", "base_branch": "develop"}
    )
    assert target["repository"] == "another-owner/another-repo"
    assert target["base_branch"] == "develop"
    assert cfg["repository"] == "aurlooij-afk/AI-INFINITY"


def test_non_full_mode_does_not_allow_cross_repository_self_maintenance():
    cfg = {
        "repository": "aurlooij-afk/AI-INFINITY",
        "base_branch": "main",
        "github_access_mode": "repository_full",
    }
    with pytest.raises(HTTPException, match="github_full"):
        maintenance.target_repository_config(
            cfg, {"repository": "another-owner/another-repo"}
        )


def test_generic_github_redaction_removes_the_active_token_even_inside_free_text():
    token = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    redacted = maintenance.redact_github_response(
        {"message": f"diagnostic echoed {token} unexpectedly"},
        secret_values=(token,),
    )
    assert token not in json.dumps(redacted)
    assert "[REDACTED]" in redacted["message"]


def test_github_full_mode_keeps_owner_protected_documents_unavailable_for_source_editing():
    assert maintenance.validate_path("docs/SELF_MAINTENANCE.md", access_mode="github_full")
    for path in (
        "AI_Infinity_Creator_Benchmark_and_Reuse_Plan.md",
        "AI_Infinity_10_Method_Deep_Research_and_Master_Command.md",
        ".env.production",
    ):
        with pytest.raises(HTTPException):
            maintenance.validate_path(path, access_mode="github_full")


def test_github_content_response_guard_preserves_non_object_api_results():
    rows = [{"full_name": "owner/repository"}, {"full_name": "another/repository"}]
    assert maintenance.validate_github_content_response(
        "/user/repos?per_page=100", rows
    ) == rows


def test_github_redirect_handler_refuses_cross_origin_and_non_https_redirects():
    from urllib.request import Request

    handler = maintenance._GitHubRedirectHandler()
    request = Request("https://api.github.com/user")
    same_origin = handler.redirect_request(
        request, None, 302, "Found", {}, "https://api.github.com/user/repos"
    )
    cross_origin = handler.redirect_request(
        request, None, 302, "Found", {}, "https://attacker.example/collect"
    )
    downgrade = handler.redirect_request(
        request, None, 302, "Found", {}, "http://api.github.com/user"
    )
    assert same_origin.full_url == "https://api.github.com/user/repos"
    assert cross_origin is None
    assert downgrade is None



def test_load_settings_defaults_to_github_full_without_enabling_missing_credentials(monkeypatch):
    for name in (
        "AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE",
        "AI_INFINITY_MAINTENANCE_REPOSITORY",
        "AI_INFINITY_MAINTENANCE_GITHUB_TOKEN",
        "AI_INFINITY_MAINTENANCE_ADMIN_TOKEN",
        "AI_INFINITY_SELF_MAINTENANCE_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = maintenance.load_settings()
    assert cfg["github_access_mode"] == "github_full"
    assert cfg["ready"] is False


def test_github_full_mode_propagates_to_cross_directory_source_discovery_and_edits(monkeypatch):
    cfg = {
        "repository": "owner/repo",
        "base_branch": "main",
        "github_access_mode": "github_full",
    }
    paths = [
        {"path": ".github/workflows/creator-runtime.yml", "size": 20, "sha": "a" * 40},
        {"path": "tests/test_github_mode.py", "size": 30, "sha": "b" * 40},
    ]
    ranked = maintenance.rank_source_candidates(
        paths, "inspect creator runtime workflow", access_mode=cfg["github_access_mode"]
    )
    assert {row["path"] for row in ranked} == {
        ".github/workflows/creator-runtime.yml", "tests/test_github_mode.py"
    }
    selected = maintenance.select_autonomous_sources(
        ranked, access_mode=cfg["github_access_mode"]
    )
    assert ".github/workflows/creator-runtime.yml" in selected
    assert "tests/test_github_mode.py" in selected

    source_text = {
        ".github/workflows/creator-runtime.yml": "name: creator-runtime\\n",
        "tests/test_github_mode.py": "def test_github_mode():\\n    assert True\\n",
    }
    def fake_read_source(_cfg, path):
        content = source_text[path]
        return {
            "path": path, "sha": "c" * 40,
            "sha256": __import__("hashlib").sha256(content.encode("utf-8")).hexdigest(),
            "size_bytes": len(content.encode("utf-8")), "content": content,
        }
    monkeypatch.setattr(maintenance, "read_source", fake_read_source)
    inspected = maintenance.inspect_sources(cfg, selected, goal="inspect creator runtime workflow")
    originals = {path: fake_read_source(cfg, path) for path in selected}
    proposed = [
        {"path": ".github/workflows/creator-runtime.yml", "content": "name: updated\\n"},
        {"path": "tests/test_github_mode.py", "content": "def test_github_mode():\\n    assert 2 == 2\\n"},
    ]
    expanded = maintenance.apply_window_edits(
        proposed, originals, inspected["files"], access_mode=cfg["github_access_mode"]
    )
    validated = maintenance.validate_changes(
        expanded, set(originals), access_mode=cfg["github_access_mode"]
    )
    assert {row["path"] for row in validated} == set(selected)
