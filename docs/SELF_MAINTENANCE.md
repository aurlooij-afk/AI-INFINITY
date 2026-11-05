# AI Infinity repository self-maintenance

AI Infinity includes a guarded repository-maintenance API. It can inspect allowlisted Python source, use the existing acknowledged Gemini Free Tier writer to propose a bounded code change, and create a draft GitHub pull request. GitHub Actions then runs the repository's existing verification workflows.

## Required configuration

Configure these values as service secrets/environment variables in the deployment platform. Do not commit any value to this repository.

| Variable | Required value |
|---|---|
| `AI_INFINITY_SELF_MAINTENANCE_ENABLED` | `true` |
| `AI_INFINITY_MAINTENANCE_REPOSITORY` | `aurlooij-afk/AI-INFINITY` |
| `AI_INFINITY_MAINTENANCE_BASE_BRANCH` | `main` |
| `AI_INFINITY_MAINTENANCE_GITHUB_TOKEN` | Fine-grained GitHub token limited to this repository, with Contents: Read and write and Pull requests: Read and write. Metadata read is required by GitHub. |
| `AI_INFINITY_MAINTENANCE_ADMIN_TOKEN` | Independently generated high-entropy secret. Never reuse a session cookie, provider key, or GitHub token. |

The code writer also requires the existing Gemini Free Tier configuration: `GEMINI_API_KEY` (or `AI_INFINITY_GEMINI_API_KEY` / `GOOGLE_AI_STUDIO_API_KEY`) and an affirmative explicit data-use acknowledgement via `AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK` (or one of the supported aliases). The model is pinned to `gemini-3.1-flash-lite`; no paid-model fallback is used.

## API

All endpoints require the `X-AI-Infinity-Maintenance-Token` header.

- `GET /infinity/studio/self-maintenance/status` — reports configuration and writer readiness without returning secrets.
- `POST /infinity/studio/self-maintenance/inspect` — JSON body: `{"paths":["studio_ultimate.py","tests/test_longform_production_planner.py"]}`.
- `POST /infinity/studio/self-maintenance/improve` — JSON body: `{"goal":"Repair the verified long-form narration provider path and add regression tests.","paths":["studio_ultimate.py","tests/test_longform_production_planner.py"]}`.

## Execution and release policy

The module is disabled unless explicitly enabled and both GitHub and admin credentials are configured. It accepts only allowlisted Python source and test paths, applies strict file/count/size limits, rejects likely hard-coded secrets, parses proposed Python, and requires at least one regression-test file in every change set.

An improvement is committed to a newly created `ai-infinity/self-maintenance/*` branch and opened as a **draft pull request**. It does not write to `main`, run arbitrary shell commands, merge, deploy, publish, alter environment variables, or approve rights. Review the exact diff and exact-head CI evidence before marking a pull request ready or merging it. CI success is not production certification.

## Security boundaries

Never expose the maintenance admin token or GitHub token to browser JavaScript, user prompts, model context, API responses, or logs. Send the admin token only from a trusted operator client over HTTPS. Keep production credentials out of inspected source. Protected project documents, environment files, hidden files, workflow definitions, deployment files, and non-Python files are intentionally outside the current allowlist.

An HTTP 503 from these endpoints means the feature is not configured or the free-tier writer is unavailable; it is not a reason to disable the safety gates.
