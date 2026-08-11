// Typed client for the FastAPI service, always called through
// /api/proxy/* — see app/api/proxy/[...path]/route.ts. Shapes here mirror
// api/app/models.py exactly; keep them in sync by hand (no shared schema
// package for a project this size).

export type DocumentStatus = "parsing" | "embedding" | "ready" | "failed";

export type DocumentSummary = {
  id: string;
  filename: string;
  mime_type: string;
  byte_size: number;
  page_count: number | null;
  chunk_count: number;
  status: DocumentStatus;
  error: string | null;
  created_at: string;
};

export type DocumentDetail = DocumentSummary & {
  full_text: string | null;
};

export type RetrievedSourceWire = {
  ordinal: number;
  chunk_id: string;
  document_id: string;
  filename: string;
  page_start: number | null;
  page_end: number | null;
  score: number;
};

export type Citation = {
  marker: number;
  chunk_id: string;
  document_id: string;
  filename: string;
  page_start: number | null;
  page_end: number | null;
  char_start: number;
  char_end: number;
};

export type AbstainReason = "below_floor" | "model_declined" | "no_documents" | null;

export type AnswerPayload = {
  text: string;
  citations: Citation[];
  abstained: boolean;
  abstain_reason: AbstainReason;
  assumption: string | null;
};

export type DoneEvent = {
  latency_ms: number;
  prompt_tokens: number;
  output_tokens: number;
};

// Discriminated union over the SSE event names /ask emits.
export type AskEvent =
  | { type: "stage"; stage: "retrieving" | "generating" }
  | { type: "sources"; sources: RetrievedSourceWire[] }
  | { type: "answer"; answer: AnswerPayload }
  | { type: "done"; done: DoneEvent }
  | { type: "error"; message: string };

const PROXY_BASE = "/api/proxy";

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${body ? `: ${body}` : ""}`);
  }
  return res.json() as Promise<T>;
}

export async function checkHealth(): Promise<{ status: string; checks: Record<string, string> }> {
  const res = await fetch(`${PROXY_BASE}/health`, { cache: "no-store" });
  return asJson(res);
}

export async function listDocuments(): Promise<DocumentSummary[]> {
  const res = await fetch(`${PROXY_BASE}/documents`, { cache: "no-store" });
  return asJson(res);
}

export async function getDocument(id: string): Promise<DocumentDetail> {
  const res = await fetch(`${PROXY_BASE}/documents/${id}`, { cache: "no-store" });
  return asJson(res);
}

export async function uploadDocument(file: File): Promise<DocumentSummary> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${PROXY_BASE}/documents`, { method: "POST", body: form });
  return asJson(res);
}

export async function deleteDocument(id: string): Promise<void> {
  const res = await fetch(`${PROXY_BASE}/documents/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
}

/**
 * POST /ask and parse the SSE stream by hand. The browser's EventSource
 * can't send a POST body, so this reads the fetch body stream directly and
 * splits it on the "\n\n" record boundary the server writes.
 */
export async function askQuestion(
  question: string,
  onEvent: (event: AskEvent) => void,
  opts: { signal?: AbortSignal } = {},
): Promise<void> {
  const res = await fetch(`${PROXY_BASE}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal: opts.signal,
  });

  if (!res.ok || !res.body) {
    const body = await res.text().catch(() => "");
    onEvent({ type: "error", message: body || `${res.status} ${res.statusText}` });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const record = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const parsed = parseSSERecord(record);
      if (parsed) onEvent(parsed);
    }
  }
}

function parseSSERecord(record: string): AskEvent | null {
  let eventName = "";
  let dataLine = "";
  for (const line of record.split("\n")) {
    if (line.startsWith("event: ")) eventName = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLine += line.slice(6);
  }
  if (!eventName || !dataLine) return null;

  const data = JSON.parse(dataLine);
  switch (eventName) {
    case "stage":
      return { type: "stage", stage: data.stage };
    case "sources":
      return { type: "sources", sources: data as RetrievedSourceWire[] };
    case "answer":
      return { type: "answer", answer: data as AnswerPayload };
    case "done":
      return { type: "done", done: data as DoneEvent };
    case "error":
      return { type: "error", message: data.message };
    default:
      return null;
  }
}
