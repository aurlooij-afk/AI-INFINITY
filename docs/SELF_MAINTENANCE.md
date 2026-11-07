# AI Infinity GitHub execution and self-maintenance

AI Infinity includes a GitHub execution adapter connected to its existing self-maintenance engine. It has three access modes: restricted (default), repository_full (all permitted text/code/configuration paths in the configured repository), and github_full (GitHub REST and GraphQL resources permitted by the configured credential, without an application-level single-repository path filter). In github_full mode, higher-level discovery, inspection, CI review and draft-PR self-maintenance can also target any owner/repository reachable by the token. The mode does not manufacture permissions: GitHub token grants, repository selection, organization policies, SSO and repository rules determine effective access. It is not valid to claim account-wide or unrestricted access until measured.

## Required configuration

Configure these values as service secrets/environment variables in the deployment platform. Do not commit any value to this repository.

| Variable | Required value |
|---|---|
| `AI_INFINITY_SELF_MAINTENANCE_ENABLED` | `true` |
| `AI_INFINITY_MAINTENANCE_REPOSITORY` | `aurlooij-afk/AI-INFINITY` |
| `AI_INFINITY_MAINTENANCE_BASE_BRANCH` | `main` |
| `AI_INFINITY_MAINTENANCE_GITHUB_TOKEN` | Owner-provisioned GitHub credential with the repository and endpoint permissions needed for the intended work. |
| `AI_INFINITY_MAINTENANCE_ADMIN_TOKEN` | Independently generated high-entropy secret. Never reuse a session cookie, provider key, or GitHub token. |
| `AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE` | `github_full` for GitHub-wide REST/GraphQL execution; `repository_full` or `restricted` for narrower scope. |

In `restricted` mode, paths are limited to root Python modules, `ai_infinity/`, `scripts/`, `tests/` and the two Creator Studio HTML UI entrypoints. In `repository_full` mode, the engine can inspect/edit text, code, workflows, configuration and documentation across the configured repository, including `.github/workflows/` and `.ai-infinity/`. The self-maintenance source inspection/edit path excludes `.env` and credential/key stores, `.git`, binary assets, and the two protected documents named above. Full source mode permits text/code/configuration files in `.github/` and `.ai-infinity/`; it does not expose secret stores as model-editable source. The generic API adapter is repository-scoped in restricted/repository_full modes. In github_full it can address any GitHub API resource allowed by the configured credential, while requests remain pinned to api.github.com. Large source files are read server-side up to 1 MB per file and 2 MB aggregate; model context remains capped at 80 KB per request. Large modules use bounded, line-numbered, SHA-256-pinned function/class windows. Proposed changes are syntax/secret-checked and still require regression tests; code maintenance produces a draft PR, not an automatic merge.

The code writer also requires the existing Gemini Free Tier configuration: `GEMINI_API_KEY` (or `AI_INFINITY_GEMINI_API_KEY` / `GOOGLE_AI_STUDIO_API_KEY`) and an affirmative explicit data-use acknowledgement via `AI_INFINITY_GEMINI_FREE_TIER_AND_DATA_USE_ACK` (or one of the supported aliases). The model is pinned to `gemini-3.1-flash-lite`; no paid-model fallback is used.

## API

All endpoints require the `X-AI-Infinity-Maintenance-Token` header. Creator Studio exposes the operator console under **System → Self-maintenance**. Its password field is transient, the browser refuses to send the token over non-HTTPS production origins, and the token is not stored in local/session storage. The console supports readiness, redacted live-runtime inspection, repository discovery, bounded inspection, autonomous draft improvement, and exact-commit CI review.

- `GET /infinity/studio/self-maintenance/status` — reports configuration and writer readiness without returning secrets.
- `GET /infinity/studio/self-maintenance/runtime` — reports redacted live application version/build, the running Git SHA when the host exposes it, required route registration, media-tool availability, project-state and asset-storage configuration. Provider configuration is explicitly not treated as proof of durable writes or read-back.
- `POST /infinity/studio/self-maintenance/discover` — JSON body: `{"goal":"Locate the long-form narration planner and its regression tests.","limit":20}`. Returns allowlisted Python source candidates and matching file SHAs without returning source bodies. A truncated Git tree is reported explicitly.
- `POST /infinity/studio/self-maintenance/ci` — JSON body: `{"commit_sha":"<full 40-character SHA>","limit":10}`. Reads run/job/step outcomes for the exact commit without returning raw logs. The token needs Actions: Read.
- `POST /infinity/studio/self-maintenance/github/execute` — JSON body: `{"method":"GET","path":"/repos/aurlooij-afk/AI-INFINITY/actions/runs?per_page=5"}`. Executes GitHub REST operations and GraphQL through POST /graphql, with timing and redacted results. Supported REST methods are GET, POST, PUT, PATCH and DELETE. In github_full mode, the caller may target any GitHub API resource the credential permits. Contents API paths still enforce the protected-document and credential-path exclusions.
- `POST /infinity/studio/self-maintenance/inspect` — JSON body: `{"goal":"Locate the long-form narration planner and provider repair path.","paths":["studio_ultimate.py","tests/test_longform_production_planner.py"]}`.
- `POST /infinity/studio/self-maintenance/improve` — JSON body: `{"goal":"Repair the verified long-form narration provider path and add regression tests.","paths":["studio_ultimate.py","tests/test_longform_production_planner.py"]}`.

### Canonical command entry for GitHub

