#!/usr/bin/env python3
from __future__ import annotations

"""Verify the real public Render runtime is serving the exact candidate commit.

The runtime commit comes from Render's RENDER_GIT_COMMIT environment variable,
exposed by the application's health payload. A reachable old deployment is
BLOCKED, never accepted as the candidate.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


HEALTH_PATH = "/infinity/studio/health"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def assess_deployment_response(candidate_sha: str, http_status: int, payload: Any) -> dict[str, Any]:
    candidate = str(candidate_sha or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        return {"status": "FAIL", "reason": "candidate_sha_invalid", "truthful": True}
    if int(http_status) != 200:
        return {
            "status": "BLOCKED",
            "reason": "production_health_endpoint_not_ready",
            "http_status": int(http_status),
            "truthful": True,
        }
    if not isinstance(payload, dict):
        return {"status": "FAIL", "reason": "health_response_not_json_object", "truthful": True}

    deployment = payload.get("deployment") if isinstance(payload.get("deployment"), dict) else {}
    deployed_sha = str(deployment.get("commit_sha") or "").strip().lower()
    trusted_render_identity = (
        deployment.get("platform") == "render"
        and deployment.get("source") == "render_runtime_environment"
        and re.fullmatch(r"[0-9a-f]{40}", deployed_sha) is not None
    )
    base = {
        "candidate_commit_sha": candidate,
        "deployed_commit_sha": deployed_sha or None,
        "deployment_branch": deployment.get("branch"),
        "health_status": payload.get("status"),
        "content_creation_ready": payload.get("content_creation_ready") is True,
    }
    if not trusted_render_identity:
        return {
            **base,
            "status": "BLOCKED",
            "reason": "runtime_does_not_expose_trusted_render_commit_identity",
            "truthful": True,
        }
    if deployed_sha != candidate:
        return {
            **base,
            "status": "BLOCKED",
            "reason": "exact_candidate_is_not_the_live_production_revision",
            "truthful": True,
        }
    if payload.get("truthful") is not True:
        return {**base, "status": "FAIL", "reason": "health_payload_truth_contract_missing", "truthful": True}
    if payload.get("status") != "healthy" or payload.get("content_creation_ready") is not True:
        return {**base, "status": "FAIL", "reason": "exact_candidate_is_live_but_runtime_is_not_healthy", "truthful": True}
    return {**base, "status": "PASS", "reason": None, "truthful": True}


def _emit(result: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("LIVE_DEPLOYMENT_VERIFICATION=" + json.dumps(result, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--base-url", default=os.getenv("AI_INFINITY_PRODUCTION_URL", "https://ai-infinity-ca5e.onrender.com"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidate = str(args.candidate_sha).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        parser.error("--candidate-sha must be a full 40-character Git commit SHA")

    parsed = urlsplit(str(args.base_url).strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        result = {
            "status": "FAIL",
            "reason": "production_url_must_be_an_https_origin_without_credentials_or_path",
            "candidate_commit_sha": candidate,
            "truthful": True,
        }
        _emit(result, args.output)
        return 1

    endpoint = f"https://{parsed.netloc}{HEALTH_PATH}"
    raw = b""
    http_status = 0
    network_error = None
    try:
        opener = build_opener(_NoRedirect())
        request = Request(endpoint, headers={"Accept": "application/json", "User-Agent": "AI-Infinity-Live-Deployment-Verifier/1.0"}, method="GET")
        with opener.open(request, timeout=20) as response:
            http_status = int(getattr(response, "status", 200))
            raw = response.read(262145)
            if len(raw) > 262144:
                raise ValueError("health response exceeded the 256 KiB verification limit")
    except HTTPError as exc:
        http_status = int(exc.code)
        try:
            raw = exc.read(262145)
        except Exception:
            raw = b""
        network_error = "redirect_rejected" if 300 <= http_status < 400 else "http_error"
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        network_error = type(exc).__name__

    payload = None
    if http_status == 200 and raw:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None

    result = assess_deployment_response(candidate, http_status, payload)
    result.update({
        "schema": "ai-infinity-live-deployment-verification.v1",
        "endpoint": endpoint,
        "http_status": http_status or None,
        "response_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
        "network_error": network_error,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    _emit(result, args.output)
    if result.get("status") == "PASS":
        return 0
    if result.get("status") == "BLOCKED":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
