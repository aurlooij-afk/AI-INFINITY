AI Infinity TARGET-2050.2801
Build: UNIVERSAL-WORK-EARNINGS-FINANCE-MEDIA-OPERATING-FABRIC Previous contract: TARGET-2050.2800
What this release adds
Unified responsive workspace: home, chat, command, plans, missions, work, create/media, memory, connections, settings, activity, voice controls, PWA support.
Work/earnings operating layer: specialist profiles, opportunity ingestion, matching, proposal drafting, projects, deliverables, invoices, payment recording, work summary, and explicit non-guaranteed earnings language.
Specialist tracks: freelancer, software engineer, writer, researcher, automation operator, and media producer. They are routing/work modes, not claims that external marketplaces or accounts are magically available.
Finance assistance: bookkeeping, budgets, cash-flow summary, and a simple run-rate forecast. It is informational planning, not individualized financial/tax/legal/investment advice.
Secure temporary earnings wallet: append-only auditable ledger, encrypted payout metadata when AI_INFINITY_WALLET_KEY is configured, idempotent mutations, operator-gated credit/debit/payout queue, reconciliation-friendly records. This is NOT regulated custody of real money; real funds remain with the external payment provider until a real payout connector is configured.
Media production: project planning, storyboards, captions/SRT, uploads, safe FFmpeg slideshow rendering, multiple delivery targets, asset hashing, and job records.
Performance/security: SQLite WAL, bounded payloads, response caching, thread pools for network/media work, mutation rate limiting, operator gate, secret redaction, path confinement, no arbitrary code, and no uncertain replay.
Backups: /api/backup exports application state while excluding operator/provider secrets; encrypted payout metadata remains ciphertext.
Deployment
The included Dockerfile is for Render's Docker runtime and installs every Python dependency explicitly, including httpx (which avoids the missing-httpx deployment failure seen in earlier builds).
Required/optional environment values
AI_INFINITY_OPERATOR_TOKEN  -> required for protected side-effect mutations and wallet operations. AI_INFINITY_WALLET_KEY      -> optional for encrypted payout metadata; use a strong stable secret and keep it in Render's secret environment. HF_TOKEN                    -> optional; enables the hosted Hugging Face model path. Without it, built-in fallback chat remains available. AI_INFINITY_HF_MODEL        -> defaults to openai/gpt-oss-120b:fastest. AI_INFINITY_OPPORTUNITY_FEEDS -> optional comma-separated public JSON feed URLs; leave empty for manual ingestion. AI_INFINITY_ACTION_HOST_ALLOWLIST -> optional existing real-world HTTP action allowlist from the preserved control plane. AI_INFINITY_PUBLIC_ORIGIN   -> public application URL.
Core checks
verify_2801.py compiles the application, compares the available baseline route set, imports the app, runs the internal self-test, starts a real Uvicorn server, and exercises health/UI/chat/work/media/finance/wallet/backup/forecast endpoints. The verified artifact reported TARGET-2050.2801 with passed=true and no missing legacy routes in the available baseline.
Important reality boundary
The project materials available for source inspection did not contain the exact deployed 2800 source bytes; they contained a later reported 2800 contract and an accessible cumulative source ending at the 2050.201 control-plane layer. Therefore this release preserves all routes present in the accessible source and implements the 2800-reported interface contract additively rather than falsely claiming byte-level inspection of an unavailable 2800 file.
Free-hosting durability note
The default Render setup uses /tmp/ai-infinity so it remains compatible with a free/ephemeral host. That local SQLite state can be lost when the host is replaced. /api/backup exists for portable exports; durable cross-restart cloud persistence would require a persistent external database service.
Real earnings note
The work engine can organize, score, prepare, and track opportunities, but it cannot guarantee income. Actual earnings require genuine client/work sources, accepted proposals, completed deliverables, and supported payment accounts/connectors. No fake earnings are reported.
