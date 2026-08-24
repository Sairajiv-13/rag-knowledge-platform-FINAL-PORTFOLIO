// All browser calls go to the same-origin BFF proxy (/api/rag/*) — the
// tenant credential lives only in the Next.js server process.

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

async function detailOf(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body.detail ?? body);
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(`/api/rag/${path}`, init);
  if (res.status === 401 && typeof window !== "undefined") {
    // Session missing/expired -> bounce to login rather than surfacing a raw
    // 401 in the UI. The proxy returns 401 only for the unauthenticated case.
    window.location.href = "/login";
    throw new ApiError(401, "redirecting to login");
  }
  if (!res.ok) throw new ApiError(res.status, await detailOf(res));
  return res;
}

export interface SseEvent {
  event: string;
  // payload shapes vary per event type; callers narrow by event name
  data: Record<string, unknown>;
}

// POST-initiated SSE: EventSource only supports GET, so parse the stream by
// hand — split frames on the blank line, honor `event:` and `data:` fields.
export async function* sseEvents(res: Response): AsyncGenerator<SseEvent> {
  if (!res.body) throw new ApiError(res.status, "response has no body to stream");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      }
      if (dataLines.length > 0) {
        yield { event, data: JSON.parse(dataLines.join("\n")) };
      }
    }
  }
}
