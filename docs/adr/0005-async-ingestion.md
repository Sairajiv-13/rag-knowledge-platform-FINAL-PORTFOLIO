# ADR 0005: Async ingestion — Celery on Redis, payload in Postgres, DB as the only status truth

Date: 2026-07-07 · Status: accepted

## Context
Parse -> chunk -> embed takes seconds to minutes (CPU-bound embedding); doing
it inside the upload request blocks a worker process, ties ingestion latency to
HTTP timeouts, and competes with query traffic for CPU.

## Decisions

**1. Celery with the existing Redis as broker, and NO result backend.**
The `documents` row (pending/processing/completed/failed + error_message) is
the single status source of truth; a Celery result store would be a second
copy that can disagree with it. Clients poll `GET /v1/documents/{id}`.

**2. Upload payload stored in `documents.raw_content` (BYTEA).**
The worker needs the bytes; options were object storage (S3/MinIO — a whole new
dependency), a shared volume (breaks multi-node), or the database. At a 10MB
upload cap, BYTEA is honest: transactional with the row, zero new infra, and it
makes re-embedding (an embedding-model migration) possible without re-uploads.
The column is deferred so metadata reads never drag megabytes. Object storage
is the documented move if caps grow.

**3. At-least-once delivery, made safe by idempotency.**
`acks_late=True` + `prefetch=1`: a worker killed mid-task gets the message
redelivered. `process()` tolerates that: COMPLETED rows no-op, and stale chunks
from a crashed attempt are deleted before insert.

**4. Retry policy encoded in the exception hierarchy.**
ParseError is permanent (a corrupt PDF never parses on retry): marked failed
immediately, no retry. Everything else is transient: exponential backoff
(10s/20s/40s), then a terminal FAILED mark on exhaustion — nothing is ever
left in PROCESSING forever. Gotcha learned the hard way (and now commented in
tasks.py): `celery.Task.retry(exc=...)` re-raises *exc*, not
MaxRetriesExceededError, when retries run out — so exhaustion is detected by
checking the counter, not by catching that exception.

**5. Engine-per-task with NullPool; embedder as a process singleton.**
asyncpg connections are bound to the event loop that created them, and each
`asyncio.run()` is a new loop — a shared engine fails across tasks. The
embedding model, by contrast, loads once per worker process.

## Consequences
- Uploads return 202 + PENDING; clients must poll (or, later, subscribe).
- Broker outage after the row exists marks the document failed with
  "could not enqueue" and returns 503 — no forever-PENDING mysteries.
- Postgres stores payloads: table size grows with uploads (bounded by cap ×
  documents); revisit as object storage at scale (SCALABILITY.md, stage 8).
