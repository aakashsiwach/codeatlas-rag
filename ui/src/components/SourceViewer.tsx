import { useEffect, useRef, useState } from "react";
import { FileSlice, Source, fetchFile } from "../api";

const CONTEXT = 6; // lines shown around the cited range

interface Props {
  source: Source;
  token: string;
  onClose: () => void;
}

export function SourceViewer({ source, token, onClose }: Props) {
  const [slice, setSlice] = useState<FileSlice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const firstHit = useRef<HTMLDivElement>(null);
  const isGraph = source.kind === "graph";

  useEffect(() => {
    setSlice(null);
    setError(null);
    if (isGraph) return;
    const start = Math.max(1, source.start_line - CONTEXT);
    fetchFile(source.path, start, source.end_line + CONTEXT, token)
      .then(setSlice)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [source, token, isGraph]);

  useEffect(() => firstHit.current?.scrollIntoView({ block: "center" }), [slice]);

  return (
    <aside className="viewer" aria-label="Source viewer" data-testid="source-viewer">
      <div className="viewer-head">
        <div>
          <div className="viewer-title">
            [{source.index}] {isGraph ? "Code graph analysis" : source.path}
          </div>
          {!isGraph && (
            <div className="muted">
              {source.symbol ?? source.kind} · lines {source.start_line}–{source.end_line}
            </div>
          )}
        </div>
        <button className="icon-button" onClick={onClose} aria-label="Close source viewer">
          ×
        </button>
      </div>
      <div className="code">
        {isGraph && <pre className="graph-preview">{source.preview}</pre>}
        {error && <div className="banner error">{error}</div>}
        {!isGraph && !slice && !error && <div className="muted pad">Loading…</div>}
        {slice?.lines.map((line, i) => {
          const n = slice.start_line + i;
          const hit = n >= source.start_line && n <= source.end_line;
          return (
            <div
              key={n}
              ref={hit && n === source.start_line ? firstHit : undefined}
              className={`line ${hit ? "hit" : ""}`}
              data-testid={hit ? "highlighted-line" : undefined}
            >
              <span className="ln">{n}</span>
              <code>{line || " "}</code>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
