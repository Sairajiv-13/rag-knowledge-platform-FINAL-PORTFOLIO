import type { DocumentItem } from "@/lib/types";

const STYLES: Record<DocumentItem["status"], string> = {
  pending: "bg-amber-50 text-amber-700 border-amber-200 animate-pulse",
  processing: "bg-blue-50 text-blue-700 border-blue-200 animate-pulse",
  completed: "bg-emerald-50 text-emerald-700 border-emerald-200",
  failed: "bg-red-50 text-red-700 border-red-200",
};

export default function StatusBadge({ status }: { status: DocumentItem["status"] }) {
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${STYLES[status]}`}
    >
      {status}
    </span>
  );
}
