from __future__ import annotations
import json
from io import BytesIO
from scripts import verify_gemini_writer as verify

def test_preflight_blocks_missing_key_and_unaccepted_terms(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY",raising=False)
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK","true")
    assert verify.verify_gemini_writer()["reason"]=="GEMINI_API_KEY_missing"
    monkeypatch.setenv("GEMINI_API_KEY","test-only-key")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK","explanatory text")
    result=verify.verify_gemini_writer()
    assert result["status"]=="BLOCKED"
    assert result["reason"]=="Gemini_Free_Tier_data_use_acknowledgement_missing_or_invalid"

def test_preflight_validates_response_without_leaking_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY","test-only-key")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK","true")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_MODEL",verify.MODEL)
    captured={}
    def fake_urlopen(request,timeout=0):
        captured["url"]=request.full_url; captured["headers"]=dict(request.header_items()); captured["timeout"]=timeout
        return BytesIO(json.dumps({"candidates":[{"finishReason":"STOP","content":{"parts":[{"text":'{"status":"ok","provider":"google-gemini"}'}]}}],
          "usageMetadata":{"promptTokenCount":14,"candidatesTokenCount":8}}).encode("utf-8"))
    monkeypatch.setattr(verify,"urlopen",fake_urlopen)
    result=verify.verify_gemini_writer()
    assert result["status"]=="PASS" and result["response_sha256"]
    assert result["usage_metadata"]["candidatesTokenCount"]==8 and captured["timeout"]==35
    assert captured["url"].endswith(f"/models/{verify.MODEL}:generateContent")
    assert captured["headers"]["X-goog-api-key"]=="test-only-key"
    assert "test-only-key" not in json.dumps(result)


def test_preflight_accepts_supported_key_and_ack_aliases(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK", raising=False)
    monkeypatch.setenv("AI_INFINITY_GEMINI_API_KEY", "alias-test-key")
    monkeypatch.setenv("GEMINI_FREE_TIER_DATA_USE_ACK", "true")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_MODEL", verify.MODEL)
    def fake_urlopen(request, timeout=0):
        assert request.get_header("X-goog-api-key") == "alias-test-key"
        assert timeout == 35
        return BytesIO(json.dumps({
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": '{"status":"ok","provider":"google-gemini"}'}]},
            }]
        }).encode("utf-8"))
    monkeypatch.setattr(verify, "urlopen", fake_urlopen)
    result = verify.verify_gemini_writer()
    assert result["status"] == "PASS"
    assert "alias-test-key" not in json.dumps(result)

def test_preflight_accepts_google_api_key_alias(monkeypatch):
    for name in ("GEMINI_API_KEY", "AI_INFINITY_GEMINI_API_KEY", "GOOGLE_AI_STUDIO_API_KEY", "GEMINI_KEY", "AI_STUDIO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-alias-test-key")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK", "true")
    monkeypatch.setenv("AI_INFINITY_GEMINI_FREE_TIER_MODEL", verify.MODEL)
    def fake_urlopen(request, timeout=0):
        assert request.get_header("X-goog-api-key") == "google-alias-test-key"
        return BytesIO(json.dumps({
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": '{"status":"ok","provider":"google-gemini"}'}]},
            }]
        }).encode("utf-8"))
    monkeypatch.setattr(verify, "urlopen", fake_urlopen)
    result = verify.verify_gemini_writer()
    assert result["status"] == "PASS"
    assert "google-alias-test-key" not in json.dumps(result)

