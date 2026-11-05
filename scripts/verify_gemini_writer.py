#!/usr/bin/env python3
"""Verify the explicitly acknowledged Gemini Free Tier API key in this runtime."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
MODEL="gemini-3.1-flash-lite"
ALLOWED_ACK={"1","true","yes","on"}

def _first_nonempty_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def verify_gemini_writer() -> dict[str, Any]:
    api_key = _first_nonempty_env(("GEMINI_API_KEY", "AI_INFINITY_GEMINI_API_KEY", "GOOGLE_AI_STUDIO_API_KEY"))
    ack = _first_nonempty_env((
        "AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK",
        "GEMINI_FREE_TIER_DATA_USE_ACK",
        "GOOGLE_AI_STUDIO_FREE_TIER_ACK",
    )).lower()
    model=os.getenv("AI_INFINITY_GEMINI_FREE_TIER_MODEL",MODEL).strip()
    if not api_key: return {"status":"BLOCKED","reason":"GEMINI_API_KEY_missing","truthful":True}
    if ack not in ALLOWED_ACK: return {"status":"BLOCKED","reason":"Gemini_Free_Tier_data_use_acknowledgement_missing_or_invalid","truthful":True}
    if model!=MODEL: return {"status":"BLOCKED","reason":"unapproved_Gemini_model","model":model,"truthful":True}
    prompt='Return exactly this JSON object and no other text: {"status":"ok","provider":"google-gemini"}'
    body={"contents":[{"role":"user","parts":[{"text":prompt}]}],"generationConfig":{"temperature":0,"maxOutputTokens":128,"responseMimeType":"application/json"}}
    request=Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
      data=json.dumps(body).encode("utf-8"),
      headers={"Content-Type":"application/json","Accept":"application/json","x-goog-api-key":api_key,"User-Agent":"AI-Infinity-Gemini-Preflight/1.0"},method="POST")
    try:
      with urlopen(request,timeout=35) as response:
        payload=json.loads(response.read().decode("utf-8")); http_status=int(getattr(response,"status",200))
    except HTTPError as exc:
      return {"status":"FAIL","reason":"Gemini_HTTP_error","http_status":int(exc.code),"truthful":True}
    except (URLError,TimeoutError,OSError,ValueError) as exc:
      return {"status":"FAIL","reason":type(exc).__name__,"truthful":True}
    candidates=payload.get("candidates") if isinstance(payload,dict) else None
    candidate=candidates[0] if isinstance(candidates,list) and candidates else {}
    finish_reason=str(candidate.get("finishReason") or "")
    content=candidate.get("content") if isinstance(candidate.get("content"),dict) else {}
    parts=content.get("parts") if isinstance(content.get("parts"),list) else []
    response_text="\n".join(str(part.get("text") or "").strip() for part in parts if isinstance(part,dict) and part.get("text")).strip()
    try: response_json=json.loads(response_text)
    except (TypeError,ValueError): response_json={}
    valid=http_status==200 and finish_reason=="STOP" and response_json.get("status")=="ok" and response_json.get("provider")=="google-gemini"
    return {"status":"PASS" if valid else "FAIL","model":model,"http_status":http_status,"finish_reason":finish_reason or None,
      "response_characters":len(response_text),"response_sha256":hashlib.sha256(response_text.encode("utf-8")).hexdigest() if response_text else None,
      "usage_metadata":payload.get("usageMetadata") if isinstance(payload,dict) else None,"truthful":True,
      **({} if valid else {"reason":"Gemini_response_did_not_match_smoke_contract"})}

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path,required=True); args=parser.parse_args()
    result=verify_gemini_writer(); args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("GEMINI_WRITER_VERIFICATION="+json.dumps(result,sort_keys=True))
    return 0 if result.get("status")=="PASS" else (3 if result.get("status")=="BLOCKED" else 1)
if __name__=="__main__": raise SystemExit(main())
