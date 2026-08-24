# Security guide

## Credential model

Each tenant holds one or more client-credential pairs. The client identifier
is public; the client secret is a 256-bit random string. Only a SHA-256 hash
of the secret is stored, because the secret has full entropy and a slow
password hash would add cost without adding security. Presenting a valid pair
at the token endpoint returns a JWT valid for thirty minutes.

## Revocation

Every authenticated request re-reads the credential row, so revoking a
credential takes effect on the very next request rather than waiting for the
current token to expire. This trades one extra database read per request for
immediate revocation, which is the right trade for a security control.

## Tenant isolation

All tenants share the same tables. Isolation is enforced in the repository
layer: every query is filtered by tenant identifier, and integration tests
assert that a request authenticated for one tenant can never retrieve another
tenant's chunks. Row-level security was evaluated and rejected because the
required session-variable pattern is fragile with pooled asynchronous
connections and can fail open.

## Rate limiting

Requests are limited per tenant using a fixed window counter in Redis. The
known weakness of a fixed window is that a burst straddling the window
boundary can briefly reach twice the nominal limit. If Redis is unavailable
the limiter fails open, allowing requests through, because a rate limiter
should protect capacity rather than become a new point of failure.

## Secret handling

Secrets are provided through environment variables and never logged. The
default configuration contains no secret that works anywhere, so an
accidental deployment of defaults cannot expose a real system.
