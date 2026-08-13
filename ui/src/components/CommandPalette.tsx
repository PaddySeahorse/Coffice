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
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const paletteRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  // Keep activeIndex within the filtered range when the list shrinks.
  const filtered = actions.filter((action) =>
    action.label.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const safeIndex = filtered.length === 0 ? 0 : Math.min(activeIndex, filtered.length - 1);

  if (!open) return null;

  const pick = (action: PaletteAction) => {
    action.run();
    onClose();
  };

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      onClose();
      return;
    }
    if (filtered.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((current) => (current + 1) % filtered.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) => (current - 1 + filtered.length) % filtered.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(filtered.length - 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const choice = filtered[safeIndex];
      if (choice) pick(choice);
    }
  };

  const onPaletteKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    // Focus trap: cycle Tab within the palette.
    if (event.key !== "Tab") return;
    const focusable = paletteRef.current?.querySelectorAll<HTMLElement>(
      'button, [href], input, [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const activeId = filtered[safeIndex] ? `palette-item-${filtered[safeIndex].id}` : undefined;

  return (
    <div
      className="palette-overlay"
      data-testid="command-palette"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      onClick={onClose}
    >
      <div
        className="palette"
        ref={paletteRef}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={onPaletteKeyDown}
      >
        <input
          ref={inputRef}
          className="palette-input"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Enter command…"
          aria-label="Command Search"
          role="combobox"
          aria-expanded={true}
          aria-controls="palette-listbox"
          aria-autocomplete="list"
          aria-activedescendant={activeId}
          onKeyDown={onInputKeyDown}
        />
        <ul className="palette-list" role="listbox" id="palette-listbox" aria-label="Commands">
          {filtered.map((action, index) => (
            <li key={action.id} role="presentation">
              <button
                type="button"
                className="palette-item"
                role="option"
                id={`palette-item-${action.id}`}
                aria-selected={index === safeIndex}
                onClick={() => pick(action)}
                onMouseEnter={() => setActiveIndex(index)}
              >
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
