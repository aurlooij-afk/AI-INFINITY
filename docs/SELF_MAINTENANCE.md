# AI Infinity repository self-maintenance

AI Infinity includes a GitHub-backed repository execution adapter and a guarded self-maintenance engine. The operator can select `restricted` mode (default) or temporary `repository_full` mode. Full mode exposes recursive repository discovery, text/code/workflow/configuration/document inspection, commit-bound Actions inspection, and repository-scoped GitHub REST `GET`, `POST`, `PUT`, `PATCH` and `DELETE`. It measures actual operations; a configured token is never assumed to have a permission it has not demonstrated. All methods remain bound to the single `AI_INFINITY_MAINTENANCE_REPOSITORY`.

## Required configuration

Configure these values as service secrets/environment variables in the deployment platform. Do not commit any value to this repository.

| Variable | Required value |
|---|---|
| `AI_INFINITY_SELF_MAINTENANCE_ENABLED` | `true` |
| `AI_INFINITY_MAINTENANCE_REPOSITORY` | `aurlooij-afk/AI-INFINITY` |
| `AI_INFINITY_MAINTENANCE_BASE_BRANCH` | `main` |
| `AI_INFINITY_MAINTENANCE_GITHUB_TOKEN` | Fine-grained GitHub token limited to this repository, with Contents: Read and write, Pull requests: Read and write, and Actions: Read. Metadata read is required by GitHub. |
| `AI_INFINITY_MAINTENANCE_ADMIN_TOKEN` | Independently generated high-entropy secret. Never reuse a session cookie, provider key, or GitHub token. |\n| `AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE` | `restricted` (default) or `repository_full` for repository-wide text/code/config inspection and GitHub API operations. |

In `restricted` mode, paths are limited to root Python modules, `ai_infinity/`, `scripts/`, `tests/` and the two Creator Studio HTML UI entrypoints. In `repository_full` mode, the engine can inspect/edit text, code, workflows, configuration and documentation across the configured repository, including `.github/workflows/` and `.ai-infinity/`. Both modes always exclude `.env` and credential/key stores, `.git`, binary assets, and the two protected documents named above. The generic REST adapter will not cross into another repository or host. Large source files are read server-side up to 1 MB per file and 2 MB aggregate; model context remains capped at 80 KB per request. Large modules use bounded, line-numbered, SHA-256-pinned function/class windows. Proposed changes are syntax/secret-checked and still require regression tests; code maintenance produces a draft PR, not an automatic merge.

The code writer also requires the existing Gemini Free Tier configuration: `GEMINI_API_KEY` (or `AI_INFINITY_GEMINI_API_KEY` / `GOOGLE_AI_STUDIO_API_KEY`) and an affirmative explicit data-use acknowledgement via `AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK` (or one of the supported aliases). The model is pinned to `gemini-3.1-flash-lite`; no paid-model fallback is used.

## API

All endpoints require the `X-AI-Infinity-Maintenance-Token` header. Creator Studio exposes the operator console under **System → Self-maintenance**. Its password field is transient, the browser refuses to send the token over non-HTTPS production origins, and the token is not stored in local/session storage. The console supports readiness, redacted live-runtime inspection, repository discovery, bounded inspection, autonomous draft improvement, and exact-commit CI review.

