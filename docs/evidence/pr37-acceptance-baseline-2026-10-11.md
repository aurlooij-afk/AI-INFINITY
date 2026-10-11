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
