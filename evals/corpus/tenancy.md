# Multi-tenant isolation

Every tenant shares the same database tables, and isolation is enforced in the
repository layer: every query filters by tenant identifier, and integration
tests assert that one tenant can never read another tenant's chunks. Row-level
security was rejected because the SET LOCAL pattern is fragile with pooled
async connections.

A denormalized tenant identifier is stored directly on the chunks table, so
retrieval filters by tenant without a join back to the documents table. This
keeps the hot retrieval path single-table at the cost of writing the tenant
id in two places.
