# AI Infinity — TARGET-2050.3603

This build is the 12-point closure of the previous 3601/3602 package. It preserves the historical routes and adds a final truthful closure layer rather than replacing the existing kernel.

## Fixed-price work

The earning workspace now treats only explicit fixed-price evidence as fixed-price work. Salary/hourly listings are rejected by the fixed-price classifier. The primary fixed-price public source is Open Bounty's public bounty API; Himalayas and Jobicy remain general public job sources and are never converted from salary data into fixed-price rewards.

## The 12 closures

1. Genius capabilities route: `/infinity/3601/genius/capabilities`.
2. Source validation/health: `/infinity/3603/sources` and `/infinity/3603/discover`.
3. Public activation identity is redacted; private activation is `/infinity/3603/activation/private` and requires `AI_INFINITY_OPERATOR_TOKEN`.
4. Persistence: local SQLite is durable only when its filesystem is durable; optional remote SQLite snapshots use `AI_INFINITY_SNAPSHOT_URL` + `AI_INFINITY_SNAPSHOT_TOKEN`. Free Render web services have ephemeral filesystems, so the build does not falsely claim durable storage there.
5. Bridge capability catalog: `/infinity/3603/bridge-capabilities` plus paired runtime capability reporting.
6. Model routing: Hugging Face, Gemini, Ollama and OpenAI-compatible adapters remain supported, with deterministic fallback when no external model is configured.
7. Application bridge: existing authorized application API adapter plus `/infinity/3603/apply/{opportunity_id}` browser-bridge mode.
8. Payment integration: authenticated payment-evidence webhook remains the only path that credits verified earnings.
9. Payout integration: verified-funds bound payout adapter and confirmation flow remain enforced.
10. Regression: `pytest -q test_ai_infinity_3603.py` plus `/infinity/3603/regression`.
11. UI state: the canonical Genius UI shows persistence, model, source-health and payout state without exposing owner identity.
12. Truthful readiness: `/infinity/3603/readiness` separates implementation/configuration/health instead of reporting external integrations as ready merely because code exists.

## Run

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 10000
```

Run the bridge separately when browser/device execution is authorized:

```bash
pip install -r bridge-requirements.txt
playwright install chromium
python ai_infinity_bridge.py --help
```

## Security boundary

No arbitrary shell/code execution is provided. Side effects require approval. External uncertain side effects are not automatically replayed. Secrets are accepted through environment variables and are not returned by status endpoints.

## Production truth

The software-side adapters are present, but actual external capabilities still require the corresponding real authorization/credentials. A deployment without those credentials remains intentionally in a blocked/fallback state rather than pretending it can submit applications, receive verified payments, or transfer money.
