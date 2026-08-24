# Web UI

Next.js 15 + Tailwind chat console for the RAG API: streaming answers with
inline `[n]` citation markers and a sources panel, document upload/management
with live status polling, and explicit loading / error / empty states
throughout.

## Auth architecture (the part worth reading)

A browser can never hold an OAuth2 `client_secret`, so the Next.js **server**
owns all credentials. Users **log in through the UI** (`/login`) with a tenant
`client_id`/`client_secret`; the server verifies them against the API's token
endpoint and creates a **server-side session** keyed by an opaque, httpOnly
cookie (`src/lib/session.ts`). The BFF proxy
(`src/app/api/rag/[...path]/route.ts`) then serves each request using *that
session's* credential — exchanging it for a JWT (cached until near expiry,
refreshed once on a 401 race), forwarding only allow-listed API paths
(`documents|search|answers|usage` — never `auth`), and streaming SSE bodies
through untouched. The browser only ever holds the session id.

**This gives session-level tenant boundaries:** two browser sessions log in
as two different tenants and see only their own data — a real improvement over
the previous single ambient credential.

Honest limits, stated plainly: the session store is **in-memory**, so sessions
don't survive a server restart and aren't shared across replicas (a production
deployment backs this with Redis or signed stateless cookies — the seam is
exactly `src/lib/session.ts`). And this is still **tenant**-level auth, not
per-user accounts with roles: everyone with a given tenant credential shares
that tenant's data. Full user accounts + RBAC are future work.

## Run

```bash
# create a tenant credential to log in with:
python -m rag_platform.cli create-tenant --name Acme --slug acme
python -m rag_platform.cli create-credential --tenant acme --name web-ui
cd frontend && npm ci
RAG_API_URL=http://localhost:8000 npm run dev
# open http://localhost:3000 -> you'll be sent to /login; paste the credential
```

Or via compose from the repo root: `docker compose up -d web` →
http://localhost:3000 → log in. The web server needs only `RAG_API_URL`.

## Notes

- Chat is single-turn by design: the API answers one grounded question at a
  time; the transcript is client-side state.
- Client-side upload validation (extension, 10MB) mirrors the server's limits
  for fast feedback — the server remains the authority, and its error details
  (409 duplicate, 415 unsupported, 422 parse failure, 429 rate limited) are
  surfaced verbatim.
