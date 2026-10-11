# ADR-0001: Keep production truth independent from recovery-test success

- Status: Accepted for implementation and verification in PR #37
- Date: 2026-10-12
- Scope: recovery acceptance, source-visual provenance, publishing gate and storage-durability claims

## Context

Baseline commit 1384e6b265e46902865e32418541cc7348233023 passed the focused visual regression and Docker/Python guards, but the full ten-scenario acceptance run failed in scenario 10. The retry replaced a corrupted intermediate with decodable media. Independent truth then correctly returned verified=false, state=REVIEW_REQUIRED and fallback_visual_count=1 because the edit included an original illustrative fallback. The test incorrectly hard-coded verified=true.

A second trust defect existed in scenario 8: it set persistence confirmation environment variables and selected a non-/tmp path but did not prove that the path was a persistent mount or that a remote snapshot could be restored.

## Decisions

1. The authoritative production truth function and its professional/rights gates remain unchanged. Illustrative fallback never counts as professional footage and remains REVIEW_REQUIRED; publishing stays blocked.
2. Recovery scenario 10 uses an explicit, deterministic CC0 test illustration. It is visibly labelled as illustration and metadata records fallback provenance and CC0 1.0 licensing. It is enabled only in CI when the exact allow-listed fixture ID is supplied.
3. Scenario 1 is separate from the fixture path. It requires relevant non-fallback provider visuals, matching 16:9 output, professional scene coverage, and verified truth. Passing the fixture recovery test cannot stand in for live-provider quality.
4. Retry records a retry_requested audit event. Scenario 10 verifies corruption changed, recovered bytes differ from corrupted bytes, media decodes, scene_completed exists after the retry, the other project's scene bytes/status remain unchanged, and publish is blocked when fallback remains.
5. Environment confirmation flags alone cannot establish persistence. A local volume needs evidence from an actual non-ephemeral mount. A SQLite file surviving a process restart inside the same container is only process-restart evidence, not clean-host or remote restore proof.
6. Keep current FFmpeg engine and existing modules. No broad rewrite, merge or deployment is authorised by this ADR.

## Alternatives rejected

- Changing production truth to treat an illustration as professional footage.
- Keeping an unconditional verified=true expectation after a fallback.
- Claiming durable state from a path name or environment flags alone.
- Claiming external provider quality from deterministic fixtures.

## Rollback

Revert the code commit for this ADR if necessary, but do not revert to the unconditional verified=true assertion or the environment-flags-only durability claim. Preserve this ADR, the baseline evidence and the known-blockers file.

## Preserved research artifacts

The earlier research reports were left unmodified and remain separate artifacts:
- AI_Infinity_Creator_Benchmark_and_Reuse_Plan.md — SHA-256 2f05d1c9af7b670f26ec5fb71c34409b1eeceaee9bbaa2f2df68bfbe3b950baa
- AI_Infinity_10_Method_Deep_Research_and_Master_Command.md — SHA-256 8745f4becadbef6c7091c22538c44ffe0e576a01aee0f718d3a8facf68aa69cd
- AI_Infinity_Professional_System_Master_Command.txt — SHA-256 58ab359c131ae63528f461e022885ee5d5bb4d7c8202e97c6d584d2330d6b2df
