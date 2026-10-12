# AI Infinity known blockers

Checked 2026-10-12 against PR #37 head 367b9db5dcf19114f9eaee6831bf5f7f568918da. Close blockers only with current workflow and artifact evidence.

## Current release blockers

1. **Real-source professional production is still the first acceptance gate.** Earlier runs on this branch produced a 60-second 1280x720 H.264/AAC render with three illustrative fallbacks and no cleared source rights. Current code includes a semantic scene-focus matcher; this follow-up filters Openverse to CC0/CC BY commercial/modification-compatible records and still locally verifies each license. The ten-scenario suite must prove zero fallback scenes, relevant source metadata, requested resolution and matching artifact hashes before professional verification.
2. **Publishing must be fail-closed regardless of the legacy toggle.** The previous truth response exposed publish_gate=true while the actual state was REVIEW_REQUIRED. This commit makes YouTube and webhook paths always require a project ID and publish_allowed_by_quality_gate, including the factual-review outcome. CI must validate this exact change.
3. **Live endpoint/revision remains unverified.** The available probe saw https://ai-infinity.blitz.cloud/ return HTTP 503 on 2026-10-12. Render metadata identifies https://ai-infinity-ca5e.onrender.com (service srv-dallhl740ujc73eetgp0); the latest recorded live deployment remains main commit c59421d2da4b9b32eaccdee2ca71e2b9ccfd07ed. Do not assume current health or revision.
4. **Storage durability and restore are incomplete.** A current isolated B2 PUT/HEAD/GET/size/SHA-256 canary and clean-instance project-state restore have not been run.
5. **Editorial and native-language approval remains pending.** Reviewer packet templates are not review evidence. Pashto pronunciation, intelligibility, caption readability and cultural fit require actual native-speaker review; two reviewer scorecards remain unrecorded.
6. **Production profile/edit interchange remain unproved.** Real 1080p output, 20s/60s/3m modes, the three-case MLT comparison and an OpenTimelineIO package with referenced media have not passed on the exact deployed revision.
7. **Preserved originals remain unchanged.** Both original research documents were found in the Project Library. The connector denied raw-byte materialization; neither was overwritten or reconstructed. They remain absent from the current PR tree, so no byte-for-byte copy into Git is claimed.

## Recent workflows

- Visual Diversity Truth Regression — run 38277290751: success on 367b9db5dcf19114f9eaee6831bf5f7f568918da.
- Final Complete Acceptance Suite — run 38277290770: in progress at the latest observed check; report its final result separately.
- Creator Studio Build Guard — run 38277290750: in progress at the latest observed check.


8. **Acquisition diagnostics are now surfaced for the next acceptance attempt.** Run 38277808132 on 7686f7f94d2afe87174c1af63fcd5ef7f34865d0 still failed scenario 1 with three fallbacks, one cleared visual-rights record and 3 unresolved rights records. The next source patch adds sanitized per-scene provider failure reasons to production truth and tests the redaction. These diagnostics are for root-cause isolation; they do not relax VERIFIED.


9. **Redaction regression caught an escaping error before release.** Visual Diversity run 38278893658 on dae3f1029c166ad7336855e3d1187d4b7816d46e had 37 passing tests and one failure in acquisition-diagnostic redaction: the result contained a literal `\\1` and retained the fake test token. The next patch replaces backreference replacements with lambda callbacks and single-escaped regex patterns, then adds separate query-string and bare credential redaction coverage. This is a reporting/security correctness fix; provider failure details remain sanitized.


10. **Credential sanitizer follow-up.** Visual Diversity run 38279202973 (head ad30ab2bdd4dac09a0884b4a9e4c5456feb617ed) had 38 passed/1 failed: the sanitizer masked `Bearer` but left the following authorization token. This patch masks the complete optional-Bearer authorization value. The regression test must pass before release.