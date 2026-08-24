"use client";

// Document management: upload (validated client-side to mirror server
// limits), a table that polls while anything is pending/processing, delete,
// and a 30-day usage card. Loading, error, and empty states are all explicit.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import type { DocumentItem, DocumentList, UsageSummary } from "@/lib/types";
import ErrorBanner from "@/components/ErrorBanner";
import Spinner from "@/components/Spinner";
import StatusBadge from "@/components/StatusBadge";

const ACCEPT = ".pdf,.md,.markdown,.html,.htm";
const MAX_BYTES = 10 * 1024 * 1024; // mirrors the server cap; server still decides

export default function DocumentsPage() {
  const [docs, setDocs] = useState<DocumentItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploadNote, setUploadNote] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch("documents?limit=100");
      const body = (await res.json()) as DocumentList;
      setDocs(body.items);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.detail : "Could not load documents");
    }
  }, []);

  const loadUsage = useCallback(async () => {
    try {
      const res = await apiFetch("usage?days=30");
      setUsage((await res.json()) as UsageSummary);
    } catch {
      setUsage(null); // usage card is best-effort; the page works without it
    }
  }, []);

  useEffect(() => {
    void load();
    void loadUsage();
  }, [load, loadUsage]);

  // Poll only while some document is still in flight.
  const inFlight = docs?.some((d) => d.status === "pending" || d.status === "processing") ?? false;
  useEffect(() => {
    if (!inFlight) return;
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [inFlight, load]);

  async function upload(files: FileList | null) {
    const file = files?.[0];
    if (!file || uploading) return;
    if (file.size > MAX_BYTES) {
      setUploadNote({ kind: "err", text: `${file.name} is over the 10MB limit` });
      return;
    }
    setUploading(true);
    setUploadNote(null);
    try {
      const form = new FormData();
      form.append("file", file);
      await apiFetch("documents", { method: "POST", body: form });
      setUploadNote({ kind: "ok", text: `${file.name} accepted — ingesting…` });
      await load();
    } catch (err) {
      // surfaces the API's own reasons: 409 duplicate, 415 unsupported,
      // 413 too large, 422 parse failure, 429 rate limited
      setUploadNote({
        kind: "err",
        text: err instanceof ApiError ? err.detail : "Upload failed",
      });
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function remove(doc: DocumentItem) {
    if (!confirm(`Delete ${doc.filename} and all its chunks?`)) return;
    try {
      await apiFetch(`documents/${doc.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setUploadNote({
        kind: "err",
        text: err instanceof ApiError ? err.detail : "Delete failed",
      });
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold">Documents</h1>
          <p className="text-sm text-slate-500">PDF, Markdown, or HTML · 10MB max</p>
        </div>
        {usage && usage.total_calls > 0 && (
          <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-right text-xs text-slate-500">
            <p className="font-medium text-slate-700">Last 30 days</p>
            <p>
              {usage.total_calls} answers · {usage.total_input_tokens + usage.total_output_tokens}{" "}
              tokens
              {usage.total_cost_usd !== null && ` · $${usage.total_cost_usd.toFixed(4)}`}
            </p>
          </div>
        )}
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void upload(e.dataTransfer.files);
        }}
        className={`rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors ${
          dragOver ? "border-blue-400 bg-blue-50" : "border-slate-300 bg-white"
        }`}
      >
        {uploading ? (
          <Spinner label="Uploading…" />
        ) : (
          <>
            <p className="text-sm text-slate-600">
              Drag a file here, or{" "}
              <button
                onClick={() => fileInput.current?.click()}
                className="font-medium text-blue-600 hover:underline"
              >
                browse
              </button>
            </p>
            <input
              ref={fileInput}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => void upload(e.target.files)}
            />
          </>
        )}
      </div>

      {uploadNote && (
        <p
          className={`rounded-lg px-4 py-2.5 text-sm ${
            uploadNote.kind === "ok"
              ? "border border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border border-red-200 bg-red-50 text-red-800"
          }`}
        >
          {uploadNote.text}
        </p>
      )}

      {loadError ? (
        <ErrorBanner message={loadError} onRetry={() => void load()} />
      ) : docs === null ? (
        // loading skeleton
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-12 animate-pulse rounded-lg bg-slate-200/70" />
          ))}
        </div>
      ) : docs.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white px-6 py-12 text-center">
          <p className="font-medium text-slate-700">No documents yet</p>
          <p className="mt-1 text-sm text-slate-500">
            Upload your first document above — once it&apos;s ingested, ask about it in Chat.
          </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-2.5 font-medium">File</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Chunks</th>
                <th className="px-4 py-2.5 font-medium">Added</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {docs.map((doc) => (
                <tr key={doc.id} className="align-top">
                  <td className="px-4 py-2.5">
                    <p className="font-medium text-slate-800">{doc.filename}</p>
                    {doc.status === "failed" && doc.error_message && (
                      <p className="mt-0.5 text-xs text-red-600">{doc.error_message}</p>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={doc.status} />
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">{doc.chunk_count ?? "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">
                    {new Date(doc.created_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={() => void remove(doc)}
                      className="rounded-md px-2 py-1 text-xs text-slate-400 hover:bg-red-50 hover:text-red-600"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
