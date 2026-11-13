import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Turn } from "../App";
import type { AnswerDone, Source } from "../api";
import { Message } from "./Message";

const source = (index: number, path: string): Source => ({
  index,
  chunk_id: `c${index}`,
  kind: "method",
  path,
  start_line: 10,
  end_line: 20,
  symbol: `x.Owner#m${index}`,
  score: 0.5,
  preview: null,
});

const done = (over: Partial<AnswerDone>): AnswerDone => ({
  answer: "",
  config: "hybrid",
  citations: [],
  sources: [],
  confidence: 0.7,
  confidence_source: "vector",
  refused: false,
  invalid_citations: [],
  truncated: false,
  usage: {},
  trace_id: null,
  ...over,
});

describe("Message", () => {
  it("renders citations as buttons that open their source, and flags unknown ones", () => {
    const onOpen = vi.fn();
    const sources = [source(1, "src/Owner.java"), source(2, "src/Pet.java")];
    const turn: Turn = {
      id: 1,
      question: "q",
      setup: "fast",
      startedAt: 0,
      status: "done",
      text: "Owners are saved [1] and pets too [2]; see [9].",
      sources,
      done: done({ answer: "x", invalid_citations: [9] }),
    };
    render(<Message turn={turn} selected={null} onOpenSource={onOpen} />);
    fireEvent.click(screen.getByTestId("cite-2"));
    expect(onOpen).toHaveBeenCalledWith(sources[1]);
    expect(screen.getByTitle("Not one of the retrieved blocks").textContent).toBe("9");
    expect(screen.getByText(/Cites blocks that were not retrieved: 9/)).toBeTruthy();
  });

  it("shows the don't-know state with the closest matches", () => {
    const turn: Turn = {
      id: 2,
      question: "How are Kafka offsets committed?",
      setup: "fast",
      startedAt: 0,
      status: "done",
      text: "I don't know based on the indexed code.",
      sources: [source(1, "src/Owner.java")],
      done: done({ refused: true, confidence: 0.12 }),
    };
    render(<Message turn={turn} selected={null} onOpenSource={() => {}} />);
    expect(screen.getByTestId("dont-know").textContent).toContain("0.12");
    expect(screen.getByText("Closest matches")).toBeTruthy();
  });

  it("marks a truncated answer", () => {
    const turn: Turn = { id: 3, question: "q", setup: "fast",
      startedAt: 0,
      status: "done", text: "Partial [1]", sources: [source(1, "a.java")], done: done({ truncated: true }) };
    render(<Message turn={turn} selected={null} onOpenSource={() => {}} />);
    expect(screen.getByText("Answer cut off at the output limit")).toBeTruthy();
  });
});
