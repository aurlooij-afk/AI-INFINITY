"""Regressions for unavailable video-provider discovery."""

import os

os.environ.setdefault("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity-provider-availability-test")
os.environ.setdefault("AI_INFINITY_3601_BACKGROUND", "false")

import production_graph
import production_openai_video


def test_discontinued_sora_adapter_is_never_advertised_as_configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-is-not-a-working-sora-api")
    state = production_openai_video.status()

    assert state["configured"] is False
    assert state["enabled"] is False
    assert state["status"] == "deprecated"
    assert "discontinued" in state["disabled_reason"].lower()


def test_legacy_sora_submit_fails_before_network_access(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    import pytest

    with pytest.raises(RuntimeError, match="Sora API discontinued"):
        production_openai_video.create_remote({"prompt": "test shot", "seconds": 8})


def test_production_graph_does_not_report_sora_as_a_available_video_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    providers = production_graph.providers()["providers"]
    sora = next(item for item in providers if item["id"] == "openai_video")

    assert sora["video"] is False
    assert sora["configured"] is False
    assert sora["enabled"] is False
    assert sora["status"] == "deprecated"


def test_runway_paid_metered_generation_requires_explicit_owner_ack(monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-key-must-not-trigger-a-bill")
    monkeypatch.delenv("AI_INFINITY_RUNWAY_PAID_USAGE_ACK", raising=False)

    def forbidden_request(*args, **kwargs):
        raise AssertionError("paid provider network request must not be sent without explicit acknowledgement")

    monkeypatch.setattr(production_graph.requests, "post", forbidden_request)

    import pytest
    with pytest.raises(RuntimeError, match="paid metered provider"):
        production_graph.runway_create({"prompt": "test", "duration": 5})

    state = production_graph.providers()["providers"]
    runway = next(item for item in state if item["id"] == "runway")
    assert runway["configured"] is True
    assert runway["enabled"] is False
    assert runway["status"] == "paid_usage_not_authorized"
