# AI Infinity — Engineering Decisions

## 2026-11-05 — Preserve review-required native drafts without granting approval

**Decision:** The Reality Kernel may preserve a project as `completed_with_qc_warnings` only when its unverified truth state is explicitly `DEGRADED` or `REVIEW_REQUIRED`, and the current project state is already `completed` or `completed_with_qc_warnings`.

**Reason:** Native deliverables can be independently intact while a companion video or professional QC remains open. Converting this state to `FAILED` hides valid primary outputs and conflicts with the production-closure review contract.

**Safety boundary:** This does not mark the project verified, approved, or published. Required-artifact failures, `FAILED`, `BLOCKED`, missing truth, and verified reports are not preserved by this predicate. Rights and publishing gates remain authoritative.

**Evidence:** PR #49 acceptance run `42097558730`; source change `e1e2af597e5e1576d79c6af623170bba7d9469b4`; regression tests `70d1df0a6567ea2d42615697b02140522f009558`.

## 2026-11-05 — Acceptance workflow must not cancel in-flight runs

**Decision:** Set `cancel-in-progress: false` for the 19-scenario acceptance workflow.

**Reason:** Acceptance runs can be long-running; a later commit must not cancel the candidate's evidence collection.

**Evidence:** Workflow commit `28bf8a317119e643876f76359975469708700024`.

## 2026-11-05 — Protected documents remain untouched

**Decision:** Do not reconstruct, rename, regenerate, or modify the two protected research documents. The candidate tree and accessible Library do not contain their bytes; ADR-0001 contains historical SHA-256 references only.

**Consequence:** Per-commit byte-preservation verification cannot yet be established. Record as BLOCKED; do not invent hashes or claim compliance.

## 2026-11-05 — No certificate on incomplete gates

**Decision:** No Release Verification Certificate until all seven gates have evidence bound to the exact candidate, including durable recovery, authenticated GPU inference, exact-candidate live deployment, and independent human creative/rights approval.
