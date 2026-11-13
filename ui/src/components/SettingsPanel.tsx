import type { ActiveConfig } from "../api";

export interface Settings {
  mode: string; // empty: the server default
  k: number; // 0: the server default
  token: string;
}

const KEY = "codeatlas.settings";
const PRESETS = ["vector", "hybrid", "hybrid+graph", "hybrid+rerank", "hybrid+rerank+graph"];

export function loadSettings(): Settings {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return { mode: saved.mode ?? "", k: saved.k ?? 0, token: saved.token ?? "" };
  } catch {
    return { mode: "", k: 0, token: "" };
  }
}

export function saveSettings(s: Settings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    // storage may be unavailable (private mode); settings then last for this page only
  }
}

interface Props {
  config: ActiveConfig | null;
  settings: Settings;
  onChange: (s: Settings) => void;
  onClose: () => void;
}

function Row({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="row">
      <dt>{label}</dt>
      <dd>{value === null || value === undefined || value === "" ? "—" : String(value)}</dd>
    </div>
  );
}

export function SettingsPanel({ config, settings, onChange, onClose }: Props) {
  const presets = config && !PRESETS.includes(config.default_mode) ? [config.default_mode, ...PRESETS] : PRESETS;
  return (
    <div className="overlay" onClick={onClose}>
      <section className="settings" onClick={(e) => e.stopPropagation()} aria-label="Settings" data-testid="settings">
        <div className="viewer-head">
          <div className="viewer-title">Settings</div>
          <button className="icon-button" onClick={onClose} aria-label="Close settings">
            ×
          </button>
        </div>

        <h3>This session</h3>
        <label>
          Retrieval mode
          <select
            value={settings.mode}
            onChange={(e) => onChange({ ...settings, mode: e.target.value })}
            data-testid="mode-select"
          >
            <option value="">Server default{config ? ` (${config.default_mode})` : ""}</option>
            {presets.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label>
          Chunks retrieved (k)
          <input
            type="number"
            min={0}
            max={30}
            value={settings.k}
            onChange={(e) => onChange({ ...settings, k: Math.max(0, Math.min(30, Number(e.target.value) || 0)) })}
          />
          <span className="hint">0 uses the server default{config ? ` (${config.default_k})` : ""}</span>
        </label>
        <label>
          API token
          <input
            type="password"
            value={settings.token}
            placeholder={config?.auth_required ? "required by this server" : "not required"}
            onChange={(e) => onChange({ ...settings, token: e.target.value })}
            autoComplete="off"
          />
        </label>

        <h3>Active server setup</h3>
        {config ? (
          <dl className="setup">
            <Row label="Repository" value={config.repo} />
            <Row label="Default mode" value={`${config.default_mode} · k=${config.default_k}`} />
            <Row label="Chunker" value={config.chunker} />
            <Row label="Embeddings" value={config.embedder} />
            <Row label="Reranker" value={config.reranker ?? "off"} />
            <Row label="LLM" value={config.llm ?? "none (retrieval only)"} />
            <Row
              label="Refusal thresholds"
              value={`vector ${config.thresholds.vector ?? "—"} · rerank ${config.thresholds.rerank ?? "—"}`}
            />
            {Object.entries(config.options).map(([name, value]) => (
              <Row key={name} label={name.replace(/_/g, " ")} value={String(value)} />
            ))}
            <Row label="Version" value={config.version} />
          </dl>
        ) : (
          <p className="muted">Not connected to the API.</p>
        )}
      </section>
    </div>
  );
}
