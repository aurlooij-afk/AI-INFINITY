# AI Infinity known blockers

Checked 2026-10-12 against PR #37 head 86d65d7c604020412954bdfdb7e462eb63188428. This record is refreshed from the exact workflow output; do not declare a blocker closed from code inspection alone.

## Current release blockers

1. **Rights gate fix exposed a real evidence-writer boundary error and fallback scenes.** Visual Diversity Truth Regression run 38247302675 passed; Creator Studio Build Guard run 38247302694 passed; Final Complete Acceptance Suite run 38247302672 failed on scenario 1. The failure output truthfully showed state REVIEW_REQUIRED, three illustrative fallback scenes, source_visual_policy_passed=false and no visual_rights.json. The missing file was caused by the rights helper having been inserted inside the evidence writer's region, making the remaining writer code unreachable. This commit moves the helper to module scope and restores the visual-rights evidence body within _write_production_evidence. After this correction, run the full suite again. If the real provider still cannot supply relevant, source-backed, compatible media for every required scene, scenario 1 must remain failed/REVIEW_REQUIRED until that production root cause is resolved.
2. **Live site/revision unverified.** The available probe saw https://ai-infinity.blitz.cloud/ return HTTP 503 on 2026-10-12. Render metadata identifies https://ai-infinity-ca5e.onrender.com (service srv-dallhl740ujc73eetgp0); its latest recorded live deployment is dep-db4v138u01pc73fohdh0 at c59421d2da4b9b32eaccdee2ca71e2b9ccfd07ed from October 10. That predates PR #37. Actual current Render HTTP status and running revision remain unverified.
3. **Storage restore incomplete.** The SQLite process-restart scenario explicitly reported persistent_storage_claimed=false. No current isolated B2 PUT/HEAD/GET/hash/size canary or clean-instance project/database restore proof is available.
4. **Editorial/native-language review pending.** The previous reviewer packet recorded human_review_status=PENDING. Pashto used local-espeak-pashto-fast-fallback; native-speaker review is not recorded. No two-reviewer scorecard has passed for the current production artifact.
5. **Production profile/editable export unproved.** The last accepted MP4 was 1280x720. A real requested 1080p profile, three-case MLT comparison and OpenTimelineIO package with external source media have not been proven on the deployed revision.
6. **Provider-backed quality is distinct from fixtures.** Recovery fixtures validate retry mechanics only. Run a separate real-provider-backed 60-second production and inspect visual relevance, license/terms, resolution and independent QC; fixtures do not prove live provider quality.
7. **Original research documents must remain unmodified.** Both AI_Infinity_Creator_Benchmark_and_Reuse_Plan.md and AI_Infinity_10_Method_Deep_Research_and_Master_Command.md were found in the Project Library and have not been overwritten. They do not appear in the current PR branch tree. The additive benchmark report is not a replacement for either original. To copy originals into this branch byte-for-byte, a raw-byte source must be available; the Files connector denied raw-byte materialization for these Library objects, so no reconstructed text is being substituted.

## Workflow links

- [Visual Diversity Truth Regression — 38247302675](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38247302675): success on commit 86d65d7c604020412954bdfdb7e462eb63188428.
- [Creator Studio Build Guard — 38247302694](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38247302694): success on commit 86d65d7c604020412954bdfdb7e462eb63188428.
- [Final Complete Acceptance Suite — 38247302672](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/38247302672): failure on scenario 1; do not treat its previous success on an older SHA as release proof.

## Release discipline

- No merge while a required check is red/pending or artifact evidence contradicts a success label.
- No deployment before all required pre-release gates pass and the target/authorization are clear.
- Unknown/incompatible rights, missing provenance and fallback visuals block professionally verified delivery and publishing.
- No human-artistry/language claim without recorded reviewer evidence.
- No durability claim without current read-back hash/size and clean-instance restore proof.
