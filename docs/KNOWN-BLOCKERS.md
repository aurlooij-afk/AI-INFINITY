# AI Infinity — Known Production Blockers

Last evidence refresh: 2026-11-04 UTC  
Repository: `aurlooij-afk/AI-INFINITY`  
Primary workstream: PR [#48](https://github.com/aurlooij-afk/AI-INFINITY/pull/48), branch `fix/cinematic-creative-gates-and-review-export` (current head `b5d5717193c63e99583fa83cc7f2935b0aa5fa5c`), based on current `main`; PR [#37](https://github.com/aurlooij-afk/AI-INFINITY/pull/37) remains merged as `0a2f0c929f68d9a8c2dbf16a3dd49e1a1ed58ba4`.

This file is a live release checklist, not a claim that the product is complete. A blocker is closed only by fresh evidence from the exact tested/deployed revision.


## Follow-up implementation — 2026-11-04 (PR #48 current work)

The previous 900-second failure exposed a real wiring defect: the Google Free Tier Gemini writer adapter existed in `studio_ultimate.py`, but `creative_plan()` never selected it when no model function was supplied. The configured model name was also stale. The current PR branch now:

- Selects the explicitly acknowledged Gemini writer when a key is configured and external providers are not disabled.
- Pins the adapter and Render Blueprint to `gemini-3.1-flash-lite`; only that exact model is accepted by this no-cost adapter.
- Keeps the Google Free Tier data-use acknowledgement separate and default-off. Google currently lists Gemini 3.1 Flash-Lite input and output as free on its Free Tier, while stating that Free Tier content is used to improve Google products. Do not enable the acknowledgement without accepting that data-use condition. Reference: https://ai.google.dev/gemini-api/docs/pricing
- Retains the spoken-word minimum, bounded repairs, sentence-uniqueness check and fail-closed long-form preflight. The planner records provider, model finish reason, token usage, actual/required word counts and repair count.
- Adds a mocked 900-second regression proving the configured Gemini adapter is selected, the initial draft is repaired, and the complete narration exceeds 1,875 words. This test proves orchestration, not live provider availability or quota.
- Passes the GitHub repository secret `GEMINI_API_KEY` and separate repository variable `AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK` into the Final Acceptance Docker run. The variable is intentionally not set by this PR.

The current branch also adds `scripts/verify_live_project_state_storage.py`. It performs a real B2 canary round-trip, a separate uniquely keyed SQLite snapshot round-trip, and a clean child-process restore check; it reports `BLOCKED` if required credentials are absent and `FAIL` if configured operations fail. Its key is a random suffix under the existing project-state key prefix; it does not overwrite the production snapshot key. The Final Acceptance workflow now runs this verifier with optional repository secrets and uploads the structured result. The storage regression suite also verifies truthful `BLOCKED` reporting with no credentials.

### External execution still required

- Long-form generation is **implemented but not yet live-proven**. To activate the no-cost Gemini path, the repository owner must add GitHub Actions secret `GEMINI_API_KEY` from a Google AI Studio Free Tier project and set repository variable `AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK=true` only after accepting the Free Tier data-use terms. Configure the same key and acknowledgement in Render only when an authorized deployment is being prepared. Missing key or quota must remain visibly blocked; no paid fallback is allowed.
- B2 persistence is **not verified** until the workflow reports `PASS` after real PUT/HEAD/GET/SHA and fresh-process restore. For this configured deployment, `B2_ENDPOINT` defaults to `https://s3.us-east-005.backblazeb2.com` and `B2_BUCKET` defaults to `ai-infinity-state`; confirm the endpoint against the bucket details. The private `B2_KEY_ID` and `B2_APPLICATION_KEY` must still be present as repository Actions secrets (or supplied through the `BACKBLAZE_B2_*` aliases). The verifier reports `BLOCKED` until both key values are available.
- Actual GPU-backed Wan2.2 inference is still not demonstrated. The existing free Render Web Service is not GPU compute. Do not set the worker URL/token or report inference available until the separate authenticated worker returns its live capability proof with verified weights, license, actual NVIDIA hardware/VRAM, FFmpeg/FFprobe and persistent storage.
- Current Render inventory still shows the AI-INFINITY web service tracking `main` on the free plan, no persistent disk, a free Postgres instance expiring `2026-11-06`, and a suspended free Key Value instance with persistence off. Live Render endpoints were not reachable through the available read interface in this refresh. No PR #48 deployment or environment/secret mutation has been performed.
- The imported OTIO timeline has not yet been re-rendered as part of the acceptance workflow. Human creative/rights review remains `PENDING`.

The current exact-candidate CI workflows must finish before any claim about this code revision is made. A mocked writer regression, storage verifier script, successful build, or artifact upload is not proof of live Gemini generation, B2 restore, GPU inference or production deployment.


### Additional production-gate hardening — 2026-11-04

- Long-form planner completion now requires both the calculated spoken-word minimum and an 85% distinct-sentence ratio. A word-count-complete but repetitive first draft triggers a bounded rewrite; the production preflight independently recomputes both measures before media generation. The 900-second acceptance profile now records actual script-generation metadata and checks the resulting mastered audio against -23 to -14 LUFS and -1 dBFS true-peak limits.
- Scenario 04 now submits the imported OTIO timeline to the canonical timeline-render endpoint, then probes and fully decodes the new master. Its evidence records the rerender SHA-256, duration, resolution and decode status. This exact-candidate scenario has not yet completed.
- A candidate-bound certificate script and regression suite were added. It keeps engineering acceptance, exact-revision live deployment, durable storage, live GPU inference, OTIO re-render and six-role human approval separate. It requires Director, Cinematographer, Editor, Sound, Story and Rights scores (each >=4/5), average >=4.5/5, explicit rights approval and approval bound to the exact candidate SHA.
- The 13-scenario acceptance workflow emits a gate report with its artifact bundle when any gate is incomplete or blocked. It creates `production-certificate.json` only after all seven production requirements pass; a missing or blocked gate never produces a certificate. A green wrapper step or static workflow does not turn an absent external result into a pass.

### External gate evidence — exact tested run, not extrapolated

The [Final Complete Acceptance run #1039](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/41736205595), on candidate `769d4c5673e43a703c439bc4a29fde52ac19b0de`, produced a structured evidence artifact. The B2 verifier reported `BLOCKED`: no endpoint/region, bucket, key ID or application key were configured in that workflow environment; no PUT/HEAD/GET or restore was attempted. The Wan verifier reported `BLOCKED`: no authenticated worker URL/token were configured, and no inference request was sent. The certificate correctly recorded `ENGINEERING_STATE=FAIL` because the acceptance run was cancelled with zero completed scenarios, `PRODUCTION_VERIFIED=BLOCKED`, and `CREATIVE_APPROVAL=PENDING`. Artifact ZIP digest: `e5e1767258f2ca00be9b10cbb06011819afdc48f8ba26fdadce3eadbbf7867e2`.

Do not carry those external BLOCKED states onto another revision as a substitute for a fresh run, and do not treat the wrapper steps' successful exit codes as storage or GPU proof. The current candidate `b5d5717193c63e99583fa83cc7f2935b0aa5fa5c` has fresh Build Guard, Standalone Runtime, Visual Diversity and Final Acceptance workflows running; its exact final acceptance and certificate have not completed yet.




## Evidence refresh — 2026-11-04 (exact PR #48 candidate)

Inspected current PR metadata and completed GitHub Actions evidence for PR head `c81969a5764032f297bfa41b3bb690f9f954a0c5`. These results apply only to this exact tested head.

- [Creator Studio Build Guard #1411](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/41700370210) — PASS (Python syntax/runtime guards, focused contracts, and production Docker image build).
- [Standalone Runtime Verification #1113](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/41700370354) — PASS (professional-closure regression tests, standalone runtime, HTTP smoke, creator production smoke, and evidence assembly).
- [Visual Diversity Truth Regression #403](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/41700370289) — PASS.
- [Final Complete Acceptance #1008](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/41700370414) — FAIL: 12 of 13 scenarios passed; scenario 12 (15-minute long-form production) failed its script preflight.

### Current flagship evidence on this candidate

Scenario 01, project `studio_b50ce7e3184143f59bd012e27450afd0`, passed the suite's flagship acceptance:

- 60.082683-second final MP4; 1920×1080, 16:9, H.264 video, AAC stereo audio, and embedded English MOV-text captions.
- The exact required narration sentence was found in both the script and normalized SRT captions.
- Professional truth was VERIFIED; fallback visual count, unresolved-rights count, and rights-review-required count were all zero.
- Audio master and isolated narration were both present, independently probed, distinct files, and the programme audio duration matched the video within the suite tolerance. Integrated loudness was checked against the suite's current acceptance band.
- Final MP4 SHA-256: `9814eff6637e4ae19debbb5c51fb833408d65027b7299ad1262dd903d844303f`; size 30,288,599 bytes. The production manifest's final-media hash matched the downloaded MP4.
- Reviewer packet and contact sheet were generated with recorded hashes. Human review remains `PENDING`; no creative approval is inferred.

The acceptance test probes the final MP4 and verifies the package/manifest, but its flagship scenario does not yet record a separate standalone FFmpeg full-decode command result in the scenario payload. Keep that as an acceptance-evidence improvement rather than claiming it was independently proven by this scenario.

### Long-form blocker — still open

Scenario 12 submitted a real 900-second / 720p narrated production request. The planner produced 365 spoken words; the minimum at the configured 125 WPM floor was 1,875. The request correctly failed before media generation because no long-form text-generation provider was configured and the deterministic built-in output is only an outline. The system explicitly refused to stretch the short outline across a 15-minute timeline.

Do not reduce the word-count floor, pad repeated narration, mark the failed job as completed, or substitute a synthetic PASS. Closure requires a genuinely executable, no-cost/authorized long-form writing path that returns a complete scene-specific script, followed by narration, 900-second rendering, synchronized captions, independent media inspection, and passing production truth.

### Timeline and storage scope

- Scenario 04 passed an OTIO export/import exercise for a separate 20-second project. The downloaded timeline and portable bundle hashes were verified, archive media references and per-file hashes were checked, and import correctly retained a re-render requirement and `PENDING` human-review state. This is not yet proof of re-rendering the imported flagship timeline.
- Scenario 08 passed a process restart in the same test container while explicitly reporting persistent storage as unverified. It is not a B2 PUT → HEAD → GET → SHA or clean-instance restore proof.
- The standalone evidence workflow assembled the 60-second review deliverables and OTIO artifacts on this candidate, but artifact upload is not equivalent to proving live external storage durability.

### Remaining release gates

1. Long-form text generation and real 900-second delivery — FAIL on this candidate.
2. Separate flagship FFmpeg full-decode evidence and post-OTIO-import re-render — not yet established by the flagship acceptance record.
3. Live exact-revision deployment and live production/artifact read-back — not established by these repository workflows.
4. Actual Wan2.2 GPU inference on verified hardware — not established; CI contract checks are not GPU execution.
5. B2 canary read-back and fresh-instance project-state restore — not established.
6. Human creative/rights approval for the exact flagship version — PENDING.



## Current production evidence refresh — 2026-11-02

This inventory is a fresh read of Render and GitHub metadata. It does not prove runtime inference or state restoration.

- PR #48 is still open and unmerged. Its current head is `d5a909aa2edc37980102d61cbd944dc5f7f8adda`.
- Current PR-head CI: Visual Diversity Truth Regression run [41364303329](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/41364303329) passed; Standalone Runtime Verification [41364303219](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/41364303219) and Final Complete Acceptance Suite [41364303207](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/41364303207) were still in progress at this refresh. These remain repository CI, not GPU-worker or live storage proof.
- Render service `srv-dallhl740ujc73eetgp0` is a single-instance free web service on `main`. Its latest listed live deploy is `dep-dbdrn6e1egvs73f6pnhg`, commit `8cc20501b087064c9016d640ea5213477652a863` (2026-10-23). The current PR head is not deployed.
- Render service inventory returned only this web service; no separate GPU worker is provisioned. A real GPU-backed Wan2.2 inference has therefore not been demonstrated.
- The service event feed records a suspension by `Monthly Quota Exceeded` and subsequent resume on 2026-11-01. It is currently marked not suspended, but this is an operational availability warning.
- The workspace contains a free Postgres instance `ai-infinity-free-durable` with status available and expiry `2026-11-06T02:35:01Z`. The PR Blueprint does not wire a `DATABASE_URL`/database reference, and this expiring free database is not an acceptable permanent production-state guarantee. A hosted MCP read-only SQL query was blocked because its external IP was not on the database allowlist; no table/data inventory was obtained.
- The free Key Value resource `ai-infinity-production-queue` is suspended and reports persistence mode `off`. It is not a valid persistent queue at this time.
- A fresh HTTP probe of the public health routes could not be completed from the current network environment (DNS resolution failed). Do not translate the Render service's metadata status into a live application-health claim.

### Production graph durability propagation fix — committed, CI pending

A source review on 2026-11-02 found that `production_graph.db()` opened a raw `sqlite3.Connection` rather than using Creator Studio's configured `_connect()` factory. The graph's job/candidate/shot writes could therefore bypass the `DurableConnection` post-commit snapshot scheduler even when the B2 state adapter was active. PR #48 now routes this graph connection through the Creator Studio factory and adds a regression test proving a committed graph write schedules a snapshot. The same update corrects two readiness claims: provider-token presence no longer counts as live native video inference, and the persistent-queue flag now depends on verified project-state durability. The SQLite workspace catalog now labels local-only state as runtime-local rather than persistent.

These are code-side corrections only. They do not establish that B2 is configured, that its PUT/HEAD/GET/SHA canary currently succeeds, that a snapshot was restored on a fresh instance, or that a GPU inference completed. Their focused tests must pass on the exact PR head before the code fix itself is marked validated.

## Fresh acceptance failure evidence — 2026-10-30

- PR #48 head before this evidence-runner update: 27b6bc5c11fbd4a165fd82b2e5f163ec127757ed; the PR base matched main at 726199a5f2d68228c5ba3d2afa4fdae17dfd5ed2.
- [Final Complete Acceptance Suite run 40961860402](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/40961860402) failed on scenario 12. The CI merge-ref SHA printed by the job was 13d9606593a0ddca85f5a56d3eee706f4329668f; the PR head SHA is recorded separately above.
- Failed long-form project: studio_15e72ea2f0894332b9a803a45c440a21. The renderer produced final.mp4 (42,075,916 bytes; SHA-256 5694b2015b18df8ebcc474911de6665d108a5ea5d4f44fddf8ccb0a258faaf60) with FFprobe duration 180.041 seconds, H.264 1280×720 at 24 fps, AAC stereo audio, and embedded subtitles. The project nevertheless failed its truthful production gate.
- Recorded failures: full_hd, required_source_motion_coverage, and fallback_visual_budget_ok. The quality evidence recorded 12 distinct qualifying motion sources against a requirement of 13, five procedural fallback visuals against a budget of two, and one rights-review-required visual asset. Research returned 19 sources; the voice provider was Edge neural English, with zero voice fallbacks. The final media's technical decodability does not override these failures.
- The acceptance script had an all-or-nothing reporting defect: it printed its scenario JSON only after every scenario completed. A failure in the final profile meant earlier results were absent from the final JSON; the workflow then wrote a generic result object rather than preserving scenario results.
- The previous profile labelled itself long-form but exercised 180 seconds, not the required 900-second / 15-minute workload. The updated acceptance matrix adds the 900-second 720p workload and separate 20-second 720p and 1080p profiles. These checks are pending execution on the new commit; they are not passes.
- Three other workflows on the same PR head passed: Visual Diversity Truth Regression (40961860255), Creator Studio Build Guard (40961860290), and Standalone Runtime Verification (40961860268). The failed final acceptance run remains a failure.

## Recorded passing CI and artifact evidence (historical exact tested SHA)

Exact tested PR head for the 12-scenario record below: `eac6cc9b98ccf5877788498bd38908b5eaec0d5a`. These results are not automatically inherited by later commits or by the live service.

- [Visual Diversity Truth Regression #169](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/39539544286) — passed.
- [Creator Studio Build Guard #724](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/39539544533) — passed (Python smoke checks and production Docker image build).
- [Final Complete Acceptance Suite #392](https://github.com/aurlooij-afk/AI-INFINITY/actions/runs/39539544258) — passed all 12 scenarios and uploaded artifact evidence.

Acceptance artifact: [`ai-infinity-final-acceptance-evidence`](https://api.github.com/repos/aurlooij-afk/AI-INFINITY/actions/artifacts/12319757374), SHA-256 `a737b829c0f2621bead38e9d8fed3a847b3658d660fcb63d5a2db94230427ded`. The ZIP contains `final-acceptance-result.json`, failure/review-required evidence, contact sheets and reviewer packets.

The 12 passing scenarios establish:
1. A 60.103-second 720p launch video with verified truth and manifest/media hashes; a 24,041,549-byte reviewer packet was generated. Its human review status remains `PENDING`.
2. Pashto script/caption text checks and neural voice output (`edge-tts-neural:ps-AF-LatifaNeural`, 58,320-byte narration sample); the project truth endpoint was verified.
3. Uploaded PDF content is extracted and used in the requested topic.
4. A reference URL is used as research grounding.
5. Three short-form variants are generated.
6. The original vector illustration fallback path is exercised and identified as fallback.
7. When research is degraded, truth remains `REVIEW_REQUIRED` and final download is blocked.
8. Project state survives a process restart in the same test container, while the system correctly refuses to claim persistent storage and rejects caller-supplied confirmation flags.
9. Project isolation is verified.
10. Recovery retry and retry audit work; a six-fallback-visual project remains `REVIEW_REQUIRED` and publishing is blocked with HTTP 409.
11. A real 20.057-second 1920×1080 render passed truth, zero-fallback-visual, full-decode, and manifest-SHA checks across five scenes.
12. A real 180.105-second 1280×720 render passed truth, zero-fallback-visual, full-decode, and manifest-SHA checks across eight scenes.

These results close the earlier repository-CI gap for the tested 1080p and three-minute 720p profiles. They do not close live deployment, external storage provisioning, human/native-language review, arbitrary-topic provider availability, or untested integration/platform gaps.

## Open release blockers

### 1. Repository-side production profile matrix — PASS on the recorded SHA; re-run after any new code change

The expanded suite passed on `eac6cc9b98ccf5877788498bd38908b5eaec0d5a`. It produced and independently probed a real 20-second 1080p render and a real three-minute 720p render. The suite checks requested/actual duration, dimensions, streams/codecs, truth gate, rights/research requirements, zero fallback visuals, full decode and manifest SHA-256. Do not carry this PASS forward to a newer commit without a new successful run on that exact SHA.

### 2. Live Render deployment identity is confirmed; live acceptance is still open

Render service `srv-dallhl740ujc73eetgp0` uses the `main` branch. Render deploy history reports deployment `dep-dbcioq0u01pc73bc7uqg` as `live` on commit `0a2f0c929f68d9a8c2dbf16a3dd49e1a1ed58ba4`, the merge commit for PR #37. This closes the old-revision mismatch at the deployment-metadata level only; it does not prove that the live routes, a real production job, or the resulting artifacts pass. A fresh live acceptance must verify exact runtime commit identity, health routes, one real production request, artifact read-back and download gating on that revision.

### 3. Durable project-state storage and clean-instance restore remain unverified

The application contains an S3-compatible Backblaze B2 SQLite snapshot adapter with a unique canary, PUT/HEAD/GET read-back, SHA-256/size checks and startup restore validation. The recorded live Render production smoke on revision `c59421d2da4b9b32eaccdee2ca71e2b9ccfd07ed` failed after edit/undo with `HeadObject 403 Forbidden`; the reported backend state was `configured=true`, `active=false`, `verified=false`, `snapshot_verified=false`, and `persistent=false`. That is historical failure evidence, not a fresh reading of the current deployment's backend state. The free web-service plan does not establish a persistent volume.

A code-side signing-region correction was merged through PR #37 and is present in deployed commit `0a2f0c929f68d9a8c2dbf16a3dd49e1a1ed58ba4`: when a B2 endpoint specifies a region and no region variable is set, the adapter derives the signing region from the endpoint hostname while preserving explicit configuration. This remains a plausible contributor to the historical 403, **not proof that it was the sole cause or that live storage is fixed**. The current follow-up branch also changes the default startup canary to share the configured snapshot object's key prefix, so prefix-restricted B2 keys can be tested against their actual allowed namespace. That code is not deployed until reviewed, merged and released.

Closure evidence required from the live backend: protected credentials with appropriate bucket/prefix permissions; isolated canary PUT → HEAD → GET with identical bytes and SHA-256; project-state snapshot round-trip; restart or fresh-instance restore; project and artifact reload; and recorded exact deployed revision/backend state. If 403 persists, verify the configured B2 key's read/list/write/delete permissions and bucket/prefix scope in the owner's provider dashboard without exposing any secret in source or chat. Do not add paid infrastructure or place secrets in source code to bypass this limitation.

### 4. Source-media availability is not guaranteed for every brief

At least one real launch production passed with zero fallbacks and cleared rights. Other scenarios intentionally disable external providers or inject recovery fixtures; those outputs correctly remain `REVIEW_REQUIRED`, have final download blocked, and cannot publish. This is correct failure behavior, not proof that arbitrary topics always have available source footage. Keep provider failures, rights uncertainty and fallback use visible. A professional verification claim requires scene-specific relevance, source metadata and rights evidence for the actual assets in that production.

### 5. Human editorial and native-language approval remain pending

Reviewer packets and contact sheets are generated, but a packet is not a human review. The recorded human review state is `PENDING`. Story clarity, visual composition, edit rhythm, voice pronunciation/intelligibility, caption legibility, factual support, rights/attribution and brand fit must be evaluated by real reviewers. Pashto TTS output has automated script/provider/media checks; it does not prove human or native-speaker approval. No human score or approval may be inferred.

### 6. Timeline interchange and operational scale are not proven

The current acceptance suite does not establish a successful OpenTimelineIO media-reference round-trip, a measured MLT-versus-FFmpeg comparison, or a representative concurrency/load benchmark across 20-second, 60-second and 180-second jobs. Keep FFmpeg as the known baseline; adopt OTIO/MLT only when round-trip, referenced-media resolution, re-render and resource measurements pass on real artifacts. Distinguish supported features from roadmap candidates.

### 7. External platforms are not universally feature-complete

AI Infinity has working local creation and a set of provider adapters, but it has not demonstrated exact feature parity with every AI creation platform. For each named provider/capability, record whether the adapter exists, what credentials/scopes it needs, whether a live call succeeded, whether returned bytes validated, source/license restrictions, quota handling, and whether the capability is production-tested. YouTube publishing remains authorization- and quality-gate-dependent; TikTok/Meta/API analytics cannot be represented as connected or working without their actual authorized integration and returned remote evidence.

### 8. Protected research documents must remain untouched

Do not rewrite, regenerate or reconstruct the separately maintained `AI_Infinity_Creator_Benchmark_and_Reuse_Plan.md` or `AI_Infinity_10_Method_Deep_Research_and_Master_Command.md` as part of this closure. Continue the existing PR and preserve both documents byte-for-byte wherever they reside in the user's source-of-truth library. If the raw bytes cannot be read, state that; do not claim a byte comparison was performed.

## Release decision rule

PR #37 is merged and its merge commit appears in Render's live-deploy metadata, but the complete release is **not verified**. Follow-up code changes require their own passing focused regression/build/full acceptance checks and an authorized release. Production closure still requires live exact-revision HTTP and artifact acceptance, successful real B2 PUT/HEAD/GET/SHA/size and fresh-instance restore, plus actual human/native-language creative review. A green CI suite or `live` deploy label alone cannot close these external and human gates.


## Active release snapshot — 2026-11-04

The active candidate is PR #49, branch `feat/universal-command-batch-and-media-bound-review`, head `91f99e34c20f8bd803e6d79b5b81ada5fe1bc867`. It includes ordered multi-command execution, media-bound human approval, recursive rejection of nested credential fields, atomic batch idempotency persistence, browser-side retry-key retention, and certificate issuance that is forbidden unless every production gate passes. The latest Visual Diversity Truth Regression on this SHA passed; Build Guard, Standalone Runtime and Final Acceptance are being checked.

The prior exact-candidate Final Acceptance run on SHA `636a47b771d321c08a3bb9753170f3af96abf16f` produced explicit structured evidence:

- Gemini writer: `BLOCKED` because `GEMINI_API_KEY` was empty. The acknowledgement variable was also not the literal `true`; the logged value was explanatory prose. The 13-scenario suite was skipped.
- B2: `BLOCKED`, with `key_id` and `application_key` missing from the GitHub Actions environment. No PUT/HEAD/GET or fresh-process restore was attempted.
- Wan2.2: `BLOCKED`; worker URL/token were missing and no inference request was sent.
- Exact-candidate deployment and human creative/rights approval remain blocked. Render still deploys `main`, not this PR head.

Render's startup log previously confirmed that the Backblaze adapter was configured, active and verified. That is configuration evidence only; it is not the canary round-trip or restore proof. The workflow also now contains a main-push-only live deployment verifier: it waits for the exact `RENDER_GIT_COMMIT`, submits a real 20-second create request, downloads the required package and MP4, checks artifact presence, probes both streams, and full-decodes the video. PR acceptance intentionally does not run this gate because the PR head is not live. Do not merge or deploy PR #49 until the pre-deployment engineering and external gates pass. After merge, the main workflow must independently pass exact-revision live deployment verification before it can issue the certificate.


## Final human approval and certificate issuance

After a main-branch Final Complete Acceptance run has passed the six non-human gates, the owner reviews the exact flagship MP4 downloaded from that run's `ai-infinity-final-acceptance-evidence` artifact. The acceptance result records its `final_media_sha256`; the owner must review those exact bytes and the accompanying provenance/rights packet.

The owner then runs **AI Infinity — Human Approval and Production Certificate** from GitHub Actions on the `main` branch. Inputs are the main-branch acceptance run ID, its exact `head_sha`, and a one-line JSON scorecard containing:

```json
{"approved":true,"rights_approval":true,"reviewed_media_sha256":"<exact final_media_sha256 from scenario 01>","review_notes":"Describe the actual story, visual, edit, sound and factual review.","rights_notes":"Describe the source and license evidence reviewed, plus any exceptions.","dimension_scores":{"director":4.8,"cinematographer":4.6,"editor":4.7,"sound":4.5,"story":4.8,"rights":4.6}}
```

The example scores are illustrative only; the real reviewer must enter their own honestly assessed scores. The workflow attributes the reviewer to the authenticated GitHub actor and stamps its UTC submission time. It refuses mismatched commit/media hashes, any non-human gate not already PASS, missing/false rights approval, incomplete notes, any dimension below 4.0, or an average below 4.5. Only then does it create and upload `production-certificate.json`. This workflow does not rerun the production suite and cannot turn a blocked acceptance run into a pass.
