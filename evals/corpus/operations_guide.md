# Operations guide

## Health checks

The liveness probe reports whether the process is running and never checks
dependencies, so a database outage does not cause the orchestrator to kill and
restart healthy API processes. The readiness probe checks that dependencies
are reachable and reports not-ready when they are not, which removes the
instance from the load balancer rotation until it recovers.

## Deployment

Deployments follow a migrate-then-roll order: database migrations are applied
against the live database first, then the new application containers start.
Because migrations are additive, the previous application version continues to
work against the newer schema, which makes rollback a matter of redeploying
the previous container image.

## Monitoring

Metrics are exported in Prometheus format and labeled by route template to
keep cardinality bounded. A provisioned dashboard shows request rate, latency
percentiles, error ratio, and token usage. Logs are structured as one JSON
object per line and carry a request identifier so a single request can be
followed across every log line it produced.

## Backups

The database is backed up nightly with continuous write-ahead-log archiving
for point-in-time recovery. Restore drills are run monthly against a restored
copy. Backups include uploaded document bytes, so they are encrypted at rest
and access to them is audited the same way as access to the uploads.

## Incident response

When ingestion appears stuck, the first step is to inspect the worker logs for
the affected document, then check the queue depth and worker health. A
document that already completed can be safely re-enqueued because processing
is idempotent: it replaces any existing chunks for that document.
