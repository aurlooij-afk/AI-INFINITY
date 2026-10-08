from __future__ import annotations

import os

import pytest

import studio_ultimate as studio


def test_runtime_state_contract_is_explicit():
    expected = {
        "queued": "WAITING",
        "running": "RUNNING",
        "producing": "RUNNING",
        "completed": "COMPLETED",
        "completed_with_qc_warnings": "DEGRADED",
        "failed": "FAILED",
        "cancelled": "FAILED",
        "degraded": "DEGRADED",
        "blocked": "BLOCKED",
        "requires_connection": "REQUIRES_CONNECTION",
        "requires_compute": "REQUIRES_COMPUTE",
        "license_limited": "LICENSE_LIMITED",
        "rate_limited": "RATE_LIMITED",
    }
    for raw, state in expected.items():
        assert studio._runtime_state_for_status(raw) == state


def test_external_provider_disable_blocks_remote_research(monkeypatch):
    monkeypatch.setenv("AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS", "1")
    result = studio.research_topic("AI Infinity", limit=3)
    assert result["status"] == "DEGRADED"
    assert result["source_count"] == 0
    assert result["truthful"] is True
    assert all(
        provider.get("status") == "disabled_by_policy"
        for provider in result["providers"].values()
    )


def test_external_provider_disable_forces_local_visual_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS", "1")
    monkeypatch.setenv("AI_INFINITY_REQUIRE_SOURCE_VISUALS", "0")
    asset = studio.acquire_scene_asset(
        {
            "heading": "Local fallback",
            "visual_query": "professional technology workspace",
            "image_prompt": "professional cinematic technology workspace",
        },
        tmp_path,
        1,
        prefer_motion=True,
        duration=5,
    )
    path = tmp_path / str(asset["path"]).split("/")[-1]
    assert path.is_file()
    assert path.stat().st_size > 20_000
    assert asset.get("fallback") is True


def test_registry_fallback_ids_are_structurally_valid():
    from professional_creator_fabric import REGISTRY

    ids = {entry["id"] for entry in REGISTRY["entries"]}
    assert len(ids) == 607
    for entry in REGISTRY["entries"]:
        assert isinstance(entry["fallback_ids"], list)
        assert all(fallback in ids and fallback != entry["id"] for fallback in entry["fallback_ids"])
