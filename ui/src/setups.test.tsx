import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const CONFIG = {
  version: "0.1.0",
  repo: "spring-petclinic",
  default_mode: "vector",
  default_k: 5,
  modes: ["vector", "bm25", "hybrid"],
  flags: ["rerank", "graph"],
  options: {},
  embedder: "local:BAAI/bge-small-en-v1.5",
  chunker: "ast",
  reranker: "Alibaba-NLP/gte-reranker-modernbert-base",
  llm: "fake:offline",
  thresholds: { vector: 0.6177, rerank: 0.868 },
  auth_required: false,
  setups: { fast: { mode: "vector", k: 5 }, quality: { mode: "hybrid+rerank+graph", k: 5 } },
  default_setup: "fast",
};

const source = { index: 1, chunk_id: "c1", kind: "method", path: "owner/Owner.java", start_line: 3, end_line: 9, symbol: "Owner#getPet", score: 0.9, preview: null };

const sse = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

/** A /api/ask/stream response whose events the test pushes when it wants. */
function controlledStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start: (c) => (controller = c) });
  const encoder = new TextEncoder();
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (text: string) => controller.enqueue(encoder.encode(text)),
    close: () => controller.close(),
  };
}

describe("fast | quality setup switch", () => {
  let stream: ReturnType<typeof controlledStream>;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
    stream = controlledStream();
    fetchMock = vi.fn((url: string) =>
      Promise.resolve(url === "/api/config" ? new Response(JSON.stringify(CONFIG)) : stream.response),
    );
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const askedBody = () => {
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/ask/stream");
    return JSON.parse((call![1] as RequestInit).body as string);
  };

  async function ask(question: string) {
    fireEvent.change(screen.getByTestId("question"), { target: { value: question } });
    fireEvent.click(screen.getByTestId("send"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/ask/stream", expect.anything()));
  }

  it("defaults to fast and sends the setup name, not a mode", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("setup-fast").getAttribute("aria-checked")).toBe("true"));
    expect(screen.getByTestId("open-settings").textContent).toContain("fast · vector · k=5");
    await ask("How are pets added?");
    expect(askedBody()).toEqual({ question: "How are pets added?", setup: "fast" });
    expect(screen.queryByTestId("progress")).toBeNull(); // fast keeps the plain loading dots
  });

  it("asks one question with quality, shows progress, then the setup and latency", async () => {
    render(<App />);
    await waitFor(() => expect((screen.getByTestId("setup-quality") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByTestId("setup-quality"));
    expect(screen.getByTestId("setup-quality").getAttribute("aria-checked")).toBe("true");
    await ask("What breaks if I change Owner?");
    expect(askedBody()).toEqual({ question: "What breaks if I change Owner?", setup: "quality" });

    const progress = await screen.findByTestId("progress");
    expect(progress.textContent).toContain("Retrieving with the quality setup");
    expect(screen.getByRole("progressbar")).toBeTruthy();
    expect((screen.getByTestId("setup-fast") as HTMLButtonElement).disabled).toBe(true); // no switching mid-question

    stream.push(sse("sources", { sources: [source], confidence: 0.93, confidence_source: "rerank", retrieval_ms: 27199.5, setup: "quality" }));
    await waitFor(() => expect(screen.queryByTestId("progress")).toBeNull());
    stream.push(sse("token", "Owner is used by [1]."));
    stream.push(
      sse("done", {
        answer: "Owner is used by [1].", config: "hybrid+rerank+graph", citations: [], sources: [source],
        confidence: 0.93, confidence_source: "rerank", refused: false, invalid_citations: [], truncated: false,
        usage: { cost_usd: null }, trace_id: null, setup: "quality", timings_ms: { retrieval_ms: 27199.5, total_ms: 28012.3 },
      }),
    );
    stream.close();

    const meta = await screen.findByTestId("answer-meta");
    expect(meta.textContent).toBe("quality · hybrid+rerank+graph · retrieval 27.2 s · total 28.0 s · confidence 0.93 (rerank)");
    expect(JSON.parse(localStorage.getItem("codeatlas.settings")!).setup).toBe("quality");
  });
});
