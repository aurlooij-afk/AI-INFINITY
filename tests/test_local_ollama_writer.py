from __future__ import annotations

import io
import json
import os

import pytest

os.environ.setdefault("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity-local-ollama-tests")
import studio_ultimate as studio


def _blueprint():
    chapters = []
    for chapter_index in range(1, 6):
        narration = " ".join(
            (
                f"Chapter {chapter_index}, segment {sentence_index}, explains a distinct production choice, "
                "the evidence behind it, the practical trade-off, the result to inspect, and the limitation "
                "the creator should keep visible during review."
            )
            for sentence_index in range(1, 36)
        )
        chapters.append({
            "heading": f"Chapter {chapter_index}: production decisions",
            "narration": narration,
            "visual_query": f"distinct documentary image composition for production chapter {chapter_index}",
            "image_prompt": f"premium documentary production frame, chapter {chapter_index}",
            "on_screen": f"Production chapter {chapter_index}",
            "duration": 180,
            "proof_needed": [],
        })
    return {
        "title": "A Practical Guide to Responsible Video Production",
        "hook": "A good production is a chain of deliberate decisions.",
        "premise": "A practical, evidence-conscious guide to planning and producing a video.",
        "audience": "independent creators",
        "tone": "precise, useful, documentary",
        "cta": "Review the evidence and revise the work before delivery.",
        "language": "English",
        "chapters": chapters,
    }


def test_local_ollama_adapter_rejects_non_loopback_endpoints(monkeypatch):
    monkeypatch.setenv("AI_INFINITY_LOCAL_LLM_BASE_URL", "https://example.com:11434")
    monkeypatch.setenv("AI_INFINITY_LOCAL_LLM_MODEL", "qwen2.5:7b")
    assert studio._local_ollama_text_model() is None

    monkeypatch.setenv("AI_INFINITY_LOCAL_LLM_BASE_URL", "http://10.0.0.5:11434")
    assert studio._local_ollama_text_model() is None


def test_local_ollama_adapter_sends_json_chat_and_returns_usage(monkeypatch):
    monkeypatch.setenv("AI_INFINITY_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("AI_INFINITY_LOCAL_LLM_MODEL", "qwen2.5:7b")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return io.BytesIO(json.dumps({
            "message": {"content": "{\"status\":\"ok\"}"},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 100,
            "eval_count": 20,
            "total_duration": 123456,
        }).encode("utf-8"))

    monkeypatch.setattr(studio, "urlopen", fake_urlopen)
    writer = studio._local_ollama_text_model()
    assert callable(writer)

    result = writer({
        "messages": [{"role": "user", "content": "Return a JSON object."}],
        "temperature": 0.2,
    })

    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["body"]["model"] == "qwen2.5:7b"
    assert captured["body"]["stream"] is False
    assert captured["body"]["format"] == "json"
    assert captured["body"]["options"]["num_ctx"] == 32768
    assert captured["body"]["options"]["num_predict"] == 8192
    assert captured["timeout"] >= 60
    assert result["provider"] == "local-ollama:qwen2.5:7b"
    assert json.loads(result["text"]) == {"status": "ok"}
    assert result["usage"]["eval_count"] == 20


def test_creative_planner_uses_loopback_model_and_applies_real_longform_quality_gates(monkeypatch):
    monkeypatch.delenv("AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS", raising=False)
    monkeypatch.setenv("AI_INFINITY_LOCAL_LLM_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("AI_INFINITY_LOCAL_LLM_MODEL", "qwen2.5:7b")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACEHUB_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    calls = []

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data.decode("utf-8")))
        return io.BytesIO(json.dumps({
            "message": {"content": json.dumps(_blueprint(), ensure_ascii=False)},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 6000,
            "eval_count": 5200,
            "total_duration": 987654321,
        }).encode("utf-8"))

    monkeypatch.setattr(studio, "urlopen", fake_urlopen)
    plan, provider = studio.creative_plan(
        {
            "title": "A Practical Guide to Responsible Video Production",
            "objective": "Create an evidence-conscious 15-minute educational programme.",
            "topic": "responsible video production workflows",
            "format": "long",
            "duration": 900,
            "speaking_rate_wpm": 125,
            "language": "English",
            "tone": "precise, cinematic, useful",
        },
        {"sources": []},
        None,
    )

    generation = plan["script_generation"]
    assert len(calls) == 1
    assert provider == "local-ollama:qwen2.5:7b"
    assert generation["status"] == "complete"
    assert generation["required_spoken_words"] == 1875
    assert generation["actual_spoken_words"] >= 1875
    assert generation["unique_sentence_ratio"] >= 0.85
    assert generation["repair_passes"] == 0
