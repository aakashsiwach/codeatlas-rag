// Client for the CodeAtlas HTTP API: types, a server-sent-events parser and fetch helpers.

export interface Source {
  index: number;
  chunk_id: string;
  kind: string;
  path: string;
  start_line: number;
  end_line: number;
  symbol: string | null;
  score: number | null;
  preview: string | null;
}

export interface Citation {
  index: number;
  path: string;
  start_line: number;
  end_line: number;
  symbol: string | null;
  kind: string;
}

export interface AnswerDone {
  answer: string;
  config: string;
  citations: Citation[];
  sources: Source[];
  confidence: number;
  confidence_source: string;
  refused: boolean;
  invalid_citations: number[];
  truncated: boolean;
  usage: { input_tokens?: number; output_tokens?: number; cost_usd?: number | null };
  trace_id: string | null;
  setup?: string | null; // the named setup the request used, if any
  timings_ms?: { retrieval_ms?: number; total_ms?: number };
}

export interface ActiveConfig {
  version: string;
  repo: string | null;
  default_mode: string;
  default_k: number;
  modes: string[];
  flags: string[];
  options: Record<string, number | boolean>;
  embedder: string | null;
  chunker: string | null;
  reranker: string | null;
  llm: string | null;
  thresholds: { vector: number | null; rerank: number | null };
  auth_required: boolean;
  setups?: Record<string, { mode: string; k: number }>;
  default_setup?: string;
}

export interface FileSlice {
  path: string;
  language: string;
  start_line: number;
  end_line: number;
  total_lines: number;
  lines: string[];
}

export type StreamEvent =
  | {
      event: "sources";
      data: { sources: Source[]; confidence: number; confidence_source: string; retrieval_ms?: number; setup?: string | null };
    }
  | { event: "token"; data: string }
  | { event: "done"; data: AnswerDone }
  | { event: "error"; data: { detail: string; kind?: string; error_class?: string } };

/** Incremental parser for a text/event-stream body: feed it chunks, it returns complete events. */
export class SseParser {
  private buffer = "";

  push(chunk: string): StreamEvent[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const events: StreamEvent[] = [];
    let end: number;
    while ((end = this.buffer.indexOf("\n\n")) !== -1) {
      const block = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      let name = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) events.push({ event: name, data: JSON.parse(data.join("\n")) } as StreamEvent);
    }
    return events;
  }
}

/** Split answer text into plain text and citation markers ([1], [1, 2], [1-3]) outside code spans. */
export type Segment = { text: string } | { cite: number };

export function citationSegments(text: string): Segment[] {
  const out: Segment[] = [];
  const pattern = /(`[^`\n]*`)|\[(\d+(?:\s*[,–-]\s*\d+)*)\]/g;
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    if (m[1]) continue; // inline code: keep as text
    const start = m.index ?? 0;
    if (start > last) out.push({ text: text.slice(last, start) });
    for (const n of expand(m[2])) out.push({ cite: n });
    last = start + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return mergeText(out);
}

function expand(group: string): number[] {
  const nums: number[] = [];
  for (const part of group.split(",")) {
    const [a, b] = part.split(/[–-]/).map((x) => parseInt(x.trim(), 10));
    if (b !== undefined && b >= a && b - a < 50) for (let i = a; i <= b; i++) nums.push(i);
    else nums.push(a);
  }
  return nums;
}

function mergeText(segments: Segment[]): Segment[] {
  const out: Segment[] = [];
  for (const s of segments) {
    const prev = out[out.length - 1];
    if ("text" in s && prev && "text" in prev) prev.text += s.text;
    else out.push(s);
  }
  return out;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function headers(token: string, json = false): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}

async function getJson<T>(url: string, token: string): Promise<T> {
  const resp = await fetch(url, { headers: headers(token) });
  if (!resp.ok) throw new ApiError(resp.status, await errorText(resp));
  return resp.json() as Promise<T>;
}

async function errorText(resp: Response): Promise<string> {
  try {
    const body = await resp.json();
    const detail = body.detail;
    return typeof detail === "string" ? detail : detail?.detail ?? JSON.stringify(detail);
  } catch {
    return `${resp.status} ${resp.statusText}`;
  }
}

/** 35 -> "35 ms", 27199 -> "27.2 s". */
export function formatMs(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export const fetchConfig = (token: string) => getJson<ActiveConfig>("/api/config", token);

export function fetchFile(path: string, start: number, end: number, token: string): Promise<FileSlice> {
  const q = new URLSearchParams({ path, start: String(start), end: String(end) });
  return getJson<FileSlice>(`/api/file?${q}`, token);
}

/** POST /api/ask/stream and hand each parsed event to onEvent. Resolves when the stream ends. */
export async function askStream(
  question: string,
  // A named setup (fast, quality), or an explicit mode and k.
  options: { setup?: string; mode?: string; k?: number; token: string; signal?: AbortSignal },
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const resp = await fetch("/api/ask/stream", {
    method: "POST",
    headers: headers(options.token, true),
    body: JSON.stringify(
      options.setup ? { question, setup: options.setup } : { question, mode: options.mode, k: options.k },
    ),
    signal: options.signal,
  });
  if (!resp.ok || !resp.body) throw new ApiError(resp.status, await errorText(resp));
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const event of parser.push(value)) onEvent(event);
  }
}
