from __future__ import annotations

import io
import json

import studio_ultimate as studio


def test_gemini_writer_requires_key_and_explicit_free_tier_acknowledgement(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK", "true")
    assert studio._gemini_free_tier_text_model() is None

    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK", "false")
    assert studio._gemini_free_tier_text_model() is None


def test_gemini_writer_uses_json_generate_content_and_extended_output_budget(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK", "true")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_MODEL", "gemini-3.1-flash-lite")
    captured = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        payload = {
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": '{"chapters":[],"status":"ok"}'}]},
            }],
            "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 5},
        }
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(studio, "urlopen", fake_urlopen)
    writer = studio._gemini_free_tier_text_model()
    assert callable(writer)
    result = writer({"messages": [{"role": "user", "content": "return json"}]})

    assert captured["url"].endswith("/models/gemini-3.1-flash-lite:generateContent")
    assert captured["timeout"] == 120
    assert captured["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert captured["body"]["generationConfig"]["maxOutputTokens"] == 16384
    assert result["provider"] == "google-gemini-free-tier:gemini-3.1-flash-lite"
    assert result["finish_reason"] == "STOP"
    assert result["usage"]["candidatesTokenCount"] == 5
