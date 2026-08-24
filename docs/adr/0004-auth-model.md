# ADR 0004: Auth — OAuth2 client credentials + short-lived HS256 JWTs

Date: 2026-07-06 · Status: accepted

## Context
The API is machine-to-machine: tenants are companies whose *services* call us;
there is no human resource owner at the API layer (the stage-8 web UI will sit
behind its own session, proxying to this API). Options considered: long-lived
static API keys checked per request; OAuth2 authorization-code (wrong flow —
that exists to delegate a human user's consent); OAuth2 client credentials.

## Decision
**Client-credentials grant (RFC 6749 §4.4).** Each tenant holds one or more
credential pairs (issued/revoked via CLI — the admin plane is deliberately
operator tooling, not HTTP, until a real IdP is in scope). `POST /v1/auth/token`
exchanges them for a **30-minute HS256 JWT** carrying `tid` (tenant) and `sub`
(credential).

Supporting choices:
- **HS256, not RS256**: one issuer and one verifier — the same service.
  Asymmetric signatures pay complexity for a multi-verifier property we don't have.
- **Secrets stored as plain SHA-256**: they are 256-bit random strings, so a
  password KDF (bcrypt/argon2) adds cost without security — KDFs exist for
  low-entropy human passwords.
- **Credential liveness checked on every request** (PK lookup): buys immediate
  revocation instead of "revoked keys work for up to TTL". A Redis cache is the
  documented optimization if this read ever shows in profiles.
- **Timing hygiene**: unknown client_id and wrong secret share a code path
  (dummy-hash compare) and one error message, so the token endpoint doesn't
  confirm which client_ids exist.
- Cross-tenant object access returns **404, not 403** — a 403 would confirm
  the identifier exists.

## Consequences
- No per-credential scopes yet (every credential has full tenant access) —
  listed in Limitations; the JWT claim structure leaves room for a `scope` claim.
- jwt_secret is a single shared secret: rotating it invalidates all live
  tokens (acceptable at 30-min TTL); secret management is the deployment's
  concern (stage 8 Terraform wires a secret store).
