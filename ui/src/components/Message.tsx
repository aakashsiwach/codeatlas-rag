import { Fragment, useEffect, useState } from "react";
import type { Turn } from "../App";
import { AnswerDone, Source, citationSegments, formatMs } from "../api";

// Typical retrieval time per setup (dev p50, CPU). Other setups get the plain loading dots, unless the API
// reports a retrieval still running after SLOW_MS.
const EXPECTED_MS: Record<string, number> = { quality: 30_000 };
const SLOW_MS = 5_000;

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

/** Shown while a slow setup retrieves: elapsed time against the typical time. */
function Progress({ setup, startedAt, expectedMs, server }: { setup: string; startedAt: number; expectedMs: number; server?: Turn["server"] }) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(performance.now()), 250);
    return () => clearInterval(timer);
  }, []);
  const elapsed = Math.max(0, now - startedAt, server?.elapsedMs ?? 0);
  const pct = Math.min(95, (elapsed / expectedMs) * 100);
  return (
    <div className="progress" role="status" data-testid="progress">
      <div className="progress-head">
        <strong>Retrieving with the {setup} setup…</strong>
        <span className="elapsed">{Math.floor(elapsed / 1000)} s</span>
      </div>
      <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
        <div className="progress-bar" style={{ width: `${pct}%` }} />
      </div>
      {server && server.elapsedMs > 0 && (
        <div className="muted" data-testid="server-progress">
          Server: still {server.stage} · {Math.floor(server.elapsedMs / 1000)} s
        </div>
      )}
      <div className="muted">
        Hybrid search, then the cross-encoder reranks the candidates and the code graph adds neighbours. Usually
        about {Math.round(expectedMs / 1000)} s on CPU{elapsed > expectedMs * 1.5 ? "; this one is taking longer" : ""}.
      </div>
    </div>
  );
}

/** setup · config · retrieval and total latency · confidence (· cost). */
function metaLine(turn: Turn, done: AnswerDone): string {
  const t = done.timings_ms ?? {};
  const retrieval = t.retrieval_ms ?? turn.retrievalMs;
  const parts = [done.setup ?? turn.setup, done.config];
  if (retrieval !== undefined) parts.push(`retrieval ${formatMs(retrieval)}`);
  if (t.total_ms !== undefined) parts.push(`total ${formatMs(t.total_ms)}`);
  parts.push(`confidence ${done.confidence.toFixed(2)} (${done.confidence_source})`);
  if (done.usage.cost_usd != null) parts.push(`$${done.usage.cost_usd.toFixed(4)}`);
  return parts.join(" · ");
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
          <div className="meta" data-testid="answer-meta">{metaLine(turn, done!)}</div>
        </div>
      ) : (
        turn.status !== "error" && (
          <div className="answer" data-testid="answer">
            {turn.text ? (
              <AnswerText text={turn.text} sources={turn.sources} onOpenSource={onOpenSource} />
            ) : turn.retrievalMs === undefined && (EXPECTED_MS[turn.setup] || (turn.server?.elapsedMs ?? 0) >= SLOW_MS) ? (
              <Progress
                setup={turn.setup}
                startedAt={turn.startedAt}
                expectedMs={EXPECTED_MS[turn.setup] ?? 30_000}
                server={turn.server}
              />
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
              <div className="meta" data-testid="answer-meta">
                {metaLine(turn, done)}
              </div>
            )}
          </div>
        )
      )}
    </article>
  );
}
