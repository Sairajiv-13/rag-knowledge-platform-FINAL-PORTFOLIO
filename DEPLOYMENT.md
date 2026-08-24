# Deployment

Two supported paths: single-host **Docker Compose** (below) and **AWS via
Terraform** — VPC, RDS Postgres 16, ElastiCache, ECS Fargate (api/worker/web),
ALB, ECR, Secrets Manager. The full cloud runbook, exact bootstrap commands,
cost honesty, and known gaps live in [terraform/README.md](terraform/README.md);
scaling behavior and redesign thresholds in [SCALABILITY.md](SCALABILITY.md).

## What runs

| Service  | Image                    | Role                                        |
|----------|--------------------------|---------------------------------------------|
| `api`    | built from `Dockerfile`  | FastAPI app (uvicorn), port 8000            |
| `worker` | same image               | Celery worker: parse → chunk → embed → store |
| `db`     | `pgvector/pgvector:pg16` | PostgreSQL + pgvector (data + FTS + vectors) |
| `redis`  | `redis:7-alpine`         | Celery broker + rate-limit counters          |
| `web`    | built from `frontend/`   | Next.js UI + BFF proxy (holds UI credential), port 3000 |

One image serves both `api` and `worker` — fewer builds, no drift between the
code that accepts an upload and the code that processes it.

## Configuration

All configuration is environment variables (see `.env.example`). The ones that
matter for a deployment:

| Variable | Required | Notes |
|---|---|---|
| `RAG_DATABASE_URL` | yes | `postgresql+asyncpg://...` |
| `RAG_REDIS_URL` | yes | broker + (stage 8) cache/rate-limit |
| `RAG_JWT_SECRET` | yes | generate: `python -c "import secrets;print(secrets.token_urlsafe(48))"`. Rotating it invalidates all live tokens (≤30 min pain by design). |
| `RAG_LLM_PROVIDER` | yes (`anthropic`) | `fake` exists for tests only |
| `RAG_ANTHROPIC_API_KEY` | if provider=anthropic | |
| `RAG_EMBEDDING_PROVIDER` | default `local` | downloads bge-small (~130MB) on first use |
| `RAG_RERANKER` | default `none` | `cross_encoder` after the eval harness justifies it |
| `RAG_PRICE_INPUT_PER_MTOK` / `RAG_PRICE_OUTPUT_PER_MTOK` | no | unset ⇒ usage rows record NULL cost, never an invented number |

Secrets: pass via environment/secret manager. Nothing in this repo has a
default secret that works anywhere; the compose file's dev JWT secret is
labelled as such and must be overridden.

## Deploying a version

Migrations are additive so far, so the safe order is **migrate, then roll**:

```bash
docker compose build
docker compose run --rm api alembic upgrade head   # against the live DB
docker compose up -d api worker
curl -fs localhost:8000/healthz && curl -fs localhost:8000/readyz
```

`readyz` returning 503 names the dependency that isn't reachable.

First-time setup on a fresh host additionally needs a tenant and credentials
(operator CLI, deliberately not HTTP — ADR 0004):

```bash
docker compose run --rm api python -m rag_platform.cli create-tenant --name "Acme" --slug acme
docker compose run --rm api python -m rag_platform.cli create-credential --tenant acme --name prod
# the client_secret is printed exactly once
```

## Operations

- **Logs** are one JSON object per line on stdout (api and worker both) —
  point your shipper at container stdout.
- **Scaling ingestion**: `docker compose up -d --scale worker=3`. Workers are
  CPU-bound (embedding); more workers than cores buys nothing. `prefetch=1`
  means tasks distribute evenly.
- **Revoking access**: `python -m rag_platform.cli revoke-credential
  --client-id ...` — takes effect on the next request, not the next token.
- **Backups**: standard `pg_dump`/base-backup practice. Note that backups
  include uploaded document bytes (`documents.raw_content`, ADR 0005) — treat
  dumps with the same sensitivity as the uploads themselves.
- **Failed ingestions** are queryable: `SELECT filename, error_message FROM
  documents WHERE status='failed'` — permanent failures carry the parse
  reason; exhausted retries say so.

The web UI needs its own tenant credential in `RAG_UI_CLIENT_ID` /
`RAG_UI_CLIENT_SECRET` (issue via the CLI). Anyone who can reach the UI acts
as that tenant — front it with user auth or network controls in production.

## Observability

- `/metrics` (Prometheus) on the API; dashboards:
  `docker compose --profile observability up -d` → Grafana on :3001
  (anonymous viewer enabled for local use only).
- Tracing: set `RAG_OTEL_EXPORTER=otlp` and `RAG_OTEL_ENDPOINT` to your
  collector. `console` prints spans to stdout for verification.
- Rate limits: `RAG_RATE_LIMIT_PER_MINUTE` (per tenant),
  `RAG_RATE_LIMIT_TOKEN_PER_MINUTE` (token endpoint, per client_id). The
  limiter fails open if Redis is down (logged).

## Known production gaps (deliberate, tracked)

- No TLS termination in this stack — put a reverse proxy (Caddy/nginx/ALB) in
  front; the API itself never sees secrets in URLs.
- Single Postgres, no HA story; single Redis, and a Redis restart drops
  queued-but-unstarted ingestion messages (documents stay PENDING and are
  re-enqueueable; a durable broker is a documented upgrade path).
- Secrets are plain env vars; stage 8's Terraform wires a secret store.
