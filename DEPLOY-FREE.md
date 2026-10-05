# AI Infinity — Free public deployment

The repository is now a standalone Docker application. The canonical entrypoint is `main.py`; the image launches `uvicorn main:app` directly.

## Recommended no-card public host

Use blitz.cloud for the free public website path. Their current free plan supports public Docker/GitHub apps, HTTPS, up to 5 apps, 512 MB reserved memory and 10 GB storage, with no paid subscription required to start. Free apps sleep when idle and wake on the next visit.

1. Open https://dashboard.blitz.cloud and create a free account.
2. Choose **Host something new** → **My own code**.
3. Paste: https://github.com/aurlooij-afk/AI-INFINITY
4. Select branch `main`.
5. Give the app a short subdomain name such as `ai-infinity`.
6. Choose **Build it and put it online**.
7. Open the HTTPS address Blitz assigns to the app.

The repository already contains the Dockerfile, port 10000 configuration, runtime healthcheck and final 3800 UI, so no Render configuration is required.

## Important operating limit

AI Infinity itself has no paid tier, no credit system and no mandatory paid AI API. Hosting is a separate resource. A free host can impose CPU, memory, storage, sleep or traffic limits; source code cannot honestly turn those into unlimited compute.

For heavier video generation, a larger always-free compute option is Oracle Cloud's Always Free tier. Oracle currently documents up to 2 OCPUs and 12 GB RAM of Ampere A1 Always Free compute in the home region, but account signup/verification and regional capacity still apply.