# PR #37 full-acceptance baseline evidence

This is baseline evidence, not a claim about the post-fix commit.

- Repository: aurlooij-afk/AI-INFINITY
- PR: https://github.com/aurlooij-afk/AI-INFINITY/pull/37
- Baseline source SHA: 1384e6b265e46902865e32418541cc7348233023
- Full acceptance: https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38171668813 — failed
- Focused regression: https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38171668830 — passed
- Docker/Python guard: https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38171668819 — passed

The failure was scenario 10 (tests/final_acceptance_suite.py). The project retry changed the corrupted intermediate's bytes and recovered a valid scene file. The final file's media checks passed: around 20.073 seconds, H.264 video plus audio, requested 16:9 ratio, black/freeze scan passed, caption timing passed, source visual diversity and minimum scene coverage passed. One qualified illustrative fallback remained, so truth correctly reported fallback_visual_count=1, source_visual_policy_passed=false, verified=false, state=REVIEW_REQUIRED, illustrative_fallback_delivery_eligible=true and no other truth failures. The test incorrectly asserted verified must always be true.

The source also showed a persistence test that used environment confirmation flags and a /data-like location inside CI without proving a real persistent mount or remote read-back. That scenario is being changed to distinguish same-filesystem process restart from actual durability.

## Required post-fix evidence

After the changes, record the exact tested SHA and workflow URLs here. All ten scenarios must run in the same complete acceptance run. Scenario 1 must independently require non-fallback visuals from the real provider ladder; scenario 10 must verify retry/recovery/isolation and conditionally expect REVIEW_REQUIRED when fallback remains. Scenario 8 must prove confirmation flags alone do not claim persistence.

No post-fix pass is implied by this baseline record.


## Post-fix execution record: commit 17f7debb6109e08d07dfa54d18dd1a778978849f

- Focused visual/recovery regressions: passed, 25 tests in 0.74s.
- Python syntax/runtime guard: passed, including module compile, existing durable-state and storage tests, app/startup smoke and canonical wrapper smoke.
- Docker image guard: passed.
- Full acceptance run: https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38180781168 — failed at the last assertion in scenario 10, after scenarios 1–9 had completed and the recovery scenario's preceding checks passed.
- Scenario 10 truth remained correct: the deterministic CC0 illustration resulted in REVIEW_REQUIRED rather than VERIFIED. The recovered scene's bytes changed from the corrupted intermediate, FFmpeg decode passed, retry/scene-completed audit checks and cross-project isolation passed, and publication was not sent.
- The sole failing assertion expected only blocked_by_quality_gate. The actual response was blocked_by_editorial_review with publish_ready=false, because the later editorial-review guard blocks the same publish request before an upload. This is a test assumption defect, not a production gate failure.
- Next action: the test now accepts only one of the two explicit block statuses and asserts there is no external URL/publish result. No professional truth or rights gate was weakened. The current commit's full acceptance is still red; rerun on the next exact SHA before considering merge.
