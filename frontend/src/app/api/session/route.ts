// Session login/logout. The browser posts a tenant client_id/client_secret
// once; the server verifies them against the RAG token endpoint, and on
// success creates a server-side session and sets an httpOnly cookie. The
// browser never holds the credential OR the JWT — both live server-side,
// keyed by the opaque session id. This is the multi-tenant replacement for
// the old single ambient credential.

import { NextRequest } from "next/server";
import { cookies } from "next/headers";
import { createSession, destroySession, SESSION_COOKIE } from "@/lib/session";

const API = process.env.RAG_API_URL ?? "http://localhost:8000";

export async function POST(req: NextRequest): Promise<Response> {
  let clientId: string;
  let clientSecret: string;
  try {
    const body = (await req.json()) as { client_id?: string; client_secret?: string };
    clientId = (body.client_id ?? "").trim();
    clientSecret = (body.client_secret ?? "").trim();
  } catch {
    return Response.json({ detail: "invalid request body" }, { status: 400 });
  }
  if (!clientId || !clientSecret) {
    return Response.json({ detail: "client_id and client_secret are required" }, { status: 400 });
  }

  // Verify the credential by actually exchanging it — we never store an
  // unvalidated credential, and a bad login fails here, not on first use.
  let res: Response;
  try {
    res = await fetch(`${API}/v1/auth/token`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "client_credentials",
        client_id: clientId,
        client_secret: clientSecret,
      }),
      cache: "no-store",
    });
  } catch {
    return Response.json({ detail: "could not reach the RAG API" }, { status: 502 });
  }
  if (res.status === 401) {
    return Response.json({ detail: "invalid client credentials" }, { status: 401 });
  }
  if (!res.ok) {
    return Response.json({ detail: `token endpoint returned ${res.status}` }, { status: 502 });
  }

  const sid = createSession(clientId, clientSecret);
  const jar = await cookies();
  jar.set(SESSION_COOKIE, sid, {
    httpOnly: true, // JS can't read it — mitigates XSS token theft
    sameSite: "lax",
    path: "/",
    secure: process.env.NODE_ENV === "production",
    maxAge: 60 * 60 * 8, // 8h browser session
  });
  return Response.json({ ok: true });
}

export async function DELETE(): Promise<Response> {
  const jar = await cookies();
  const sid = jar.get(SESSION_COOKIE)?.value;
  destroySession(sid);
  jar.delete(SESSION_COOKIE);
  return Response.json({ ok: true });
}

// Lets the UI know whether it's authenticated without exposing the credential.
export async function GET(): Promise<Response> {
  const jar = await cookies();
  const sid = jar.get(SESSION_COOKIE)?.value;
  const { getSession } = await import("@/lib/session");
  return Response.json({ authenticated: getSession(sid) !== null });
}
