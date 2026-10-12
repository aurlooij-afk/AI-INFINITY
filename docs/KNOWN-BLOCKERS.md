# AI Infinity known blockers

Checked 2026-10-12 against PR #37 head **8f77d4a287f3e139c80d47b84bf14bec232d6e54**. A green workflow must not erase contradictory artifact evidence.

## Current release blockers

1. **Rights-truth false positive in the last acceptance artifact.** [Final Complete Acceptance Suite, run 38224101444](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38224101444) emitted ten passing scenarios. Its 60-second H.264/AAC MP4 SHA-256 8fc266f90a4bfcf054f6cbae3e607f821dbd55d91589a8fd9a863fcd1017983a was labeled VERIFIED even though three visual records lacked source URL/creator and had UNKNOWN_REVIEW_REQUIRED commercial-use status; a Commons record also remained review-required. Cause: label-only rights acceptance plus provenance loss during reuse. The fail-closed source patch and regressions must pass on a new SHA; if the real provider case correctly becomes REVIEW_REQUIRED, fix the missing source/rights evidence rather than lowering the gate.
2. **Live site/revision unverified.** The available probe saw https://ai-infinity.blitz.cloud/ return HTTP 503 on 2026-10-12. Render metadata identifies https://ai-infinity-ca5e.onrender.com (service srv-dallhl740ujc73eetgp0); its latest recorded live deployment is dep-db4v138u01pc73fohdh0 at c59421d2da4b9b32eaccdee2ca71e2b9ccfd07ed from October 10. That predates PR #37. Actual current Render HTTP status and running revision have not been independently verified.
3. **Storage restore incomplete.** The SQLite process-restart scenario explicitly says persistent_storage_claimed=false. No current isolated B2 PUT/HEAD/GET/hash/size canary or clean-instance project/database restore proof was captured.
4. **Editorial/native-language review pending.** The reviewer packet says human_review_status=PENDING. The Pashto scenario used local-espeak-pashto-fast-fallback; native-speaker review is not recorded. No two-reviewer scorecard has passed for this 60-second artifact.
5. **Production profile/editable export unproved.** The accepted artifact is 1280x720. A real requested 1080p profile, the three-case MLT comparison and an OpenTimelineIO package with its external source media have not been proven on the deployed revision.
6. **Live provider quality is distinct from fixtures.** Recovery fixtures prove retry mechanics only. Re-run a real-provider-backed 60-second project after the rights patch and inspect relevance, rights, requested resolution and QC separately.
7. **Preserved research originals are absent from the current branch tree.** Neither AI_Infinity_Creator_Benchmark_and_Reuse_Plan.md nor AI_Infinity_10_Method_Deep_Research_and_Master_Command.md appears among the 93 files in the PR tree. ADR-0001 records SHA-256 values 2f05d1c9af7b670f26ec5fb71c34409b1eeceaee9bbaa2f2df68bfbe3b950baa and 8745f4becadbef6c7091c22538c44ffe0e576a01aee0f718d3a8facf68aa69cd, but their original bytes were not located in this branch. The new benchmark is additive and does not claim to replace the originals.

## Workflow evidence at prior head 8f77d4a287f3e139c80d47b84bf14bec232d6e54

- [Visual Diversity Truth Regression — 38224101489](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38224101489): success.
- [Final Complete Acceptance Suite — 38224101444](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38224101444): success summary, but contradictory rights evidence requires the fix.
- [Creator Studio Build Guard — 38224101430](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38224101430): success.

## Release discipline

- Do not merge while required checks are red/pending or a passing run contradicts source/rights evidence.
- Do not deploy until gates 1–8 pass and the target/authorization are clear.
- Unknown/incompatible rights, missing provenance and fallback visuals block VERIFIED delivery/publishing.
- Do not claim artistic/language approval without real reviewer records.
- Do not claim durability without current read-back hash/size and clean-instance restore.
