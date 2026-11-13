import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ActiveConfig, AnswerDone, ApiError, Source, askStream, fetchConfig } from "./api";
import { Message } from "./components/Message";
import { SettingsPanel, Settings, loadSettings, saveSettings } from "./components/SettingsPanel";
import { SourceViewer } from "./components/SourceViewer";

export interface Turn {
  id: number;
  question: string;
  status: "streaming" | "done" | "error";
  text: string;
  sources: Source[];
  confidence?: number;
  confidenceSource?: string;
  done?: AnswerDone;
  error?: string;
}

const EXAMPLES = [
  "How does searching for owners by last name work?",
  "What breaks if I change the Owner entity?",
  "Which handlers redirect to the /owners/{ownerId} page?",
];

export default function App() {
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const [config, setConfig] = useState<ActiveConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [selected, setSelected] = useState<Source | null>(null);
  const nextId = useRef(1);
  const bottom = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    fetchConfig(settings.token)
      .then((c) => {
        setConfig(c);
        setConfigError(null);
      })
      .catch((e: unknown) =>
        setConfigError(e instanceof ApiError && e.status === 401 ? "API token required (open Settings)" : String(e)),
      );
  }, [settings.token]);

  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [turns]);

  const update = useCallback((id: number, patch: (t: Turn) => Turn) => {
    setTurns((all) => all.map((t) => (t.id === id ? patch(t) : t)));
  }, []);

  const mode = settings.mode || config?.default_mode || "hybrid";
  const k = settings.k || config?.default_k || 8;

  async function ask(question: string) {
    const id = nextId.current++;
    setTurns((all) => [...all, { id, question, status: "streaming", text: "", sources: [] }]);
    setBusy(true);
    abort.current = new AbortController();
    try {
      await askStream(question, { mode, k, token: settings.token, signal: abort.current.signal }, (ev) => {
        if (ev.event === "sources")
          update(id, (t) => ({
            ...t,
            sources: ev.data.sources,
            confidence: ev.data.confidence,
            confidenceSource: ev.data.confidence_source,
          }));
        else if (ev.event === "token") update(id, (t) => ({ ...t, text: t.text + ev.data }));
        else if (ev.event === "done") update(id, (t) => ({ ...t, status: "done", text: ev.data.answer, done: ev.data }));
        else if (ev.event === "error") update(id, (t) => ({ ...t, status: "error", error: ev.data.detail }));
      });
      update(id, (t) => (t.status === "streaming" ? { ...t, status: "error", error: "The stream ended early" } : t));
    } catch (e) {
      const message = e instanceof DOMException && e.name === "AbortError" ? "Stopped" : String(e instanceof Error ? e.message : e);
      update(id, (t) => ({ ...t, status: "error", error: message }));
    } finally {
      setBusy(false);
      abort.current = null;
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const q = input.trim();
    if (!q || busy) return;
    setInput("");
    void ask(q);
  }

  return (
    <div className={`app ${selected ? "with-viewer" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>
            ◆
          </span>
          CodeAtlas
          {config?.repo && <span className="repo">{config.repo}</span>}
        </div>
        <button className="setup-chip" onClick={() => setShowSettings(true)} data-testid="open-settings">
          <span className="dot" aria-hidden />
          {mode} · k={k}
        </button>
      </header>

      <main className="chat" aria-live="polite">
        {configError && <div className="banner error">{configError}</div>}
        {turns.length === 0 && (
          <section className="empty">
            <h1>Ask about the indexed codebase</h1>
            <p>Answers cite the exact files and lines they use. Click a citation to open the source.</p>
            <div className="examples">
              {EXAMPLES.map((q) => (
                <button key={q} onClick={() => void ask(q)} disabled={busy}>
                  {q}
                </button>
              ))}
            </div>
          </section>
        )}
        {turns.map((t) => (
          <Message key={t.id} turn={t} onOpenSource={setSelected} selected={selected} />
        ))}
        <div ref={bottom} />
      </main>

      <form className="composer" onSubmit={onSubmit}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) onSubmit(e);
          }}
          placeholder="Ask a question about the code…"
          rows={1}
          aria-label="Question"
          data-testid="question"
        />
        {busy ? (
          <button type="button" className="secondary" onClick={() => abort.current?.abort()}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!input.trim()} data-testid="send">
            Ask
          </button>
        )}
      </form>

      {selected && <SourceViewer source={selected} token={settings.token} onClose={() => setSelected(null)} />}
      {showSettings && (
        <SettingsPanel
          config={config}
          settings={settings}
          onChange={(s) => {
            setSettings(s);
            saveSettings(s);
          }}
          onClose={() => setShowSettings(false)}
        />
      )}
    </div>
  );
}