The same GitHub adapter is callable through the existing natural-language command dispatcher at `POST /infinity/canonical/execute`. This keeps GitHub operations on the shared command path instead of requiring a separate GitHub executor. Requests require both the normal AI Infinity user session and the independent `X-AI-Infinity-Maintenance-Token` operator header. Explicit REST command text such as `Run GitHub REST GET /user/repos?per_page=100` is parsed into an API request; structured payloads are also supported.

REST example:

```json
{
  "command": "Inspect the authenticated GitHub user",
  "task_type": "github_api",
  "github_method": "GET",
  "github_path": "/user"
}
```

Cross-repository example (available only in `github_full`):

```json
{
  "command": "List repositories accessible to the owner token",
  "task_type": "github_api",
  "github_method": "GET",
  "github_path": "/user/repos?per_page=100&visibility=all"
}
```

GraphQL example:

```json
{
  "command": "Read the authenticated GitHub viewer",
  "task_type": "github_graphql",
  "github_graphql_query": "query { viewer { login } }",
  "github_graphql_variables": {}
}
```

The canonical response includes the selected route, method/path, duration, redacted result and whether the operation was read-only or a write. The server-side GitHub token is never accepted from a request body or returned to the client.

## Execution and release policy

The module is disabled unless explicitly enabled and both GitHub and admin credentials are configured. In restricted mode, it accepts only allowlisted Python source and test paths. In repository_full/github_full modes, source inspection and edits can cover permitted text/code/configuration paths. It reads source files up to 1 MB (8 files / 2 MB aggregate), and limits combined model context to 80 KB. Small files are inspected whole; large modules are inspected through goal-matched function/class windows with exact line ranges and SHA-256 hashes. Large-file edits must replace only one of those exact hash-pinned windows; the server rechecks the source hash, applies the edits to the complete file, parses the complete resulting Python module, rejects credential assignments under common key/token names and recognizable provider-token formats, and requires at least one regression-test file in every change set.

The intended sequence is **discover → inspect → plan → execute → verify → review**. Discovery reads the full recursive Git tree and can include hidden workflow/project metadata paths in repository_full and github_full modes. In github_full mode, discover, inspect, ci and improve requests accept optional repository and base_branch fields to target another authorized repository. The generic executor can call any GitHub REST/GraphQL API route the supplied token is authorized to use, including user, organization, repository, branch, commit, pull request, issue, Actions, checks, statuses, releases, tags and repository settings. Each call returns its method, path, duration and result status; credentials and secret-bearing file content are redacted. The self-maintenance writer remains a separate controlled path: it creates a temporary branch and a **draft pull request**, never a direct `main` write, merge or deployment. The benchmark uses a deterministic test writer only to exercise real GitHub discovery/read/write/PR/cleanup operations; its result measures GitHub execution, not model intelligence. Review the exact diff and CI evidence. CI success is not production certification.



## GitHub-wide performance mode

Set AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE to github_full on the AI Infinity deployment to remove the application-level single-repository restriction from the generic GitHub REST/GraphQL executor and cross-repository self-maintenance routes. Keep AI_INFINITY_MAINTENANCE_REPOSITORY as the default working repository. Restart/redeploy the service after changing environment variables.

The generic executor forwards supported REST and GraphQL requests to api.github.com. The configured GitHub credential remains the actual authority: a personal access token must include the desired repositories and endpoint permissions, and organization owners may restrict or require approval for token access. Classic personal access tokens can span the repositories available to their owner according to granted scopes and organization policy; fine-grained personal access tokens are limited to selected repositories and specific permissions. GitHub endpoint requirements vary, so a successful permission probe does not establish permission for every endpoint.

The universal-github-access-benchmark.yml workflow requires the dedicated AI_INFINITY_MAINTENANCE_GITHUB_TOKEN secret or explicit AI_INFINITY_GITHUB_TOKEN alias. It never falls back to GITHUB_TOKEN or another product connector. The benchmark reports the exact candidate SHA, authenticated account identity, repository permission metadata, a paginated `/user/repos` inventory (up to 100 pages of 100 entries, with explicit pagination-completeness status and a bounded sample in the report), authenticated GraphQL viewer query, optional first-page organization membership view, cross-repository read, temporary cross-repository branch creation/deletion, configured-repository source-to-draft-PR round-trip, operation latency and actual failures. It does not claim to exhaustively enumerate all resources or prove unrestricted access. If the owner token is absent, the workflow reports BLOCKED before attempting any GitHub API operation.

The cross-repository branch test creates no commit and never modifies the sampled repository default branch. The configured-repository self-maintenance probe creates a temporary draft PR, closes it and deletes its branch. Neither test merges or deploys. Cleanup failures remain visible in the evidence report.

To restore a narrower application-level mode, set AI_INFINITY_MAINTENANCE_GITHUB_ACCESS_MODE to repository_full or restricted and restart/redeploy.

## Security boundaries

Never expose the maintenance admin token or GitHub token to browser JavaScript, user prompts, model context, API responses, or logs. Send the admin token only from a trusted operator client over HTTPS. Keep production credentials out of inspected source. The two owner-protected project documents, `.env` files, credential/key stores, `.git` internals, and binary assets remain excluded from the self-maintenance source-edit path. Full modes can inspect and propose edits to text workflows, deployment configuration, documentation and non-Python source files.

An HTTP 503 from these endpoints means the feature is not configured or the free-tier writer is unavailable; it is not a reason to disable the safety gates.
