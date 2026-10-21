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


## Successful full acceptance: SHA 0e50a1a36208e542be030da614184acecbd4bbbb

- Full ten-scenario acceptance: [workflow run 38181419443](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38181419443) — **passed** on exact SHA 0e50a1a36208e542be030da614184acecbd4bbbb.
- Focused visual/recovery regression: [38181419420](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38181419420) — passed, 25 tests.
- Python/runtime build guard: [38181419423](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38181419423) — passed; this included 4 new mountinfo persistence-truth tests, existing project-state and artifact storage tests, JavaScript parsing, unified app startup smoke and canonical runtime smoke.
- Docker image guard: passed in run 38181419423.
- Test 1 created project \`studio_e929f030f34c4990bc852e86831fefd4\`, a 60-second English launch video with real provider visuals. Independent truth returned \`VERIFIED\`, and the production-manifest SHA matched the downloaded final.mp4. Runtime-probed duration: 60.075229 seconds. Its exact resolution and downloaded bytes were not retained by this baseline run, so do not infer them from the passing status.
- Test 2 created project \`studio_4487fba36b764fa782c8b9ea7ec928cd\`; Pashto text/captions used Pashto script, provider evidence reported \`edge-tts-neural:ps-AF-LatifaNeural\`, and the downloaded narration was 47,088 bytes. This is technical/provider evidence only, not native-speaker editorial approval.
- Test 3: uploaded PDF material was parsed and used; project \`studio_7167e29b83324d9d8ec47ce51b157327\`.
- Test 4: a reference to \`https://www.python.org/\` appeared in research/source evidence; project \`studio_6d9f3f96dc714da089e5ec60310c59de\`.
- Test 5: three non-identical repurposed variants were generated.
- Test 6: after forced primary AI-visual failure, the adapter recovered through the NASA Image and Video Library.
- Test 7: external providers were disabled; the local media path completed while research was truthfully recorded as degraded; project \`studio_dc736f695a884a65b667665406ef24a3\`.
- Test 8: process restart retained the job on the same CI filesystem, but persistence was correctly reported false despite confirmation flags; project \`studio_10251c9fc071472d8be2f0435ce1ab89\`. This is not a clean-instance or remote snapshot restore proof.
- Test 9: two concurrent projects stayed isolated; project IDs \`studio_f6abb648c0d840eba67a73257bac3245\` and \`studio_a5f696657a914aac8419400f6d8b1bde\`.
- Test 10: a deliberately corrupted intermediate was recovered and decoded, retry/scene-completed audit evidence existed, and another project's scene SHA remained unchanged. The deterministic CC0 test illustration correctly kept truth at \`REVIEW_REQUIRED\` with five fallback-marked visual records; publish returned \`blocked_by_editorial_review\` and \`publish_ready=false\`. This is the expected fail-closed result, not a professional visual pass.

This successful run proves the ten automated acceptance scenarios under their stated policy. It does **not** establish live deployment health, current B2 round-trip/clean-host restore, native-speaker approval, human editorial scores, 1080p professional-mode output, OpenTimelineIO export or comparative MLT performance. Those remain separate release gates.


## Latest failure investigation: SHA c6491929732baf8d2d5f74420a8419dc7e6b7390

- Full acceptance run: https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38182253435 — failed before scenario 1 completed.
- First failing assertion: none. The runner raised an uncaught filesystem exception in `create_review_packet` at `tests/final_acceptance_suite.py:124` while saving `/evidence/studio_2ad9e15d3ad542b89679b16723e06dd9-contact-sheet.png`.
- Exact exception: `PermissionError: [Errno 13] Permission denied`. The acceptance image runs as non-root UID 10001 while GitHub Actions mounted a host-created evidence directory that was not writable by that UID.
- Intended fix: make only the disposable `acceptance-evidence` directory writable in the CI workflow before the Docker run. No production filesystem permissions, creative-quality gate, rights gate or publish behavior is changed.
- Rollback: revert the workflow permission line if needed; do not skip contact-sheet generation or call the failed run a pass.
