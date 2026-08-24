// BFF proxy: forwards allow-listed API calls using the SESSION's tenant
// credential (set at login), never an ambient one. The browser holds only an
// opaque httpOnly session id; the credential and JWT live server-side in the
// session store. Two browser sessions therefore act as two different tenants.

import { NextRequest } from "next/server";
import { cookies } from "next/headers";
import { getSession, Session, SESSION_COOKIE } from "@/lib/session";

const API = process.env.RAG_API_URL ?? "http://localhost:8000";

// documents/search/answers/usage only — notably NOT auth: the browser has no
// business reaching the token endpoint through us.
const ALLOWED = new Set(["documents", "search", "answers", "usage"]);

async function tokenFor(session: Session, force: boolean): Promise<string> {
  if (!force && session.token && Date.now() < session.tokenExpiresAt - 60_000) {
    return session.token;
  }
  const res = await fetch(`${API}/v1/auth/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "client_credentials",
      client_id: session.clientId,
      client_secret: session.clientSecret,
    }),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`token endpoint returned ${res.status}`);
  const body = (await res.json()) as { access_token: string; expires_in: number };
  session.token = body.access_token;
  session.tokenExpiresAt = Date.now() + body.expires_in * 1000;
  return session.token;
}

function jsonError(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function proxy(req: NextRequest, path: string[], retried = false): Promise<Response> {
  if (path.length === 0 || !ALLOWED.has(path[0])) {
    return jsonError(404, "not proxied");
  }

  const jar = await cookies();
  const session = getSession(jar.get(SESSION_COOKIE)?.value);
  if (session === null) {
    // Not logged in (or session expired/lost on restart) -> the UI redirects
    // to /login on a 401.
    return jsonError(401, "not authenticated: log in with a tenant credential");
  }

  let token: string;
  try {
    token = await tokenFor(session, retried);
  } catch {
    return jsonError(502, "could not reach the RAG API token endpoint");
  }

  const url = `${API}/v1/${path.join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = { authorization: `Bearer ${token}` };
  const init: RequestInit = { method: req.method, headers, cache: "no-store" };

  if (req.method === "POST") {
    const contentType = req.headers.get("content-type") ?? "";
    if (contentType.includes("multipart/form-data")) {
      // re-encode via formData(): fetch sets a fresh multipart boundary
      init.body = await req.formData();
    } else {
      headers["content-type"] = "application/json";
      init.body = await req.text();
    }
  }

  const upstream = await fetch(url, init);
  if (upstream.status === 401 && !retried) {
    // token expired between cache check and use: refresh once, retry once
    return proxy(req, path, true);
  }
  // Pass the body stream through untouched — this is what keeps SSE streaming.
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "cache-control": "no-cache",
    },
  });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function DELETE(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
