interface Props {
  setups: string[];
  value: string | null; // null: a custom mode from Settings is active
  disabled: boolean;
  onChange: (setup: string) => void;
}

const HINTS: Record<string, string> = {
  fast: "Vector search · about 30 ms",
  quality: "Hybrid + reranker + code graph · about 30 s on CPU",
};

/** Per-question choice between the named setups (fast | quality). */
export function SetupSwitch({ setups, value, disabled, onChange }: Props) {
  if (!setups.length) return null;
  return (
    <div className="setup-switch" role="radiogroup" aria-label="Retrieval setup">
      {setups.map((name) => (
        <button
          key={name}
          type="button"
          role="radio"
          aria-checked={value === name}
          className={value === name ? "on" : ""}
          disabled={disabled}
          title={HINTS[name] ?? name}
          onClick={() => onChange(name)}
          data-testid={`setup-${name}`}
        >
          {name}
        </button>
      ))}
    </div>
  );
}
