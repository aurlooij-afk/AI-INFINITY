from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_storage_routes_are_registered_on_the_serving_asgi_app():
    # Render starts this exact object via "uvicorn main:app". Checking an
    # isolated FastAPI instance would miss the original production regression.
    import main

    paths = {str(getattr(route, "path", "")) for route in main.app.router.routes}
    assert "/infinity/storage/v1/status" in paths
    assert "/infinity/storage/v1/verify/{asset_id}" in paths
    assert main.app.state.ai_infinity_storage_routes_registered is True

    schema = main.app.openapi()
    assert "/infinity/storage/v1/status" in schema["paths"]
    assert "/infinity/storage/v1/verify/{asset_id}" in schema["paths"]

    from fastapi.testclient import TestClient

    response = TestClient(main.app).get("/infinity/storage/v1/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("truthful") is True
    assert isinstance(body.get("providers"), list)
    assert isinstance(body.get("configured_providers"), list)


def test_live_render_heredocs_compile_and_import_sys_before_sys_exit():
    workflow = (ROOT / ".github" / "workflows" / "live-render-smoke.yml").read_text(encoding="utf-8")
    blocks = re.findall(
        r"^[ \t]+python - <<'PY'\r?\n(.*?)^[ \t]+PY[ \t]*$",
        workflow,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert len(blocks) == 2, f"expected the two distinct Python heredocs, found {len(blocks)}"

    trees = []
    for index, block in enumerate(blocks, start=1):
        source = textwrap.dedent(block)
        # Compilation catches syntax/indentation regressions without contacting
        # Render or creating any project during unit tests.
        compile(source, f"live-render-smoke-heredoc-{index}", "exec")
        trees.append(ast.parse(source))

    second = trees[1]
    imports_sys = any(
        isinstance(node, ast.Import) and any(alias.name == "sys" for alias in node.names)
        for node in ast.walk(second)
    ) or any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "sys" for alias in node.names)
        for node in ast.walk(second)
    )
    assert imports_sys, "the media runner calls sys.exit but does not import sys"
    assert any(
        isinstance(node, ast.Attribute)
        and node.attr == "exit"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        for node in ast.walk(second)
    ), "the regression guard must exercise the actual sys.exit call path"

    assert "sudo apt-get install -y --no-install-recommends ffmpeg" in workflow
    assert "command -v ffmpeg" in workflow
    assert "command -v ffprobe" in workflow
    assert "timeout=180" in blocks[1]
