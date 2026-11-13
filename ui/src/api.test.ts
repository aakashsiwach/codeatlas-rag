import { describe, expect, it } from "vitest";
import { SseParser, citationSegments } from "./api";

describe("SseParser", () => {
  it("parses events split across chunks and CRLF line endings", () => {
    const p = new SseParser();
    expect(p.push('event: token\r\ndata: "Hel')).toEqual([]);
    expect(p.push('lo"\r\n\r\nevent: token\ndata: " world"\n\nevent: do')).toEqual([
      { event: "token", data: "Hello" },
      { event: "token", data: " world" },
    ]);
    expect(p.push('ne\ndata: {"refused": true}\n\n')).toEqual([{ event: "done", data: { refused: true } }]);
  });
});

describe("citationSegments", () => {
  it("turns [n], lists and ranges into citations but leaves inline code alone", () => {
    expect(citationSegments("Saved in `owners[1]` by save [2], see [3, 4] and [5-6].")).toEqual([
      { text: "Saved in `owners[1]` by save " },
      { cite: 2 },
      { text: ", see " },
      { cite: 3 },
      { cite: 4 },
      { text: " and " },
      { cite: 5 },
      { cite: 6 },
      { text: "." },
    ]);
  });
});
