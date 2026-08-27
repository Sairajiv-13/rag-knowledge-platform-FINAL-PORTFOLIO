# rag-knowledge-platform

![Python](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-90%20passing-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-81%25-brightgreen)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-336791)
![License](https://img.shields.io/badge/license-MIT-green)

A multi-tenant RAG service. You upload documents, ask questions in plain English, and get answers that quote the exact passages they came from. Every answer shows its sources, and if the model tries to cite something that wasn't retrieved, that citation gets stripped before you see it.

Live API (interactive docs): https://rag-knowledge-platform-production.up.railway.app/docs

**Status:** functionally complete, verified for single-node work up to ~50k chunks. I've tried to be exact about what's actually tested versus what's built but unproven at scale. Verified: 90 tests at 81% line coverage, hybrid retrieval measured against a labeled eval set, retrieval latency measured on disclosed hardware, per-tenant quotas, live ingestion status, and per-session tenant login in the UI. Not validated at scale: the million-user tier described in SCALABILITY.md (no multi-node load test), the AWS Terraform (it passes fmt/validate in CI but I never applied it to a live account), and answer-quality numbers against a real LLM (the harness exists, it needs a live model to mean anything). The full list is in Limitations at the bottom. I'd rather you know the edges than find them.

## Why I built it

Every team eventually rebuilds the same thing: question-answering over their own documents, where the answer has to be traceable to a source. The demo loop is a weekend. The part that takes real work is everything around it. Keeping one tenant's data out of another's results. Ingestion that fails in a way you can recover from. Retrieval whose ranking you can actually explain. Usage metering that reports a real number or no number, never a guessed one. Citations that are checked, not trusted.

This repo is that shape, built end to end, with the limitations written down instead of hidden.

## What's in it

One Docker image runs both the API and the Celery worker. Postgres holds the relational data, the dense vectors (pgvector), and the keyword index (full-text search) in a single store, so there's one system to operate and one place where consistency lives. Providers (LLM, embeddings, reranker) sit behind interfaces you can swap. See `docs/adr/` for the decisions and the alternatives I rejected. There's an architecture diagram in the repo root.

## Quick start

```bash
docker compose up --build -d
docker compose run --rm api alembic upgrade head   # migrations (or: make migrate)
curl -s localhost:8000/healthz    # liveness  -> {"status":"ok"}
curl -s localhost:8000/readyz     # readiness -> checks postgres + redis
```

Interactive docs at http://localhost:8000/docs.

## Using the API

Every data-plane call needs a bearer token. Credentials are issued by an operator through the CLI, and the secret is shown exactly once.

```bash
python -m rag_platform.cli create-tenant --name Acme --slug acme
python -m rag_platform.cli create-credential --tenant acme --name laptop
# {"client_id": "rag_ci_...", "client_secret": "rag_cs_..."}

# 1. trade credentials for a 30-minute JWT
TOK=$(curl -s -X POST localhost:8000/v1/auth/token \
  -d "grant_type=client_credentials&client_id=$CID&client_secret=$CSEC" | jq -r .access_token)

# 2. upload a document (pdf/md/html). Returns 202 + status=pending; a worker ingests it.
#    Watch it finish without polling:
curl -X POST localhost:8000/v1/documents -H "Authorization: Bearer $TOK" -F "file=@guide.md"
curl -N localhost:8000/v1/documents/<id>/events -H "Authorization: Bearer $TOK"   # SSE status

# 3. ask a question, get a grounded answer with citations (add "stream": true for SSE)
curl -X POST localhost:8000/v1/answers -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" -d '{"query":"how are tenants isolated?"}'

# 4. retrieval with a per-source score breakdown
curl -X POST localhost:8000/v1/search -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" -d '{"query":"how do I tune recall?","top_n":5}'

# 5. token/cost metering
curl "localhost:8000/v1/usage?days=30" -H "Authorization: Bearer $TOK"
```

The SSE stream sends `citations` first (so a UI can render sources while tokens are still arriving), then `delta` events, then a final `done` with usage, model, and cost. Cost is only computed if you set the per-token price env vars. If you don't, usage rows record the token counts and leave cost null rather than inventing a dollar figure.

Operators can also skip HTTP entirely and ingest or search straight from the CLI (`rag_platform.cli ingest` / `search`), which is handy for offline dev and for loading a corpus.

## Retrieval

Retrieval runs dense search (pgvector, HNSW) and keyword search (Postgres full-text) and fuses the two with reciprocal-rank fusion. There's an optional cross-encoder reranker, off by default until the eval harness says the latency is worth it.

I didn't want to assume hybrid was better, so I measured it. On a 19-doc corpus with 38 labeled questions, hybrid ranks the right passage above dense-only and keyword-only at every k I looked at, and the expected fact lands in a retrieved chunk for 35 of 38 questions. Reproduce it with `python evals/run_eval.py`. The in-repo numbers use the deterministic fake embedder, so read the ordering, not the absolute magnitudes. Set `RAG_EMBEDDING_PROVIDER=local` for real semantic scores (pulls bge-small on first run). Details and the chunk-size sweep are in `evals/README.md`.

## Testing

90 tests, 81% line coverage, and the suite is the point. Unit tests cover the fiddly logic: chunker edge cases including pathological unpunctuated input, parser rejection paths, JWT and hashing failure modes, the RRF math, citation filtering, cost math, and storage-quota thresholds. Integration tests run the real app (httpx + lifespan) against a real Postgres that gets recreated and migrated by the actual Alembic migrations every session, with deterministic fake providers and eager Celery so the whole upload -> worker -> completed path runs in-process.

```bash
make test-unit          # no dependencies
make test-integration   # needs Postgres (docker compose up db)
make test-cov           # coverage report
```

What's covered end to end: auth failure modes and immediate revocation, tenant isolation at both the API and SQL level, duplicate/corrupt/oversized/unsupported uploads, per-tenant storage quota (413), batch upload with per-file failure isolation, SSE event ordering, usage rollups, and the worker's retry-exhaustion contract (a transient failure ends in a FAILED row, never a stuck PROCESSING one). Core paths — answering, retrieval, security, models — sit at 100%. The uncovered remainder is deliberate: operator CLI and the Anthropic/local-model network paths, which need keys or model downloads. Every number here is measured, never aspirational.

The same gates run in CI on every push: ruff, mypy, unit tests, `alembic upgrade` plus `alembic check` for migration/model drift, integration tests against a pgvector service, and a docker build.

## Measured performance

Real numbers, reproduced by `make benchmark` and `python evals/run_eval.py`. Hardware and caveats live in `benchmarks/RESULTS.md` — read them before quoting the numbers, because latency is hardware-dependent and I'd rather you cite mine than assume yours.

Hybrid-search latency excludes the LLM on purpose; it isolates the index path, which is the part that scales with corpus size. HNSW keeps p50 roughly flat as the corpus grows, which is the whole reason for the index. Answer-quality (correctness and faithfulness) has its own harness in `evals/answer_eval.py`, and it needs a real LLM to produce a meaningful score.

## Design decisions

The load-bearing ones, each with full reasoning and rejected alternatives in an ADR:

- **One database for everything.** pgvector + tsvector inside Postgres instead of a separate vector store and search engine. The cost is that `ts_rank_cd` isn't true BM25 and HNSW post-filters the tenant predicate. What I bought is one system to run and transactional consistency. (ADR 0001, 0003)
- **Shared tables + `tenant_id`, enforced in code.** Not RLS, not a database per tenant. Isolation becomes a discipline, so it's pinned by tests at the API and SQL level. In return I get sane migrations and connection pooling. (ADR 0002)
- **RRF over score blending.** Reranker is implemented but default-off until the harness proves the latency pays for itself. (ADR 0003)
- **Client-credentials OAuth2 with per-request revocation checks.** One primary-key read per request buys immediate revocation. (ADR 0004)
- **Upload bytes live in Postgres (10MB cap), not object storage.** The `documents` row is the single source of truth for job status; there's no Celery result backend. (ADR 0005)

## Security

OAuth2 client credentials, 30-minute HS256 JWTs. Secrets are stored as SHA-256 of 256-bit random strings — KDFs are for low-entropy passwords, not high-entropy machine credentials (ADR 0004). Revocation is immediate because liveness is checked on every request, not just at token issuance. `tenant_id` always comes from the token, never the request body, and a cross-tenant read returns 404 rather than 403, because 403 would confirm the id exists. Unknown client and wrong secret share one code path and one message, so there's no enumeration or timing oracle.

## What I'd do differently

Honest reflections, because after building something you can see the seams:

- **Upload bytes belong in object storage, not Postgres BYTEA.** Keeping payloads in the `documents` row (ADR 0005) kept the stack to one datastore and made re-embedding trivial, which was right at a 10MB cap. At real scale it puts large binaries in the same buffer cache as the hot retrieval path and bloats backups. I'd move raw bytes to S3 and keep a pointer. The empty ECS task role in the Terraform is already the seam for the bucket policy.
- **Redis broker should be SQS or something durable.** Redis is simple and fast, but a restart drops queued-but-unstarted messages. At-least-once with idempotent processing already tolerates redelivery; a durable queue would delete the message-loss failure mode instead of documenting it.
- **Keyword search is `ts_rank_cd`, not real BM25.** Staying inside Postgres FTS avoided running a search engine, and hybrid still beats dense in the eval, but there's no corpus-level IDF and the keyword-mode plateau is the price. For a keyword-heavy corpus I'd put a real BM25 engine behind the existing `keyword_search()` seam.
- **UI sessions are in-memory.** Real per-session tenant isolation, but the store doesn't survive a restart or span replicas. The seam is isolated so Redis-backed or signed stateless cookies is a contained change.
- **Citations point at a chunk, not a span.** Character offsets would let a UI highlight the exact supporting text and make the faithfulness eval exact instead of substring-based.

## Limitations

- Ingestion status is available by polling `GET /v1/documents/{id}` (it returns an `X-Poll-Interval` hint while non-terminal) or by subscribing to the SSE stream. Outbound webhooks are future work — they need a delivery subsystem (retries, signing, dead-lettering) the SSE stream doesn't.
- Per-tenant total storage is capped (`RAG_MAX_TENANT_STORAGE_BYTES`, default 500MB), enforced before the row is written. Time-windowed ingestion-rate quotas are future work.
- No per-credential scopes yet; any tenant credential has full tenant access.
- Postgres FTS lexes dotted identifiers oddly (`hnsw.ef_search` -> `hnsw.ef` + `search`), so keyword queries for the underscored part alone won't match. Documented, not hidden.
- Token counts are word-based estimates, not a real tokenizer. Chunk sizing is a budget; the 400-token default stays clear of bge's 512-token cap.
- PDF parsing is text-layer only (pypdf). Scanned PDFs without OCR are rejected with an actionable error; complex multi-column or table layouts extract imperfectly.
- Full-text search uses the English config only; other languages tokenize poorly until language handling is added.
- Tenant filtering happens after the HNSW scan (pgvector post-filter), so recall for very small tenants can degrade at large corpus sizes (ADR 0002).
- The AWS Terraform is fmt/validate-gated in CI but has never been applied to a live account. Cloud-deploy gaps are itemized in `terraform/README.md`; scale limits and their fixes in `SCALABILITY.md`.

## Stack

Python, FastAPI, PostgreSQL + pgvector, Celery/Redis, Next.js, Docker, Terraform.

## Running the hosted demo

The hosted instance runs the API against a small operator-loaded corpus so the answers endpoint works out of the box. If you deploy your own, run the worker (`./start-worker.sh`) alongside the API so the upload endpoint can process documents. Deploy runbook: `DEPLOYMENT.md`. It runs on trial credit plus an Anthropic key, so it's cheap for a handful of questions; turn it off when you're not demoing.
# rag-knowledge-platform

![Python](https://img.shields.io/badge/python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-336791)
![Tests](https://img.shields.io/badge/tests-90%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

Multi-tenant RAG-as-a-service: ingests documents (PDF / Markdown / HTML),
chunks and embeds them into pgvector, and serves grounded answers **with
citations** over a REST API.

 **🔗 Live demo:** https://rag-knowledge-platform-production.up.railway.app/docs

![Architecture](architecture-diagram.png)


> **Status: functionally complete; verified for single-node, ≤50k-chunk
> workloads.** What's *verified*: 90 tests at measured coverage, hybrid
> retrieval with a measured mode-comparison and chunk-size sweep
> ([evals/](evals/)), hybrid-search latency measured to ~60–90 ms p50 across
> 1k–50k chunks ([benchmarks/](benchmarks/)), per-tenant storage quotas,
> live ingestion status (SSE + poll hint), and per-session multi-tenant login
> in the UI. What's *designed but not validated at scale*: the 1M-user tier in
> [SCALABILITY.md](SCALABILITY.md) (no multi-node load test was run), the AWS
> Terraform (fmt/validate-gated in CI but never `apply`-ed to a live account),
> and real-model answer-quality numbers (harness exists; needs a live LLM).
> Everything not yet done is listed in *Limitations / future work* — named,
> not hidden. See also *What I'd do differently* below.

## Why this project exists

Teams keep rebuilding the same thing: question-answering over their own
documents, where the answer must be traceable to a source. The interesting
engineering isn't the demo loop (that's a weekend) — it's everything around
it: per-tenant isolation you can test, ingestion that fails loudly and
recoverably, retrieval whose ranking you can explain score-by-score, metering
that never invents a cost, and citations that are parsed and verified rather
than trusted. This repo is that service shape, built end-to-end and verified
at every stage — with its limitations written down instead of papered over.

## System architecture

```mermaid
flowchart LR
    client["API client"] -- "OAuth2 client credentials (ADR 0004)" --> api["FastAPI (api)"]
    api -- "hybrid search:<br/>HNSW + tsvector (ADR 0003)" --> pg[("PostgreSQL<br/>+ pgvector")]
    api -- "enqueue ingest (ADR 0005)" --> redis[("Redis broker")]
    redis --> worker["Celery worker"]
    worker -- "parse -> chunk -> embed -> store" --> pg
    api -- "grounded answer + citations" --> llm["LLM provider<br/>(Anthropic | fake)"]
    api --> emb["Embedding provider<br/>(local bge | fake)"]
    worker --> emb
```

One image serves `api` and `worker`; Postgres holds relational data, dense
vectors, and keyword search in a single store; providers sit behind swappable
interfaces (ADR 0001).

## Quick start

```bash
docker compose up --build -d
docker compose run --rm api alembic upgrade head   # apply migrations (or: make migrate)
curl -s localhost:8000/healthz   # liveness -> {"status":"ok"}
curl -s localhost:8000/readyz    # readiness -> checks postgres + redis
```

OpenAPI docs: http://localhost:8000/docs

## Layout

```
src/rag_platform/
  config.py       # pydantic-settings, env-driven, no hardcoded secrets
  logging.py      # structured JSON logging (structlog), incl. uvicorn logs
  exceptions.py   # application exception hierarchy root
  db.py           # async engine + session factory
  models.py       # ORM models (source of truth for the schema)
  main.py         # app factory + lifespan + domain error handler
  cli.py          # create-tenant / ingest / search without HTTP in the way
  api/            # deps (DI accessors) and routes
  ingestion/      # parsers (pdf/md/html -> Blocks) + sentence-packing chunker
  llm/            # provider interfaces + anthropic/local/fake impls (ADR 0001)
  retrieval/      # tenant-scoped queries + RRF hybrid + rerank (ADR 0003)
  services/       # ingestion orchestration (parse->chunk->embed->store)
  worker/         # celery app + ingest task (retries, idempotent, ADR 0005)
migrations/       # alembic env + hand-written, checked migrations
docs/adr/         # architecture decision records
```

## API walkthrough

All data-plane calls need a bearer token (ADR 0004). Credentials are issued by
an operator via the CLI; the secret is shown exactly once.

```bash
python -m rag_platform.cli create-credential --tenant acme --name laptop
# {"client_id": "rag_ci_...", "client_secret": "rag_cs_..."}

# 1. exchange credentials for a 30-min JWT
TOK=$(curl -s -X POST localhost:8000/v1/auth/token \
  -d "grant_type=client_credentials&client_id=$CID&client_secret=$CSEC" | jq -r .access_token)

# 2. upload a document (pdf/md/html, 10MB cap) -> 202 + status=pending;
#    a Celery worker ingests it. Watch it complete without polling:
curl -X POST localhost:8000/v1/documents -H "Authorization: Bearer $TOK" -F "file=@guide.md"
curl localhost:8000/v1/documents/<id> -H "Authorization: Bearer $TOK"   # status (+ X-Poll-Interval header while working)
curl -N localhost:8000/v1/documents/<id>/events -H "Authorization: Bearer $TOK"  # SSE status stream

# bulk upload: one call, per-file result (207), each processed independently
curl -X POST localhost:8000/v1/documents/batch -H "Authorization: Bearer $TOK" \
  -F "files=@a.md" -F "files=@b.md" -F "files=@c.md"

# 3. list / inspect / delete
curl localhost:8000/v1/documents -H "Authorization: Bearer $TOK"
curl localhost:8000/v1/documents/<id> -H "Authorization: Bearer $TOK"

# 4. retrieval with per-source score breakdown
curl -X POST localhost:8000/v1/search -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" -d '{"query":"how do I tune recall?","top_n":5}'

# 5. grounded answer with citations (add "stream": true for SSE)
curl -X POST localhost:8000/v1/answers -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" -d '{"query":"how are tenants isolated?"}'

# 6. token/cost metering
curl "localhost:8000/v1/usage?days=30" -H "Authorization: Bearer $TOK"
```

SSE stream shape: `event: citations` (sources, sent first so a UI can render
them while tokens arrive) → `event: delta` (text) → `event: done` (usage, model,
cost). Costs are computed only if `RAG_PRICE_INPUT_PER_MTOK` /
`RAG_PRICE_OUTPUT_PER_MTOK` are set — otherwise usage rows record tokens and a
NULL cost rather than an invented number. Interactive docs at `/docs`.

## Web UI

`frontend/` is a Next.js 15 + Tailwind console: streaming chat with inline
citation markers and a sources panel, document upload/management with live
status, and explicit loading/error/empty states. Users **log in** with a
tenant credential; the server holds it in a per-session store (httpOnly
cookie) and proxies requests as that session's tenant, so two sessions act as
two tenants. Architecture and honest limits (in-memory sessions; tenant-level,
not per-user, auth) in [frontend/README.md](frontend/README.md).

```bash
python -m rag_platform.cli create-tenant --name Acme --slug acme
python -m rag_platform.cli create-credential --tenant acme --name web-ui
docker compose up -d web        # -> http://localhost:3000 -> log in
```

## Observability & limits

- Every request carries an **X-Request-ID** (honored if supplied, generated
  otherwise), bound into every JSON log line for that request, and echoed on
  the response.
- **Prometheus metrics** at `/metrics`: request rate/latency/status by route
  *template* (never raw paths — cardinality protection), and LLM tokens by
  model/direction. A provisioned **Grafana dashboard** ships in
  `observability/`: `docker compose --profile observability up -d`, then
  http://localhost:3001 (rate, p95, 5xx ratio, tokens/min; port 3000 is the
  web UI).
- **OpenTelemetry tracing** is opt-in: `RAG_OTEL_EXPORTER=console` prints
  spans to stdout (how it's verified in dev); `otlp` ships them to
  `RAG_OTEL_ENDPOINT`.
- **Rate limiting** (Redis fixed window): per-*tenant* on the data plane
  (more credentials must not mean more quota), a tighter per-client_id limit
  on the token endpoint checked before any secret work, `429 + Retry-After`,
  and fail-open if Redis is down — a limiter shouldn't be a second point of
  failure. Trade-offs (window-edge bursts) documented in `ratelimit.py`.
- The **LLM client retries** transient failures (429/5xx/connection) with the
  SDK's exponential backoff, `RAG_LLM_MAX_RETRIES` deep.

## Measured performance

Real numbers, reproduced by `make benchmark` and `python evals/run_eval.py`.
Hardware and caveats: [benchmarks/RESULTS.md](benchmarks/RESULTS.md).

**Retrieval quality** (19-doc corpus, 38 labeled questions; `fake` embedder,
so read the *ordering*, not absolutes — run `RAG_EMBEDDING_PROVIDER=local` for
semantic magnitudes):

| mode    | hit@1 | hit@3 | hit@5 | MRR@5 |
|---------|-------|-------|-------|-------|
| keyword | 0.395 | 0.421 | 0.421 | 0.408 |
| dense   | 0.474 | 0.789 | 0.816 | 0.624 |
| hybrid  | 0.553 | 0.816 | 0.842 | 0.681 |

Hybrid beats dense beats keyword at every k — the measured justification for
the hybrid default (ADR 0003), not an assertion. The expected fact is in a
retrieved chunk for 35/38 questions (0.921), so retrieval rarely bottlenecks
the answer. A **chunk-size sweep** (`--chunk-sweep 200 400 800`) shows the
precision/context trade-off directly: 200-token chunks win hit@1 (more
precise), 400 wins hit@3/5 (more context) — full table in
[evals/README.md](evals/README.md).

**Hybrid-search latency** (real Postgres+pgvector, LLM excluded, 1 vCPU):

| corpus (chunks) | p50 (ms) | p95 (ms) | p99 (ms) |
|---:|---:|---:|---:|
| 1,000 | 63.7 | 69.9 | 72.3 |
| 10,000 | 88.9 | 135.6 | 145.2 |
| 50,000 | 58.2 | 68.2 | 73.2 |

~60–90 ms p50 at single concurrency, and HNSW keeps it flat as the corpus
grows 50× (full concurrency matrix in RESULTS.md). Answer-quality
(correctness + faithfulness) has its own harness, `evals/answer_eval.py`,
which needs a real LLM to be meaningful.

## Design trade-offs

The load-bearing decisions, each with its full reasoning and rejected
alternatives in an ADR:

- **One database for everything** — pgvector + tsvector in Postgres instead of
  a dedicated vector store and a search engine. Cost: `ts_rank_cd` is not true
  BM25, and HNSW post-filters tenant predicates. Bought: one system to
  operate, transactional consistency. ([ADR 0001](docs/adr/0001-stack-and-llm-provider.md), [0003](docs/adr/0003-hybrid-retrieval.md))
- **Shared tables + tenant_id, enforced in code** over RLS or DB-per-tenant.
  Cost: isolation is a discipline, so it's pinned by tests at the API and SQL
  level. Bought: sane migrations and pooling. ([ADR 0002](docs/adr/0002-tenant-isolation.md))
- **RRF over score blending**; **rerank implemented but default-off** until
  the eval harness proves the latency is paid for. ([ADR 0003](docs/adr/0003-hybrid-retrieval.md))
- **Client-credentials OAuth2 with per-request revocation checks** — one PK
  read per request buys immediate revocation. ([ADR 0004](docs/adr/0004-auth-model.md))
- **Upload bytes live in Postgres** (10MB cap) rather than adding object
  storage; **the documents row is the only job-status truth** (no Celery
  result backend). ([ADR 0005](docs/adr/0005-async-ingestion.md))

## Security notes

- OAuth2 client credentials; 30-min HS256 JWTs; secrets stored as SHA-256 of
  256-bit random strings (KDFs are for low-entropy passwords — ADR 0004).
- Revocation (`revoke-credential`) is immediate: credential liveness is checked
  on every request, not just at token issuance.
- tenant_id always comes from the token, never from the request; cross-tenant
  reads return 404 (403 would confirm the id exists).
- Token endpoint: unknown client_id and wrong secret share one code path and
  one message (no enumeration/timing oracle).
- Not yet here: per-credential scopes, rate limiting (stage 8), HTTP admin
  plane (deliberately CLI-only until a real IdP is in scope).

## Try it: ingest and search from the CLI

```bash
# offline/deterministic providers — no API keys, no model downloads:
export RAG_DATABASE_URL=postgresql+asyncpg://rag:rag@localhost:5432/rag \
       RAG_REDIS_URL=redis://localhost:6379/0 \
       RAG_EMBEDDING_PROVIDER=fake RAG_RERANKER=fake

python -m rag_platform.cli create-tenant --name "Acme" --slug acme
python -m rag_platform.cli ingest --tenant acme examples/docs/*.md examples/docs/*.html examples/docs/*.pdf
python -m rag_platform.cli search --tenant acme --query "how are tenants isolated?"
python -m rag_platform.cli search --tenant acme --query "ef_search" --mode keyword
```

Unset `RAG_EMBEDDING_PROVIDER` (default `local`) for real semantic retrieval —
requires `pip install ".[local-inference]"`; bge-small (~130MB) downloads from
Hugging Face on first use. Results include the full score breakdown per chunk
(`dense_cosine_sim`, `keyword_ts_rank`, `rrf`, `rerank`) — see ADR 0003.

## Data model

```mermaid
erDiagram
    tenants ||--o{ documents : owns
    tenants ||--o{ chunks : "owns (denormalized for query hot path)"
    documents ||--o{ chunks : "split into"

    tenants {
        uuid id PK
        string name
        string slug UK
        timestamptz created_at
    }
    documents {
        uuid id PK
        uuid tenant_id FK
        string filename
        string source_type "pdf | markdown | html"
        string content_sha256 "unique per tenant (dedup)"
        string status "pending | processing | completed | failed"
        text error_message
        int chunk_count
        timestamptz created_at
        timestamptz updated_at
    }
    chunks {
        bigint id PK
        uuid document_id FK
        uuid tenant_id FK
        int chunk_index
        text content
        int token_count
        vector_384 embedding "HNSW index (cosine)"
        tsvector tsv "generated column, GIN index"
        jsonb meta
        timestamptz created_at
    }
```

Multi-tenancy is shared-tables + `tenant_id` on every row, enforced in the
repository layer — trade-offs and rejected alternatives (incl. RLS) in
[ADR 0002](docs/adr/0002-tenant-isolation.md).

### Migrations

```bash
make migrate                       # inside docker compose
alembic upgrade head               # local (needs .env)
alembic check                      # fail if models.py drifted from migrations
alembic upgrade head --sql         # review the SQL without applying it
```

## Infrastructure

`terraform/` provisions the AWS footprint (VPC, RDS Postgres 16, ElastiCache,
ECS Fargate api/worker/web, ALB, ECR, Secrets Manager — secret *values* never
touch Terraform state; RDS manages its own master password). The deploy
runbook with exact bootstrap commands and an honest cost estimate is
[terraform/README.md](terraform/README.md). What breaks at 100/10k/1M users
and what gets redesigned when: [SCALABILITY.md](SCALABILITY.md). Stated
plainly there and here: the HCL is fmt/validate-gated in CI but has not been
applied to a live account as part of this repo's development.

## Testing

90 tests: unit (chunker edge cases incl. pathological inputs, parser
rejection paths, JWT/hashing failure modes, RRF math against stubbed
repositories, citation filtering, cost math, storage-quota thresholds) and
integration — integration tests run the **real app** (httpx + lifespan)
against a **real Postgres** that is recreated and migrated by the actual
Alembic migrations each session, with deterministic fake providers and eager
Celery so the full upload→worker→completed path executes in-process. Covered
end-to-end: auth failure modes and immediate revocation, tenant isolation at
API and SQL level, duplicate/corrupt/oversize/unsupported uploads, per-tenant
storage quota (413), batch upload with per-file failure isolation, SSE event
ordering and the live document-status stream, usage rollups, and the worker's
retry-exhaustion contract (a transient failure ends in a FAILED row, never a
stuck PROCESSING one).

```bash
make test-unit           # no dependencies needed
make test-integration    # needs Postgres (docker compose up db); override RAG_TEST_DATABASE_URL if not localhost:5432
make test-cov
```

The same gates run in CI on every push (`.github/workflows/ci.yml`):
ruff → mypy → unit tests → `alembic upgrade` + `alembic check` (migration/model
drift fails the build) → integration tests against a pgvector service →
docker build. Deployment runbook: [DEPLOYMENT.md](DEPLOYMENT.md).

Measured coverage (`make test-cov`): **81% line coverage**, 90 tests —
observability, rate limiting (real 429s against Redis), storage quotas, batch
upload, SSE status streaming, and request-ID behavior included.
The uncovered remainder is deliberate: `cli.py` (operator tooling, exercised
manually each stage), and the Anthropic/local-model provider internals, which
need API keys or model downloads — their construction guards are covered, the
network paths are not. Numbers here are only ever measured ones.

## What I'd do differently

Honest reflections after building it — the trade-offs I'd revisit, and why:

- **Upload bytes in Postgres BYTEA → object storage sooner.** Storing raw
  payloads in the `documents` table (ADR 0005) kept the stack to one datastore
  and made re-embedding trivial, which was the right call at a 10MB cap. But it
  puts large binary in the same buffer cache as the hot retrieval path, and it
  bloats backups. At any real scale I'd move raw bytes to S3 and keep only a
  pointer in Postgres — the empty ECS task role in the Terraform is already the
  seam where the bucket policy would go.
- **Redis broker → SQS (or at least a durable broker).** Redis as the Celery
  broker is simple and fast, but a Redis restart drops queued-but-unstarted
  messages (documents stay PENDING, re-enqueueable — but still, message loss is
  real). At-least-once with idempotent processing already tolerates
  redelivery; a durable queue would *delete* the message-loss failure mode
  instead of merely documenting it.
- **Keyword search: `ts_rank_cd`, not true BM25.** Staying inside Postgres FTS
  (ADR 0003) avoided operating a search engine, and the eval harness shows
  hybrid still beats dense — but `ts_rank_cd` lacks corpus-level IDF, and the
  measured keyword-mode plateau is the cost. For a keyword-heavy corpus I'd put
  a real BM25 engine behind the existing `keyword_search()` seam.
- **In-memory UI sessions → Redis-backed.** The per-session login (Stage 5)
  gives real session-level tenant isolation, but the store is in-process:
  sessions don't survive a restart or span replicas. The seam (`session.ts`)
  is deliberately isolated so swapping in Redis or signed stateless cookies is
  a contained change.
- **Structured citations with offsets.** Citations currently point at a chunk;
  character start/end offsets would let a UI highlight the exact supporting
  span, and make the faithfulness eval exact rather than substring-based.

## Limitations / future work

- Ingestion status is available three ways: poll `GET /v1/documents/{id}`
  (which returns an `X-Poll-Interval` hint header while non-terminal), or
  subscribe to the `GET /v1/documents/{id}/events` SSE stream for pushed
  status transitions. Outbound webhooks (delivering status to a caller's URL)
  are future work — a design note, not built, because they need a delivery
  subsystem (retries, signing, dead-lettering) that the SSE stream doesn't.
- Upload payloads live in Postgres BYTEA (deliberate at a 10MB cap — ADR
  0005); object storage is the move if caps grow. Per-tenant total storage is
  capped (`RAG_MAX_TENANT_STORAGE_BYTES`, default 500MB, enforced on the API
  upload path before the row is written); set 0 to disable for single-tenant
  self-hosting. Time-windowed *ingestion-rate* quotas are future work.
- No per-credential scopes; any tenant credential has full tenant access.
- Anthropic and local-model provider internals are not covered by automated
  tests (keys/downloads); they're exercised only in real deployments.
- Postgres FTS lexes dotted identifiers unintuitively (`hnsw.ef_search` →
  `hnsw.ef` + `search`), so keyword queries for the underscored part alone
  won't match — a documented tokenizer behavior, not a bug we hide.
- Token counts are word-based estimates (see `ingestion/chunking.py`), not a
  real tokenizer; chunk sizing is a budget, and inputs past bge's 512-token
  cap would be silently truncated by the encoder — the 400-token default
  target keeps clear of that.
- Keyword ranking is Postgres `ts_rank_cd`, not true BM25 (no corpus IDF) —
  trade-off documented in ADR 0003.
- Full-text search uses the `english` tsvector config only; non-English
  corpora will tokenize poorly until language handling is added.
- PDF parsing is text-layer only (pypdf): scanned PDFs without OCR are
  rejected with an actionable error, and complex layouts (multi-column,
  tables) extract imperfectly.
- The `fake` providers are deterministic plumbing for tests/offline dev, not
  semantic — don't judge retrieval quality on them.
- Tenant filtering happens after the HNSW ANN scan (pgvector post-filter);
  recall for small tenants can degrade at large corpus sizes — see ADR 0002.
- `CREATE EXTENSION vector` in migration 0001 assumes the role may install
  extensions (true in compose and for the RDS master user the Terraform
  stack uses).
- Cloud deploy gaps (TLS-optional default, no autoscaling, master DB user as
  app user, CloudWatch-only metrics) are itemized in terraform/README.md;
  scale limits and their fixes in SCALABILITY.md.
