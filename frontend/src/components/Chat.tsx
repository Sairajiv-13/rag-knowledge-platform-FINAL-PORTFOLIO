"use client";

// Chat over the tenant's documents. Single-turn by design: the API answers
// one grounded question at a time (no server-side conversation memory yet —
// stated in the README), so history here is a client-side transcript.

import { useEffect, useRef, useState } from "react";
import { ApiError, apiFetch, sseEvents } from "@/lib/api";
import type { ChatMessage, Citation, Usage } from "@/lib/types";
import ErrorBanner from "@/components/ErrorBanner";

const SAMPLE_PROMPTS = [
  "How are tenants isolated from each other?",
  "What does ef_search control?",
  "How does upload deduplication work?",
];

let nextId = 0;
const newId = () => `m${nextId++}`;

/** Render answer text with [n] markers as styled superscripts. */
function AnswerText({ text }: { text: string }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <p className="whitespace-pre-wrap leading-relaxed">
      {parts.map((part, i) => {
        const m = part.match(/^\[(\d+)\]$/);
        return m ? (
          <sup key={i} className="mx-0.5 select-none font-semibold text-blue-600">
            [{m[1]}]
          </sup>
        ) : (
          <span key={i}>{part}</span>
        );
      })}
    </p>
  );
}

function Citations({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Sources</p>
      {citations.map((c) => (
        <div key={c.marker} className="rounded-lg bg-slate-50 px-3 py-2 text-sm">
          <p className="font-medium text-slate-700">
            <span className="mr-1.5 text-blue-600">[{c.marker}]</span>
            {c.filename}
            {c.location && <span className="ml-1.5 font-normal text-slate-400">{c.location}</span>}
          </p>
          <p className="mt-0.5 line-clamp-2 text-slate-500">{c.snippet}</p>
        </div>
      ))}
    </div>
  );
}

function UsageFooter({ usage, model }: { usage: Usage | null; model: string | null }) {
  if (!usage || !model) return null;
  return (
    <p className="mt-2 text-xs text-slate-400">
      {model} · {usage.input_tokens} in / {usage.output_tokens} out tokens
    </p>
  );
}

export default function Chat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const patch = (id: string, update: Partial<ChatMessage>) =>
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...update } : m)));

  async function send(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true);
    setDraft("");
    const assistantId = newId();
    setMessages((prev) => [
      ...prev,
      { id: newId(), role: "user", text: q, citations: [], usage: null, model: null, streaming: false, error: null },
      { id: assistantId, role: "assistant", text: "", citations: [], usage: null, model: null, streaming: true, error: null },
    ]);

    try {
      const res = await apiFetch("answers", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ query: q, stream: true }),
      });
      let text = "";
      for await (const { event, data } of sseEvents(res)) {
        if (event === "citations") {
          patch(assistantId, { citations: (data.citations ?? []) as Citation[] });
        } else if (event === "delta") {
          text += data.text as string;
          patch(assistantId, { text });
        } else if (event === "done") {
          patch(assistantId, {
            streaming: false,
            usage: (data.usage ?? null) as Usage | null,
            model: (data.model ?? null) as string | null,
          });
        }
      }
      // stream ended without a done event (network cut): keep the text, stop the cursor
      patch(assistantId, { streaming: false });
    } catch (err) {
      patch(assistantId, {
        streaming: false,
        error: err instanceof ApiError ? err.detail : "Request failed — is the API up?",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-[calc(100vh-8.5rem)] flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto pb-4">
        {messages.length === 0 && (
          <div className="mt-16 text-center">
            <p className="text-lg font-medium text-slate-700">Ask your documents anything</p>
            <p className="mt-1 text-sm text-slate-500">
              Answers are grounded in what you&apos;ve uploaded and cite their sources.
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-2">
              {SAMPLE_PROMPTS.map((p) => (
                <button
                  key={p}
                  onClick={() => send(p)}
                  className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 hover:border-slate-300 hover:bg-slate-100"
                >
                  {p}
                </button>
              ))}
            </div>
            <p className="mt-8 text-xs text-slate-400">
              No documents yet? Add some on the Documents page first.
            </p>
          </div>
        )}

        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-slate-900 px-4 py-2.5 text-sm text-white">
                {m.text}
              </div>
            </div>
          ) : (
            <div key={m.id} className="flex justify-start">
              <div className="max-w-[85%] rounded-2xl rounded-bl-sm border border-slate-200 bg-white px-4 py-3 text-sm shadow-sm">
                {m.error ? (
                  <ErrorBanner message={m.error} />
                ) : (
                  <>
                    {m.text ? (
                      <AnswerText text={m.text} />
                    ) : (
                      m.streaming && <span className="text-slate-400">Retrieving context…</span>
                    )}
                    {m.streaming && (
                      <span className="ml-1 inline-block h-4 w-2 animate-pulse bg-slate-300 align-text-bottom" />
                    )}
                    {!m.streaming && <Citations citations={m.citations} />}
                    {!m.streaming && <UsageFooter usage={m.usage} model={m.model} />}
                  </>
                )}
              </div>
            </div>
          ),
        )}
        <div ref={bottomRef} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(draft);
        }}
        className="flex gap-2 border-t border-slate-200 pt-4"
      >
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(draft);
            }
          }}
          rows={1}
          placeholder={busy ? "Answering…" : "Ask a question (Enter to send)"}
          disabled={busy}
          className="max-h-40 flex-1 resize-none rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm shadow-sm focus:border-slate-400 focus:outline-none disabled:bg-slate-100"
        />
        <button
          type="submit"
          disabled={busy || !draft.trim()}
          className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
  );
}
