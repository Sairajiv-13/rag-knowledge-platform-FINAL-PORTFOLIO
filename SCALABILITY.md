# SCALABILITY

What breaks at 100 / 10k / 1M users, where the bottlenecks are, and what I'd
redesign. Written against the architecture as actually built (ADRs 0001–0005),
not an imaginary one.

**Epistemic status, stated first:** retrieval latency **has now been measured**
(see [benchmarks/RESULTS.md](benchmarks/RESULTS.md)) — hybrid search is ~60–90 ms
p50 at single concurrency across 1k–50k chunks on one vCPU, and HNSW keeps it
sub-linear in corpus size. What has *not* been measured: multi-node behaviour,
the 1M-user tier (that hardware wasn't available), real-model embedding
throughput, and buffer-cache pressure at 10M+ chunks. So the per-query costs
below are grounded in measurement; the large-tier projections remain reasoned
estimates, labeled as such, with the measurement plan in the last section.

## Assumptions (so the tiers mean something)

"Users" = end-users across all tenants. Assume a knowledge-base usage shape:
~10 questions/user/day concentrated in working hours (~×3 peak factor), ~50
documents/user ingested over time, ~30 chunks/document. That makes the tiers
roughly:

| Tier | Peak queries/sec | Total chunks | Ingestion |
|---|---|---|---|
| 100 users | « 1 | ~150k | trickle |
| 10k users | ~10 | ~15M | steady |
| 1M users | ~1,000 | ~1.5B | heavy, bursty |

If your shape differs (fewer, chattier users; enormous corpora), shift tiers
accordingly — the failure order below mostly holds.

## 100 users: nothing breaks; latency is the LLM's

One compose host (or the Terraform stack at defaults) is comfortably
over-provisioned. End-to-end answer latency is dominated by LLM generation
(seconds); retrieval over ~150k chunks with HNSW is a rounding error against
it — **measured at ~60–90 ms p50 for a hybrid query even at 50k chunks**
(benchmarks/RESULTS.md), and the LLM call is 1–2 orders of magnitude slower.
The real risks at this tier are operational, not load: Redis restart
dropping queued-but-unstarted ingestion messages (documents stay PENDING,
re-enqueueable — ADR 0005), and the single-process API meaning one CPU-heavy
query embedding can add tail latency to neighbors. Neither justifies
redesign; both are known.

## 10k users (~10 QPS peak, ~15M chunks): the first real cracks

In the order I'd expect them to appear:

1. **Tenant-filtered ANN recall degrades** (flagged since ADR 0002). HNSW
   scans candidates globally, then filters by tenant. A tenant owning 0.1% of
   15M chunks needs the scan to survive a 1000:1 filter; recall for small
   tenants quietly drops. Fixes, cheapest first: pgvector iterative scans
   (`hnsw.iterative_scan`, 0.8+), raising `ef_search` (latency for recall),
   partial indexes for the largest tenants, or partitioning chunks by tenant
   hash. This is the eval harness's job to detect — run it per-tenant-size
   band before believing any fix.
2. **One Postgres does five jobs**: relational data, vectors, keyword search,
   job-status truth, and raw upload bytes. The bytes go first — move
   `raw_content` to S3 (the empty ECS task role in Terraform is where the
   bucket policy lands); backups shrink, the buffer cache stops competing
   with retrieval. Then read/write separation: retrieval to read replicas;
   pgbouncer in front of the whole thing (asyncpg pools per process multiply
   fast at higher API counts).
3. **API processes hold an embedding model.** Every uvicorn replica pays
   ~1GB+ for bge-small and burns CPU embedding queries in-process. Split
   embedding into its own small service (or use an embeddings API): API
   replicas get cheap and stateless, and embedding capacity scales
   independently — it's also step one toward caching frequent-query
   embeddings in Redis.
4. **Redis as broker stops being cute.** At-least-once with idempotent
   processing already tolerates redelivery, but Redis restart still *loses*
   queued messages. Move the queue to SQS (Celery supports it) — durable,
   and it deletes a failure mode instead of documenting it.
5. **Smaller items:** prometheus_client needs multiprocess mode once
   gunicorn/multiple workers appear; the per-request credential-liveness read
   wants the Redis cache ADR 0004 reserved (with a pub/sub invalidation on
   revoke); fixed-window rate limiting's 2× boundary burst starts mattering
   at real limits — sliding window is a contained change in `ratelimit.py`.

## 1M users (~1,000 QPS peak, ~1.5B chunks): different system

At this tier the honest answer is that several ADRs get reversed, and that's
fine — they were priced for the previous tiers.

- **Retrieval becomes its own service and leaves vanilla Postgres.** A single
  pgvector instance does not serve 1.5B vectors at 1,000 QPS with per-tenant
  filtering. Options, in the order I'd evaluate: sharded pgvector (tenant →
  shard routing; keeps the operational model), Citus, or a dedicated vector
  store; keyword search finally justifies a real BM25 engine (OpenSearch or
  ParadeDB — the seam is `retrieval/repository.py`, unchanged callers, as
  ADR 0003 planned). Tenant→shard routing plus per-shard HNSW also *solves*
  the small-tenant recall problem structurally.
- **Cell-based tenancy.** Shared-everything multi-tenancy stops being a
  discipline problem and becomes a blast-radius problem. Group tenants into
  cells (own DB shard, own workers, shared control plane); a bad neighbor or
  a bad deploy hits one cell.
- **Ingestion becomes an event pipeline**: uploads land in S3, events drive
  parse/chunk/embed fleets (GPU embedding at this volume), with idempotency
  keys end-to-end. The documents table remains the status truth the API
  reads — that principle survives.
- **Metering leaves the request path.** A synchronous usage-row INSERT per
  answer at 1,000 QPS on the primary is self-inflicted load; emit usage
  events to a stream, aggregate into a warehouse, serve `/v1/usage` from
  rollups. Same "never invent a cost" rule, different plumbing.
- **What survives unchanged:** the provider interfaces, the citation
  contract, JWT auth shape (token verification is stateless and horizontal),
  SSE streaming (fan-out is per-connection; ALB idle-timeout already set),
  and the exception/retry taxonomy.

## The first five changes, if growth showed up tomorrow

1. `raw_content` → S3 (smallest change, biggest DB relief)
2. Broker → SQS (deletes the message-loss failure mode)
3. Embedding out of the API process + query-embedding cache
4. pgbouncer + read replica for retrieval
5. Real BM25 behind `keyword_search()` — *after* the eval harness proves the
   `ts_rank_cd` gap matters on real corpora

## How I'd replace this document with numbers

k6 or Locust against a seeded corpus (the eval harness's ingestion path
scales to generate one), measuring: p50/p95/p99 for `/v1/search` (retrieval
isolated from LLM variance) at 1/10/100 QPS across corpus sizes of 100k/1M/
10M chunks; recall\@5 per tenant-size band at each corpus size (quality and
latency degrade *together* or the numbers lie); ingestion throughput
docs/min/worker; and Postgres buffer-cache hit ratio as `raw_content` grows.
Pass/fail lines get set before the runs, not after.
