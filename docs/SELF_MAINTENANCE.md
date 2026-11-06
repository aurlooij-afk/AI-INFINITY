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

The allowlist covers selected root application modules, `ai_infinity/`, `scripts/`, and `tests/` Python files; protected documents, hidden files, environment files, deployment definitions, and workflow files remain excluded. Large source files are read server-side up to 1 MB per file and 2 MB aggregate, while model context is capped at 80 KB across the request. For a large module, provide a goal describing the target function or behavior; the writer receives bounded, line-numbered function/class windows, not the entire module. Proposed edits must cite the exact inspected line range and SHA-256; the server applies those edits to the full source and checks the complete resulting Python file before opening a PR.

The code writer also requires the existing Gemini Free Tier configuration: `GEMINI_API_KEY` (or `AI_INFINITY_GEMINI_API_KEY` / `GOOGLE_AI_STUDIO_API_KEY`) and an affirmative explicit data-use acknowledgement via `AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK` (or one of the supported aliases). The model is pinned to `gemini-3.1-flash-lite`; no paid-model fallback is used.

## API

All endpoints require the `X-AI-Infinity-Maintenance-Token` header.

- `GET /infinity/studio/self-maintenance/status` — reports configuration and writer readiness without returning secrets.
- `POST /infinity/studio/self-maintenance/inspect` — JSON body: `{"goal":"Locate the long-form narration planner and provider repair path.","paths":["studio_ultimate.py","tests/test_longform_production_planner.py"]}`.
- `POST /infinity/studio/self-maintenance/improve` — JSON body: `{"goal":"Repair the verified long-form narration provider path and add regression tests.","paths":["studio_ultimate.py","tests/test_longform_production_planner.py"]}`.

## Execution and release policy

The module is disabled unless explicitly enabled and both GitHub and admin credentials are configured. It accepts only allowlisted Python source and test paths, reads source files up to 1 MB (8 files / 2 MB aggregate), and limits combined model context to 80 KB. Small files are inspected whole; large modules are inspected through goal-matched function/class windows with exact line ranges and SHA-256 hashes. Large-file edits must replace only one of those exact hash-pinned windows; the server rechecks the source hash, applies the edits to the complete file, parses the complete resulting Python module, rejects likely hard-coded secrets, and requires at least one regression-test file in every change set.

An improvement is committed to a newly created `ai-infinity/self-maintenance/*` branch and opened as a **draft pull request**. It does not write to `main`, run arbitrary shell commands, merge, deploy, publish, alter environment variables, or approve rights. Review the exact diff and exact-head CI evidence before marking a pull request ready or merging it. CI success is not production certification.

## Security boundaries

Never expose the maintenance admin token or GitHub token to browser JavaScript, user prompts, model context, API responses, or logs. Send the admin token only from a trusted operator client over HTTPS. Keep production credentials out of inspected source. Protected project documents, environment files, hidden files, workflow definitions, deployment files, and non-Python files are intentionally outside the current allowlist.

An HTTP 503 from these endpoints means the feature is not configured or the free-tier writer is unavailable; it is not a reason to disable the safety gates.
