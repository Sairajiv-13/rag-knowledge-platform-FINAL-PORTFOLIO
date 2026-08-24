// In-memory session store: maps an opaque session id (held by the browser in
// an httpOnly cookie) to that session's tenant credential + cached token.
//
// This is what makes the UI multi-tenant at the SESSION level: each browser
// session authenticates as its own tenant, so two people on the same server
// act as different tenants — unlike the previous single ambient credential.
//
// In-memory is deliberate for a portfolio deployment: it's the smallest thing
// that demonstrates real session boundaries. Its limits are stated honestly in
// the frontend README — sessions don't survive a server restart and don't
// share across replicas. A production system would back this with Redis or
// signed stateless cookies; the seam here is exactly where that swap goes.

import { randomBytes } from "crypto";

export interface Session {
  clientId: string;
  clientSecret: string;
  token: string | null;
  tokenExpiresAt: number;
}

const store = new Map<string, Session>();

export const SESSION_COOKIE = "rag_sid";

export function createSession(clientId: string, clientSecret: string): string {
  const sid = randomBytes(24).toString("base64url");
  store.set(sid, { clientId, clientSecret, token: null, tokenExpiresAt: 0 });
  return sid;
}

export function getSession(sid: string | undefined): Session | null {
  if (!sid) return null;
  return store.get(sid) ?? null;
}

export function destroySession(sid: string | undefined): void {
  if (sid) store.delete(sid);
}
