import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { Source } from "./api";
import { SourceViewer } from "./components/SourceViewer";

// Newer Chrome returns a Promise from scrollIntoView. An effect that returns it hands React a
// non-function "cleanup", which crashes the app on unmount (StrictMode does this on mount).
const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));

describe("scroll effects", () => {
  let errors: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn(() => Promise.resolve()) as unknown as Element["scrollIntoView"];
    errors = vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  const effectWarnings = () =>
    errors.mock.calls.filter((args: unknown[]) => String(args[0]).includes("useEffect must not return anything"));

  it("App survives scrollIntoView returning a Promise", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json({ default_mode: "vector", default_k: 5, modes: [], flags: [], options: {} })));
    const { unmount } = render(<StrictMode><App /></StrictMode>);
    await new Promise((r) => setTimeout(r, 0));
    expect(() => unmount()).not.toThrow();
    expect(effectWarnings()).toEqual([]);
  });

  it("SourceViewer survives scrollIntoView returning a Promise", async () => {
    const source: Source = {
      index: 1, chunk_id: "c1", kind: "method", path: "a/Owner.java", start_line: 3, end_line: 4,
      symbol: "Owner#m", score: 0.9, preview: null,
    };
    vi.stubGlobal("fetch", vi.fn(() =>
      json({ path: "a/Owner.java", language: "java", start_line: 1, end_line: 6, total_lines: 6, lines: ["a", "b", "c", "d", "e", "f"] }),
    ));
    const { unmount } = render(<StrictMode><SourceViewer source={source} token="" onClose={() => {}} /></StrictMode>);
    await screen.findByText("c");
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled()); // the effect runs after the render
    expect(() => unmount()).not.toThrow();
    expect(effectWarnings()).toEqual([]);
  });
});