- `GET /infinity/studio/self-maintenance/status` — reports configuration and writer readiness without returning secrets.
- `GET /infinity/studio/self-maintenance/runtime` — reports redacted live application version/build, the running Git SHA when the host exposes it, required route registration, media-tool availability, project-state and asset-storage configuration. Provider configuration is explicitly not treated as proof of durable writes or read-back.
- `POST /infinity/studio/self-maintenance/discover` — JSON body: `{"goal":"Locate the long-form narration planner and its regression tests.","limit":20}`. Returns allowlisted Python source candidates and matching file SHAs without returning source bodies. A truncated Git tree is reported explicitly.
- `POST /infinity/studio/self-maintenance/ci` — JSON body: `{"commit_sha":"<full 40-character SHA>","limit":10}`. Reads run/job/step outcomes for the exact commit without returning raw logs. The token needs Actions: Read.\n- `POST /infinity/studio/self-maintenance/github/execute` — JSON body: `{"method":"GET","path":"/repos/aurlooij-afk/AI-INFINITY/actions/runs?per_page=5"}`. Executes repository-scoped GitHub REST operations with timing, effective endpoint and redacted result. Supported methods are GET, POST, PUT, PATCH and DELETE. The caller must have the required GitHub token permission for each individual operation. Contents API paths still enforce the protected-document and credential-path exclusions.
- `POST /infinity/studio/self-maintenance/inspect` — JSON body: `{"goal":"Locate the long-form narration planner and provider repair path.","paths":["studio_ultimate.py","tests/test_longform_production_planner.py"]}`.
- `POST /infinity/studio/self-maintenance/improve` — JSON body: `{"goal":"Repair the verified long-form narration provider path and add regression tests.","paths":["studio_ultimate.py","tests/test_longform_production_planner.py"]}`.

## Execution and release policy

The module is disabled unless explicitly enabled and both GitHub and admin credentials are configured. It accepts only allowlisted Python source and test paths, reads source files up to 1 MB (8 files / 2 MB aggregate), and limits combined model context to 80 KB. Small files are inspected whole; large modules are inspected through goal-matched function/class windows with exact line ranges and SHA-256 hashes. Large-file edits must replace only one of those exact hash-pinned windows; the server rechecks the source hash, applies the edits to the complete file, parses the complete resulting Python module, rejects credential assignments under common key/token names and recognizable provider-token formats, and requires at least one regression-test file in every change set.

The intended sequence is **discover → inspect → plan → execute → verify → review**. Discovery reads the full recursive Git tree and can include hidden workflow/project metadata paths in `repository_full` mode. The generic executor can call any repository API route the supplied token is authorized to use, including repository content, branches, commits, pull requests, issues, Actions, checks, statuses, releases, tags and repository settings. Each call is measured and logged with its method/path/result status; credentials and secret-bearing file content are redacted. The self-maintenance writer remains a separate controlled path: it creates a temporary branch and a **draft pull request**, never a direct `main` write, merge or deployment. The benchmark uses a deterministic test writer only to exercise real GitHub discovery/read/write/PR/cleanup operations; its result measures GitHub execution, not model intelligence. Review the exact diff and CI evidence. CI success is not production certification.



## Temporary repository-wide performance mode

Set `AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE=repository_full` on the AI Infinity service to enable repository-wide text/code/configuration access. Restart/redeploy the service after changing the environment variable. The mode remains limited to the repository named by `AI_INFINITY_MAINTENANCE_REPOSITORY`; it does not grant access to other repositories or an organization. Effective operations are the intersection of this endpoint's scope and the actual GitHub token permissions.

To restore the prior narrower mode, set `AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE=restricted` (or remove the variable) and restart/redeploy. Do not delete the GitHub token to restore restrictions; the mode flag is the explicit switch.

The `universal-github-access-benchmark.yml` workflow runs for same-repository PRs that change the adapter or benchmark. It records the effective repository permission flags, GitHub rate-limit snapshot, recursive tree discovery, workflow/source inspection, candidate-bound Actions run/job inspection, and a temporary branch plus draft-PR write/read/close/delete round-trip. The temporary PR is never merged; its branch is deleted during cleanup. The artifact records operation-level latency and permission failures but never stores token values. If a configured service PAT is present, it is used; otherwise the benchmark falls back to the repository-scoped Actions `GITHUB_TOKEN` and reports that source. A failure with `GITHUB_TOKEN` indicates a token permission or repository Actions setting boundary, not automatically a code defect.

## Security boundaries

Never expose the maintenance admin token or GitHub token to browser JavaScript, user prompts, model context, API responses, or logs. Send the admin token only from a trusted operator client over HTTPS. Keep production credentials out of inspected source. Protected project documents, environment files, hidden files, workflow definitions, deployment files, and non-Python files are intentionally outside the current allowlist.

An HTTP 503 from these endpoints means the feature is not configured or the free-tier writer is unavailable; it is not a reason to disable the safety gates.
