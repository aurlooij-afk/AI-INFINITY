from __future__ import annotations

import json
from urllib.error import HTTPError
from io import BytesIO

from scripts import verify_gemini_writer as verify


def test_gemini_preflight_blocks_empty_key_without_network(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK", "true")
    result = verify.verify_gemini_writer()
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "GEMINI_API_KEY_missing"


def test_gemini_preflight_blocks_unacknowledged_free_tier_without_network(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK", "the entire instruction text")
    result = verify.verify_gemini_writer()
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "Gemini_Free_Tier_data_use_acknowledgement_missing_or_invalid"


def test_gemini_preflight_validates_actual_json_response_and_never_logs_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK", "true")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_MODEL", verify.MODEL)
    captured = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        response = {
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": '{"status":"ok","provider":"google-gemini"}'}]},
            }],
            "usageMetadata": {"promptTokenCount": 14, "candidatesTokenCount": 8},
        }
        return BytesIO(json.dumps(response).encode("utf-8"))

    monkeypatch.setattr(verify, "urlopen", fake_urlopen)
    result = verify.verify_gemini_writer()

    assert result["status"] == "PASS"
    assert result["model"] == verify.MODEL
    assert result["response_sha256"]
    assert result["usage_metadata"]["candidatesTokenCount"] == 8
    assert captured["timeout"] == 35
    assert captured["url"].endswith(f"/models/{verify.MODEL}:generateContent")
    assert captured["headers"]["X-goog-api-key"] == "test-only-key"
    assert "test-only-key" not in json.dumps(result)
