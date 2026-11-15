#!/usr/bin/env python3
"""Verify genuine local Ollama inference without an external model API."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    endpoint = os.getenv("AI_INFINITY_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434").strip()
    model = os.getenv("AI_INFINITY_LOCAL_LLM_MODEL", "qwen2.5:7b").strip()
    try:
        parsed = urlparse(endpoint)
        valid = (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.port == 11434
            and parsed.path in {"", "/"}
        )
    except ValueError:
        valid = False
    if not valid or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}", model):
        raise SystemExit("LOCAL_MODEL_PREFLIGHT=FAIL invalid local endpoint or model identifier")

    prompt = (
        "Return one JSON object with exactly these fields: "
        '{"status":"ok","runtime":"ollama-local","capability":"json-generation"}. '
        "Do not add prose or markdown."
    )
    request = Request(
        endpoint.rstrip("/") + "/api/chat",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 128},
        }).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=240) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise SystemExit(f"LOCAL_MODEL_PREFLIGHT=FAIL {type(exc).__name__}") from None

    message = payload.get("message") if isinstance(payload, dict) else {}
    response_text = str(message.get("content") or "").strip() if isinstance(message, dict) else ""
    try:
        response_json = json.loads(response_text)
    except (TypeError, ValueError):
        response_json = {}
    valid = (
        payload.get("done") is True
        and response_json.get("status") == "ok"
        and response_json.get("runtime") == "ollama-local"
        and response_json.get("capability") == "json-generation"
        and int(payload.get("eval_count") or 0) > 0
    )
    if not valid:
        raise SystemExit("LOCAL_MODEL_PREFLIGHT=FAIL model response did not meet the inference contract")

    result = {
        "schema": "ai-infinity.local-model-inference.v1",
        "status": "PASS",
        "provider": "ollama-local-cpu",
        "model": model,
        "endpoint_host": parsed.hostname,
        "response_sha256": hashlib.sha256(response_text.encode("utf-8")).hexdigest(),
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "eval_count": payload.get("eval_count"),
        "total_duration_ns": payload.get("total_duration"),
        "model_response_stored": False,
        "external_model_api_called": False,
        "truthful": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("LOCAL_MODEL_INFERENCE=PASS")
    print("LOCAL_MODEL=" + model)
    print("LOCAL_MODEL_EXTERNAL_API=NOT_USED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
