from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from scripts.dispatch_repeat_acceptance import dispatch_and_wait


class _Response:
    def __init__(self, value):
        self.payload = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


def test_dispatches_and_waits_for_a_distinct_exact_sha_acceptance_run():
    candidate = "a" * 40
    responses = [
        {"workflow_run_id": 24680},
        {
            "id": 24680,
            "head_sha": candidate,
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
        },
    ]
    calls = []

    def opener(request, timeout=30):
        calls.append(request)
        assert timeout == 30
        return _Response(responses.pop(0))

    result = dispatch_and_wait(
        repository="owner/repository",
        workflow="final-acceptance-suite.yml",
        ref="ai-infinity/master-v2",
        candidate_sha=candidate,
        token="test-token",
        opener=opener,
        sleeper=lambda _seconds: None,
    )

    assert result["workflow_run_id"] == 24680
    assert result["head_sha"] == candidate
    assert result["status"] == "completed"
    assert result["conclusion"] == "success"
    assert calls[0].get_method() == "POST"
    payload = json.loads(calls[0].data.decode("utf-8"))
    assert payload == {
        "ref": "ai-infinity/master-v2",
        "inputs": {"acceptance_role": "repeat"},
    }
    assert calls[1].get_method() == "GET"
    assert "Bearer test-token" in calls[0].get_header("Authorization")


def test_repeat_acceptance_rejects_a_different_candidate_sha():
    responses = [
        {"workflow_run_id": 24681},
        {
            "id": 24681,
            "head_sha": "b" * 40,
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
        },
    ]

    def opener(_request, timeout=30):
        return _Response(responses.pop(0))

    with pytest.raises(RuntimeError, match="different commit"):
        dispatch_and_wait(
            repository="owner/repository",
            workflow="final-acceptance-suite.yml",
            ref="ai-infinity/master-v2",
            candidate_sha="a" * 40,
            token="test-token",
            opener=opener,
            sleeper=lambda _seconds: None,
        )


def test_repeat_acceptance_rejects_a_failed_second_run():
    responses = [
        {"workflow_run_id": 24682},
        {
            "id": 24682,
            "head_sha": "a" * 40,
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "failure",
        },
    ]

    def opener(_request, timeout=30):
        return _Response(responses.pop(0))

    with pytest.raises(RuntimeError, match="did not complete successfully"):
        dispatch_and_wait(
            repository="owner/repository",
            workflow="final-acceptance-suite.yml",
            ref="ai-infinity/master-v2",
            candidate_sha="a" * 40,
            token="test-token",
            opener=opener,
            sleeper=lambda _seconds: None,
        )
