from __future__ import annotations

from ai_infinity.deployment_identity import render_runtime_identity


def test_render_runtime_identity_uses_platform_commit_and_branch():
    commit = "a" * 40
    identity = render_runtime_identity({
        "RENDER": "true",
        "RENDER_GIT_COMMIT": commit,
        "RENDER_GIT_BRANCH": "main",
    })

    assert identity == {
        "platform": "render",
        "source": "render_runtime_environment",
        "commit_sha": commit,
        "branch": "main",
    }


def test_render_runtime_identity_does_not_trust_git_sha_outside_render():
    identity = render_runtime_identity({
        "RENDER": "false",
        "RENDER_GIT_COMMIT": "b" * 40,
        "RENDER_GIT_BRANCH": "untrusted",
    })

    assert identity == {
        "platform": None,
        "source": None,
        "commit_sha": None,
        "branch": None,
    }


def test_render_runtime_identity_rejects_malformed_commit():
    identity = render_runtime_identity({
        "RENDER": "true",
        "RENDER_GIT_COMMIT": "not-a-full-git-sha",
        "RENDER_GIT_BRANCH": "main",
    })

    assert identity["platform"] == "render"
    assert identity["source"] is None
    assert identity["commit_sha"] is None
