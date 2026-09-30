# AI Infinity — TARGET-2050.3603.2

This is the completed operating-workspace build on top of the supplied cumulative AI Infinity kernel. It preserves the historical routes and adds a real outcome-oriented workspace instead of a decorative dashboard.

## What is genuinely executable
- Natural-language command workspace.
- Persistent chat, missions, plans, activity and production state.
- Public-source research with evidence retrieval and persistence.
- Fixed-price opportunity discovery with an explicit stated-reward rule. Hourly/salary listings are not converted into fixed-price rewards.
- Proposal/offer preparation and work tracking.
- Real application bridge adapters and paired browser/device bridge protocol.
- Verified-payment accounting and payout orchestration with hard truth boundaries.
- Truthful readiness, source-health, bridge-capability, persistence and regression endpoints.

## Fixed-price work
The primary fixed-price discovery feed is Open Bounty. Its public API documents open bounties and an exact `rewardUsdc` field; AI Infinity treats that explicit reward as fixed-price evidence.

## Important production truth
A free Render web service uses an ephemeral filesystem. This build therefore reports persistence as EPHEMERAL unless a durable filesystem or the optional HTTPS SQLite snapshot adapter is configured. It never counts prepared work as earnings. Actual application submission, payment verification and payout require the corresponding real authorization/credentials.

## Run
```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 10000
```

Run local tests:
```bash
pytest -q test_ai_infinity_3603.py
```

Browser bridge (only when authorized):
```bash
pip install -r bridge-requirements.txt
playwright install chromium
python ai_infinity_bridge.py --server https://YOUR-AI-INFINITY-HOST --kind browser --name my-browser --code YOUR_PAIR_CODE
```

## Core URLs
- `/genius` — canonical operating workspace
- `/infinity/3603/ui` — operating workspace UI
- `/health` — deployment health
- `/infinity/3603/readiness` — truthful readiness
- `/infinity/3603/sources` — live source catalog
- `/infinity/3603/regression` — regression suite
- `/infinity/3603/work` — fixed-price work state
- `/infinity/3603/command` — command execution API


## TARGET-2050.3603.2 hardening

- Consequential legacy approval/rejection/resume endpoints require `AI_INFINITY_OPERATOR_TOKEN` through the request header `X-AI-Infinity-Operator-Token` (or `Authorization: Bearer ...`).
- Fixed-price discovery preparation never prepares hourly/salary-only listings.
- Browser bridge capability advertisement matches its executable browser actions.
- Readiness reports discovery as ready only when at least one configured source has actually returned successfully.
- Model readiness recognizes Hugging Face, Gemini, Ollama, and authenticated OpenAI-compatible configuration.
- Render Blueprint declares supported model/application secrets without embedding values.

## Deployment persistence truth

Render Free web services have ephemeral filesystems and can spin down after inactivity. Therefore the default `/tmp/ai-infinity` SQLite database is intentionally reported as ephemeral. For durable production state, configure `AI_INFINITY_DB_PATH` on durable storage or configure the authenticated snapshot adapter. Render documents that persistent disks are unavailable on Free services and recommends a managed datastore for persistent relational state.


## TARGET-2050.3603.2 closure

The canonical `/genius` and `/infinity/3603/ui` workspace now exposes the principal operating functions through one responsive interface: command, chat, research, plans, fixed-price work discovery, task application, creation, missions, organization, economy, wallet, memory, activity, connections, authority/approvals, payout requests, and persistence snapshots.

Browser workspaces receive an anonymous isolated session cookie; the server does not use a shared `default` identity for the canonical UI. Consequential actions still require the operator token. The UI does not claim an application was submitted, money was earned, or a payout completed unless the corresponding external bridge provides evidence.

Historical duplicate path/method registrations are removed from the final FastAPI route surface while preserving the first registered behavior that was previously reachable. Regression coverage now checks the canonical session, UI action manifest, workspace endpoints, security gate, fixed-price rule, bridge catalog, and duplicate-route absence.

### Infrastructure truth

This application can start at zero paid dependencies, but a hosting platform's infrastructure limits cannot be removed by Python code. On Render Free, services can spin down after 15 minutes without inbound traffic and their local filesystem is ephemeral; persistent disks are unavailable on Free services. Therefore durable production state and an always-running background worker require an actually durable/always-on external deployment configuration. The application reports these conditions instead of hiding them.

## TARGET-2050.3604 — reality boundary closure

The remaining hosting boundary now has a real, user-owned path instead of a placeholder:

1. **External websites:** pair `ai_infinity_bridge.py` in browser mode on a trusted computer. The server queues allowlisted browser actions; the bridge performs them locally. Application success is recorded only when an authorized application API returns an external reference, or after a future explicit browser submission workflow reports a real result.
2. **Durable free-first state:** configure `AI_INFINITY_GITHUB_TOKEN` and `AI_INFINITY_GITHUB_REPO`. AI Infinity writes a consistent SQLite snapshot to that repository and can restore it when the local database is empty. Do not put the token in source code.
3. **Free periodic execution:** configure `AI_INFINITY_CRON_TOKEN` and enable the included `.github/workflows/ai-infinity-wakeup.yml`. GitHub Actions periodically calls `/infinity/3604/cron`, waking a sleeping Render service and running the fixed-price discovery cycle. This is periodic, not a guarantee of continuous compute.
4. **Real payments:** set `AI_INFINITY_PAYMENT_WEBHOOK_SECRET` and send authenticated payment evidence from the actual payment provider. AI Infinity never treats an offer, task, or application as money.
5. **Real payout:** set `AI_INFINITY_PAYOUT_SUBMIT_URL` and `AI_INFINITY_PAYOUT_API_KEY` for an actual authorized payout service. The rail must return an external payout reference before AI Infinity records a submission.
6. **Real AI model:** configure HF/Gemini/Ollama/OpenAI-compatible credentials if deterministic fallback is insufficient.

The new `/infinity/3604/reality` endpoint and **Reality** interface tab expose each gate and its exact next action. This makes the boundary operational and inspectable rather than hiding it.
