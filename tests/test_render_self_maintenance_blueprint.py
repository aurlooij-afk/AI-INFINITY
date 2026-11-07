from pathlib import Path
import re


def test_render_blueprint_declares_owner_authorized_github_runtime_without_committing_secrets():
    root = Path(__file__).resolve().parents[1]
    source = (root / "render.yaml").read_text(encoding="utf-8")

    expected_values = {
        "AI_INFINITY_SELF_MAINTENANCE_ENABLED": '"true"',
        "AI_INFINITY_MAINTENANCE_REPOSITORY": '"aurlooij-afk/AI-INFINITY"',
        "AI_INFINITY_MAINTENANCE_BASE_BRANCH": '"main"',
        "AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE": '"github_full"',
    }
    for key, value in expected_values.items():
        match = re.search(
            rf"(?m)^      - key: {re.escape(key)}\n        value: {re.escape(value)}$",
            source,
        )
        assert match, f"{key} must be declared with its exact runtime value"

    assert re.search(
        r"(?m)^      - key: AI_INFINITY_MAINTENANCE_GITHUB_TOKEN\n        sync: false$",
        source,
    ), "the owner token must be requested manually, never generated or committed"
    assert re.search(
        r"(?m)^      - key: AI_INFINITY_MAINTENANCE_ADMIN_TOKEN\n        generateValue: true$",
        source,
    ), "the operator token must be generated independently by Render"

    assert "github_pat_" not in source
    assert "ghp_" not in source
