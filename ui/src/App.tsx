import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ActiveConfig, AnswerDone, ApiError, Source, askStream, fetchConfig } from "./api";
import { Message } from "./components/Message";
import { SettingsPanel, Settings, loadSettings, saveSettings } from "./components/SettingsPanel";
import { SetupSwitch } from "./components/SetupSwitch";
import { SourceViewer } from "./components/SourceViewer";

export interface Turn {
  id: number;
  question: string;
  setup: string; // the named setup used, or "custom"
  startedAt: number; // performance.now() when asked
  retrievalMs?: number; // set when the sources arrive
  server?: { stage: string; elapsedMs: number }; // the latest progress event from the API
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

  // Block body: newer browsers return a Promise from scrollIntoView, and an effect may only return a cleanup.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const update = useCallback((id: number, patch: (t: Turn) => Turn) => {
    setTurns((all) => all.map((t) => (t.id === id ? patch(t) : t)));
  }, []);

  // A named setup unless Settings holds a custom mode/k (or the server offers no setups).
  const setupNames = Object.keys(config?.setups ?? {});
  const custom = Boolean(settings.mode || settings.k) || setupNames.length === 0;
  const setup = setupNames.includes(settings.setup) ? settings.setup : config?.default_setup ?? setupNames[0] ?? "";
  const active = custom ? undefined : config?.setups?.[setup];
  const mode = active?.mode ?? (settings.mode || config?.default_mode || "vector");
  const k = active?.k ?? (settings.k || config?.default_k || 5);
  const label = custom ? "custom" : setup;

  function changeSettings(s: Settings) {
    setSettings(s);
    saveSettings(s);
  }

  async function ask(question: string) {
    const id = nextId.current++;
    setTurns((all) => [
      ...all,
      { id, question, setup: label, startedAt: performance.now(), status: "streaming", text: "", sources: [] },
    ]);
    setBusy(true);
    abort.current = new AbortController();
    try {
      const target = custom ? { mode, k } : { setup };
      await askStream(question, { ...target, token: settings.token, signal: abort.current.signal }, (ev) => {
        if (ev.event === "progress")
          update(id, (t) => ({ ...t, server: { stage: ev.data.stage, elapsedMs: ev.data.elapsed_ms } }));
        else if (ev.event === "sources")
          update(id, (t) => ({
            ...t,
            sources: ev.data.sources,
            confidence: ev.data.confidence,
            confidenceSource: ev.data.confidence_source,
            retrievalMs: ev.data.retrieval_ms ?? performance.now() - t.startedAt,
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
          {label} · {mode} · k={k}
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
        <SetupSwitch
          setups={setupNames}
          value={custom ? null : setup}
          disabled={busy}
          onChange={(name) => changeSettings({ ...settings, setup: name, mode: "", k: 0 })}
        />
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
          onChange={changeSettings}
          onClose={() => setShowSettings(false)}
        />
      )}
    </div>
  );
}
