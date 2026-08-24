# ADR 0006: Batch upload registers many, but processes each independently

Date: 2026-07-26 · Status: accepted

## Context

Bulk ingestion (hundreds of documents) over the single `POST /v1/documents`
endpoint means one HTTP round-trip and one auth check per document — real
client-side amplification. A reviewer suggested "batch processing: group
documents into batches of 50, single transaction per batch, embed the batch
together."

## Decision

Add `POST /v1/documents/batch` that accepts many files in one request, but:

- **One registration transaction is *not* used for the whole batch.** Each
  file is registered and committed independently.
- **Each document is still enqueued as its own Celery task**, exactly like the
  single-upload path.
- The response is `207 Multi-Status` with a **per-file result**: either the
  registered document or that file's rejection reason.

## Why not the suggested "single transaction, one task per batch of 50"

Because it couples failure domains that must stay separate:

1. **A single corrupt file would poison its 49 neighbors.** One transaction
   means one rollback: a parse error, a duplicate, or a quota rejection on
   file #37 fails the whole batch. Per-file registration means file #37 is
   reported as rejected and the other 49 succeed.
2. **A batch task is a bigger, coarser retry unit.** The existing
   permanent-vs-transient retry logic (ADR 0005) is per-document; a batch task
   would either re-run all 50 on one file's transient error, or need
   sub-tracking that reinvents per-document tasks inside the batch.
3. **Embedding is *already* batched where it matters** — inside each
   document's processing, `embed_batch_size` sends chunks to the provider in
   batches. The suggested win ("embed the batch together") is largely already
   captured, at the correct granularity, without cross-document coupling.

## What the batch endpoint actually buys

The legitimate win the reviewer identified — fewer HTTP round-trips and auth
checks for bulk upload — without the coupling. N documents become one request
and one auth check; failure isolation and retry semantics stay per-document.

## Consequences

- Clients get one call with a clear per-file outcome; partial success is the
  norm and is represented honestly (`accepted`/`rejected` counts).
- The heavy work is still N independent tasks, so a big batch doesn't create a
  single long-running task or a single large transaction.
- Trade-off accepted: registration is N commits, not one. At bulk-upload
  volumes this is cheap relative to parse+embed, and it is the price of
  failure isolation.
