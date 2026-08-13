// Command palette (Cmd+K, planning doc 9.2): minimal quick actions —
// Revert to previous step / View History / Create Branch.

import { useEffect, useRef, useState } from "react";

export interface PaletteAction {
  id: string;
  label: string;
  hint: string;
  run: () => void;
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  actions: PaletteAction[];
}

export function CommandPalette({ open, onClose, actions }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  if (!open) return null;

  const filtered = actions.filter((action) =>
    action.label.toLowerCase().includes(query.trim().toLowerCase()),
  );

  const pick = (action: PaletteAction) => {
    action.run();
    onClose();
  };

  return (
    <div
      className="palette-overlay"
      data-testid="command-palette"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div className="palette" onClick={(event) => event.stopPropagation()}>
        <input
          ref={inputRef}
          className="palette-input"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Enter command..."
          aria-label="Command Search"
          onKeyDown={(event) => {
            if (event.key === "Escape") onClose();
            if (event.key === "Enter" && filtered[0]) pick(filtered[0]);
          }}
        />
        <ul className="palette-list">
          {filtered.map((action) => (
            <li key={action.id}>
              <button type="button" className="palette-item" onClick={() => pick(action)}>
                <span className="palette-label">{action.label}</span>
                <span className="palette-hint">{action.hint}</span>
              </button>
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="palette-empty">No matching commands</li>
          )}
        </ul>
      </div>
    </div>
  );
}
