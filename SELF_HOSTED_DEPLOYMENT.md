# AI Infinity — Self-Hosted Production

This is the canonical deployment path for the production Creator Studio.

## Architecture

- **web**: FastAPI + canonical Creator Studio UI
- **worker**: dedicated durable production worker
- **persistent volume**: projects, SQLite queue/state, checkpoints and local artifacts
- **Caddy**: public HTTP/HTTPS gateway with automatic TLS for a real domain
- **optional object storage**: any S3-compatible endpoint for a second durable media copy

The application does not require a hosted application platform. The production stack is standard Docker Compose and can run on a normal Linux server.

## Server

Use a Linux server with Docker Engine and the Compose plugin. For sustained video generation, choose enough RAM/CPU for FFmpeg and concurrent provider calls; the supplied runtime intentionally defaults to one heavy production job for bounded resource use.

Point your domain's DNS A/AAAA record at the server.

## Start

```bash
cp .env.production.example .env
# edit AI_INFINITY_DOMAIN and optional credentials
docker compose -f docker-compose.production.yml up -d --build
```

The public application is then served through Caddy on ports 80/443.

## Verify

```bash
docker compose -f docker-compose.production.yml ps
docker compose -f docker-compose.production.yml logs --tail=200 web worker caddy
```

The API health contract is:

```
/infinity/3708/health
```

The production acceptance test is:

```bash
python scripts/production_acceptance.py
```

It queues a real 30-second production command, waits for the dedicated worker to render it, verifies the finished artifacts and timeline, and restarts the web process to verify persistence.

## Updates

```bash
git pull
docker compose -f docker-compose.production.yml up -d --build
```

Compose keeps the application definition deterministic and restartable across deployments.

## Backups

The durable application volume is `ai_infinity_data`. Back it up at the server level or use an S3-compatible secondary object store for finished media. Do not store secrets in Git.

## Provider configuration

The core application remains usable without proprietary provider credentials wherever local/open fallbacks exist. A provider-dependent capability is reported as unavailable rather than being presented as successfully connected.

External publishing is only reported as successful after the destination confirms the operation.
