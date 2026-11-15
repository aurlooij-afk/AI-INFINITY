#!/usr/bin/env python3
"""Run the production long-form planner against genuine local model inference."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    os.environ.setdefault("AI_INFINITY_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434")
    os.environ.setdefault("AI_INFINITY_LOCAL_LLM_MODEL", "qwen2.5:7b")
    import studio_ultimate as studio

    request = {
        "title": "A Practical Guide to Responsible Video Production",
        "objective": (
            "Create a useful 15-minute educational programme explaining how an independent creator "
            "can research, plan, script, shoot, edit, inspect, revise and responsibly deliver a video."
        ),
        "topic": "responsible video production workflows",
        "format": "long",
        "duration": 900,
        "speaking_rate_wpm": 125,
        "language": "English",
        "tone": "precise, cinematic, practical, evidence-conscious",
        "audience": "independent creators and small production teams",
        "visual_style": "premium documentary editorial",
    }
    prompt_hash = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    plan, provider = studio.creative_plan(request, {"sources": []}, None)
    generation = plan.get("script_generation") if isinstance(plan, dict) else {}
    generation = generation if isinstance(generation, dict) else {}
    words = int(generation.get("actual_spoken_words") or 0)
    required = int(generation.get("required_spoken_words") or 0)
    distinctness = float(generation.get("unique_sentence_ratio") or 0.0)
    expected_provider = "local-ollama:"
    if (
        not str(provider).startswith(expected_provider)
        or generation.get("status") != "complete"
        or required != 1875
        or words < required
        or distinctness < 0.85
    ):
        raise SystemExit(
            "LOCAL_MODEL_LONGFORM=FAIL "
            + json.dumps({
                "provider": str(provider),
                "script_status": generation.get("status"),
                "required_words": required,
                "actual_words": words,
                "distinct_sentence_ratio": distinctness,
                "repair_passes": generation.get("repair_passes"),
            }, sort_keys=True)
        )

    result = {
        "schema": "ai-infinity.local-model-longform.v1",
        "status": "PASS",
        "provider": str(provider),
        "model": os.getenv("AI_INFINITY_LOCAL_LLM_MODEL", "qwen2.5:7b"),
        "duration_seconds": 900,
        "speaking_rate_wpm": 125,
        "required_spoken_words": required,
        "actual_spoken_words": words,
        "unique_sentence_ratio": distinctness,
        "repair_passes": int(generation.get("repair_passes") or 0),
        "script_generation_status": generation.get("status"),
        "request_sha256": prompt_hash,
        "script_text_saved_to_evidence": False,
        "truthful": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("LOCAL_MODEL_LONGFORM=PASS")
    print("LONGFORM_WORDS=" + str(words))
    print("LONGFORM_DISTINCTNESS=" + str(round(distinctness, 4)))
    print("LONGFORM_REPAIR_PASSES=" + str(result["repair_passes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
