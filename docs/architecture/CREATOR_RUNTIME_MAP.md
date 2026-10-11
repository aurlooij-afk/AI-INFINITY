# AI Infinity runtime architecture map

Date: 2026-10-12
Scope: source inspection of the PR #37 code line. This is a static architecture map, not proof that the deployed revision is identical or healthy.

## Deployed entrypoint and registration order

The configured Render command is uvicorn main:app --host 0.0.0.0 --port $PORT, so main.py is the deployment entrypoint. creator_entrypoint.py is a separate ASGI dispatcher used by tests and optional callers; it delegates to main.app for the foundation routes and registers the richer Creator Studio app.

The source-level startup path in main.py is:

1. Import canonical contract, Reality Kernel, Studio engine, production graph, intelligence, OpenAI video, closure, timeline patch, Creator OS extension, capability fabric and editorial review.
2. Dynamically load ai3704_storage_fabric.py into the main FastAPI app with the Studio database/lock/clock/user helpers injected; fail startup if the required storage status/verify routes are absent.
3. Install reality_first_3901, production_graph, production_openai_video, production_closure_3624, then editorial_review_3625.
4. Register routes in order: canonical contract, Studio, Reality Kernel, production graph, intelligence, remote OpenAI video, production closure, editorial review, timeline patch, Creator OS extension and capability fabric.
5. Reapply editorial review after add-ons so the final served surface retains the approval panel.

The installation order is operationally significant: wrappers can intercept the same publisher and lifecycle hooks. In the latest recovery acceptance run, the publication request returned blocked_by_editorial_review, because the downstream review gate intercepted the attempt before the publish wrapper returned blocked_by_quality_gate. Both states are fail-closed; tests must verify blocking/no side effect, not require a particular internal wrapper name.

## Route and responsibility map

| Surface | Source owner | Responsibility |
|---|---|---|
| Canonical command/preflight/memory/version contract | ai_infinity_canonical.py | Translate the user's primary intent into a bounded request, idempotency contract, project/version records and reconciliation |
| Studio routes, media jobs and core rendering | studio_ultimate.py | User session, project queue, research, creative plan, TTS, per-scene media acquisition, FFmpeg render, captions, package, assets and retry |
| Objective media/provenance truth and publish wrapper | production_closure_3624.py | ffprobe/media anomaly checks, scene count/diversity, captions, fallback status, artifact reconciliation, truth and a publish gate |
| Artifact inspection and production workflow graph | reality_first_3901.py | Independent file inspection, lifecycle state, evidence and recovery coordination |
| Shot graph/candidate/review/job records | production_graph.py | Separate production graph model, candidates, shot reviews, jobs and optional Redis connectivity |
| Research and visual/audio intelligence adapters | production_intelligence.py | Research, vision review, TTS-related features, candidate ranking and intelligence routes |
| Optional remote video jobs | production_openai_video.py | Provider-backed remote video job lifecycle; separate from local FFmpeg rendering |
| Timeline/editor APIs | professional_creator_v2_timeline_patch.py and professional_creator_v2.py | Editable timeline operations, edit/export and compatibility APIs |
| Provider/capability inventory | professional_creator_fabric.py | Capability routing, public/runtime probes and provider reports |
| Human editorial approval | editorial_review_3625.py | Version-bound scorecard/approval contract; publication requires the applicable approval gate |
| Object-store artifact replication | ai3704_storage_fabric.py | Configured object-store uploads, replicas, read-back verification and integrity metadata |
| Durable SQLite snapshot backend | ai_infinity/project_state_backend.py | State database backup/snapshot to S3-compatible backend and restore |
| Durability truth contract | ai_infinity/persistence_truth.py | Reports persistent state only when a configured verified backend or mount evidence supports the claim |

## Data stores and queue ownership

- Studio state uses SQLite tables with the studio_projects_3610, studio_assets_3610, studio_audit/checkpoint and studio_artifacts_3614 families.
- Canonical intent/version/memory/history lives in canonical_projects, canonical_versions, canonical_memories and canonical_events tables in the same core database helper.
- The production graph also defines pg_projects, pg_shots, pg_candidates, pg_reviews and pg_jobs.
- Remote video defines its own job records/worker in production_openai_video.py.
- Object storage tracks ai_storage_assets and ai_storage_replicas; this is distinct from the SQLite state snapshot backend.

These are source-defined responsibilities, not a verified statement that every queue/storage component is running in production. Multiple state and job subsystems are a cohesion risk. Consolidation should be staged behind API compatibility tests, replay/recovery tests and owner-scoped data tests; deleting or bypassing modules by name is unsafe.

## Actual local media path

The canonical create route passes the request to studio_ultimate.enqueue. The Studio worker runs research and creative planning, creates an ordered scene plan, renders per-scene narration and visual assets, and uses FFmpeg for scene rendering, concatenation, duration normalization, subtitle muxing/burning and packaging. Production closure wraps this lifecycle to produce and reconcile truth evidence after output exists. The final MP4 is then independently inspected by the truth path; file existence alone is not professional verification.

A provider failure must stay attached to its scene attempt. The original CC0 recovery fixture is enabled only with CI=true, AI_INFINITY_TEST_MEDIA=1 and the exact allow-listed fixture ID. It is not a footage provider, cannot establish source_visual_policy_passed and must result in REVIEW_REQUIRED.

## Known architecture risks and next boundary

1. Wrapper/install order is important; add a focused test for every publisher/retry lifecycle gate so later wrappers cannot bypass prior gates.
2. The graph, remote-video worker and Studio queue have separate records. A durable project needs an explicit parent job, idempotency key, lease/heartbeat, cancellation semantics and per-scene attempt/provenance records across these surfaces.
3. Current Studio rendering is FFmpeg-first and is the stable reference. OpenTimelineIO remains a planned editable/interchange format, not a renderer. MLT should be evaluated as a bounded render-worker spike, not adopted without memory/time/quality comparisons.
4. Provider credentials and reachability must be diagnosed on the live runtime without exposing secret values. Environment presence alone does not prove active provider functionality.
5. The public vanity endpoint returned 503 in the latest external probe. The Render service's latest recorded live deployment is on main at c59421d2da4b9b32eaccdee2ca71e2b9ccfd07ed, while PR #37 is not deployed. Current live revision/health remains unverified.
6. Current storage logs include historical B2 verification, but the latest session did not complete a fresh isolated PUT/HEAD/GET/hash canary and clean-instance restore. Artifact replication is not proof of project-state durability.
7. A generated contact sheet and reviewer packet are evidence collection only; scores remain blank until reviewers actually inspect the complete media. Human/native-language review is a release gate, not an automated status.

## Preservation

The benchmark and ten-method research reports remain unchanged as separate artifacts. Their recorded SHA-256 values are in docs/adr/ADR-0001-acceptance-truth-and-recovery-fixtures.md.
