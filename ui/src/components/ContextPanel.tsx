// Context panel (planning doc 9.1/9.2): current document state (docId, path,
// outline headings, selection) so the human can see what the AI is working on.

import type { DocumentContext } from "../types";

interface ContextPanelProps {
  context: DocumentContext;
  busy: boolean;
  onRefresh: () => void;
}

export function ContextPanel({ context, busy, onRefresh }: ContextPanelProps) {
  return (
    <section className="panel" data-testid="context-panel">
      <div className="panel-scroll">
        <dl className="kv">
          <dt>Document ID</dt>
          <dd>{context.docId}</dd>
          <dt>Path</dt>
          <dd>{context.path ?? "(In-memory Document)"}</dd>
          <dt>Current Selection</dt>
          <dd>{context.selection ?? "(None)"}</dd>
        </dl>

        <div className="section-head">
          <h3 className="section-title">Outline</h3>
          <button
            type="button"
            className="btn btn--ghost btn--small"
            data-testid="refresh-context"
            disabled={busy}
            onClick={onRefresh}
          >
            Refresh
          </button>
        </div>
        {context.outline.length === 0 ? (
          <p className="empty-hint">No outline available.</p>
        ) : (
          <ul className="outline-list">
            {context.outline.map((heading, index) => (
              <li
                key={`${heading.text}-${index}`}
                className="outline-item"
                style={{ paddingLeft: `${Math.min(heading.level, 4) * 10}px` }}
              >
                <span className="outline-level">L{heading.level}</span>
                <span className="outline-text">{heading.text}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
