export interface Citation {
  marker: number;
  chunk_id: number;
  document_id: string;
  filename: string;
  location: string | null;
  snippet: string;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
}

export interface DocumentItem {
  id: string;
  filename: string;
  source_type: "pdf" | "markdown" | "html";
  status: "pending" | "processing" | "completed" | "failed";
  chunk_count: number | null;
  error_message: string | null;
  created_at: string;
}

export interface DocumentList {
  items: DocumentItem[];
  total: number;
}

export interface UsageSummary {
  days: number;
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  citations: Citation[];
  usage: Usage | null;
  model: string | null;
  streaming: boolean;
  error: string | null;
}
