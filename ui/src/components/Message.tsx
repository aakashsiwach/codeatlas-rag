import { Fragment } from "react";
import type { Turn } from "../App";
import { Source, citationSegments } from "../api";

interface Props {
  turn: Turn;
  selected: Source | null;
  onOpenSource: (source: Source) => void;
}

function sourceLabel(s: Source): string {
  if (s.kind === "graph") return "code graph analysis";
  const file = s.path.split("/").pop() ?? s.path;
  const where = `${file}:${s.start_line}-${s.end_line}`;
  const symbol = s.symbol ? s.symbol.split(".").pop() : null;
  return symbol ? `${symbol} · ${where}` : where;
}

/** Answer text with citation markers turned into buttons that open the cited source. */
function AnswerText({ text, sources, onOpenSource }: { text: string; sources: Source[]; onOpenSource: Props["onOpenSource"] }) {
  const byIndex = new Map(sources.map((s) => [s.index, s]));
  return (
    <div className="answer-text">
      {text.split(/\n{2,}/).map((para, p) => (
        <p key={p}>
          {citationSegments(para).map((seg, i) => {
            if ("text" in seg) return <Fragment key={i}>{seg.text}</Fragment>;
            const src = byIndex.get(seg.cite);
            return src ? (
              <button
                key={i}
                className="cite"
                onClick={() => onOpenSource(src)}
                title={sourceLabel(src)}
                data-testid={`cite-${seg.cite}`}
              >
                {seg.cite}
              </button>
            ) : (
              <span key={i} className="cite invalid" title="Not one of the retrieved blocks">
                {seg.cite}
              </span>
            );
          })}
        </p>
      ))}
    </div>
  );
}

function SourceList({ sources, selected, onOpenSource, title }: { sources: Source[]; selected: Source | null; onOpenSource: Props["onOpenSource"]; title: string }) {
  if (!sources.length) return null;
  return (
    <div className="sources">
      <div className="sources-title">{title}</div>
      <ol>
        {sources.map((s) => (
          <li key={s.chunk_id}>
            <button
              className={`source ${selected?.chunk_id === s.chunk_id ? "active" : ""}`}
              onClick={() => onOpenSource(s)}
              data-testid={`source-${s.index}`}
            >
              <span className="num">{s.index}</span>
              <span className="label">{sourceLabel(s)}</span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function Message({ turn, selected, onOpenSource }: Props) {
  const done = turn.done;
  const refused = done?.refused ?? false;
  return (
    <article className="turn" data-testid="turn">
      <div className="question">{turn.question}</div>

      {turn.status === "error" && (
        <div className="answer error" role="alert" data-testid="answer-error">
          <strong>Something went wrong.</strong> {turn.error}
        </div>
      )}

      {refused ? (
        <div className="answer dont-know" data-testid="dont-know">
          <div className="dont-know-head">
            <span className="icon" aria-hidden>
              ?
            </span>
            <div>
              <strong>I don't know based on the indexed code.</strong>
              <div className="muted">
                Retrieval confidence {done!.confidence.toFixed(2)} ({done!.confidence_source}) was too low for a grounded answer.
              </div>
            </div>
          </div>
          <SourceList sources={turn.sources.filter((s) => s.kind !== "graph").slice(0, 3)} selected={selected} onOpenSource={onOpenSource} title="Closest matches" />
        </div>
      ) : (
        turn.status !== "error" && (
          <div className="answer" data-testid="answer">
            {turn.text ? (
              <AnswerText text={turn.text} sources={turn.sources} onOpenSource={onOpenSource} />
            ) : (
              <div className="thinking" aria-label="Retrieving">
                <span />
                <span />
                <span />
              </div>
            )}
            {turn.status === "streaming" && turn.text && <span className="caret" aria-hidden />}
            {done?.truncated && <div className="badge warn">Answer cut off at the output limit</div>}
            {done && done.invalid_citations.length > 0 && (
              <div className="badge warn">Cites blocks that were not retrieved: {done.invalid_citations.join(", ")}</div>
            )}
            <SourceList sources={turn.sources} selected={selected} onOpenSource={onOpenSource} title="Sources" />
            {done && (
              <div className="meta">
                {done.config} · confidence {done.confidence.toFixed(2)} ({done.confidence_source})
                {done.usage.cost_usd != null ? ` · $${done.usage.cost_usd.toFixed(4)}` : ""}
              </div>
            )}
          </div>
        )
      )}
    </article>
  );
}
