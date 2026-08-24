# ADR 0002: Multi-tenant isolation via shared tables + tenant_id

Date: 2026-07-05 · Status: accepted

## Context
Every document and chunk belongs to exactly one tenant, and a retrieval query
must never surface another tenant's data. Options considered:

1. **Database-per-tenant** — strongest isolation, but connection pools,
   migrations, and pgvector indexes multiply per tenant. Operationally wrong
   for a service expecting many small tenants.
2. **Schema-per-tenant** — same migration fan-out problem, and pgvector HNSW
   indexes per schema get expensive fast.
3. **Shared tables + Postgres Row-Level Security (RLS)** — DB-enforced
   isolation, but with a pooled asyncpg engine every transaction must
   `SET LOCAL app.tenant_id`, and a missed SET fails *open* unless policies
   are written very carefully. Real complexity for a solo-maintained service.
4. **Shared tables + tenant_id column, enforced in the application layer.**

## Decision
Option 4. Every row carries `tenant_id`; `chunks.tenant_id` is denormalized
from `documents` so the retrieval hot path filters without a join. The
repository layer (stage 3) takes `tenant_id` as a *required* argument on every
query — there is no "query all tenants" API to misuse.

## Consequences
- Isolation is a code discipline, not a database guarantee. Mitigations:
  repository functions require tenant_id, and an integration test (stage 6)
  asserts that tenant A's query cannot return tenant B's chunks.
- HNSW scans apply the tenant filter *after* candidate selection, so recall
  for tiny tenants can degrade as the global index grows. Acceptable at this
  scale; partitioning / partial indexes are the documented escape hatch
  (SCALABILITY.md, stage 8).
- RLS remains the natural hardening step if this ever handles regulated data;
  the schema (tenant_id everywhere) is already shaped for it.
