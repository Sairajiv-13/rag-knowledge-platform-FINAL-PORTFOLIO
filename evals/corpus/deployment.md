# Deployment runbook

Deploys follow migrate-then-roll: apply Alembic migrations against the live
database first, then start the new api and worker containers. Health is
verified with the healthz and readyz probes; readyz names the dependency that
is unreachable when it returns 503.

Rollbacks re-deploy the previous image tag. Because migrations so far are
additive, the previous version runs safely against the newer schema. A
non-additive migration requires an explicit expand-and-contract plan before it
ships.
