from __future__ import annotations

import json
from pathlib import Path

from scripts.verify_live_deployment import verify


def test_live_deployment_verifier_blocks_missing_live_url_without_network(tmp_path: Path):
    output = tmp_path / "live-deployment-verification.json"

    result = verify("", "a" * 40, output, wait_seconds=30)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "live_base_url_must_be_https"
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["status"] == "BLOCKED"
    assert saved["truthful"] is True


def test_live_deployment_verifier_rejects_non_https_url_without_network(tmp_path: Path):
    output = tmp_path / "live-deployment-verification.json"

    result = verify("http://ai-infinity.example", "a" * 40, output, wait_seconds=30)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "live_base_url_must_be_https"
    assert output.is_file()


def test_live_deployment_verifier_rejects_invalid_commit_sha_without_network(tmp_path: Path):
    output = tmp_path / "live-deployment-verification.json"

    result = verify("https://ai-infinity.example", "not-a-git-sha", output, wait_seconds=30)

    assert result["status"] == "FAIL"
    assert result["reason"] == "expected_commit_sha_invalid"
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "FAIL"
