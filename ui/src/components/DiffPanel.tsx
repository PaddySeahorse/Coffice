// Diff panel: per-session change summary. Each change is expandable and has
// accept/reject actions (governance confirmOp/rejectOp via POST /confirm when
// the change is still pending; otherwise a local accept/reject).

import { useState } from "react";
import type { AppliedToolCall } from "../types";
import { summarizeToolCall, toolCallDetail } from "../logic/toolSummary";

interface DiffPanelProps {
  changes: AppliedToolCall[];
  sessionId: string | null;
  onAccept: (change: AppliedToolCall) => void;
  onReject: (change: AppliedToolCall) => void;
  busy: boolean;
}

export function DiffPanel({ changes, sessionId, onAccept, onReject, busy }: DiffPanelProps) {
  const [expanded, setExpanded] = useState<number | null>(null);

  return (
    <section className="panel" data-testid="diff-panel">
      <div className="panel-scroll">
        <h3 className="section-title">Session Changes（{changes.length}）</h3>
        {changes.length === 0 ? (
          <p className="empty-hint">
            No changes yet. Send instructions to the AI in the Chat panel to see tool call summaries here.
          </p>
        ) : (
          <ul className="change-list">
            {changes.map((change, index) => {
              const isOpen = expanded === index;
              const summary = summarizeToolCall(change);
              return (
                <li key={`${change.tool}-${index}`} className="change-item">
                  <button
                    type="button"
                    className="change-item__summary"
                    onClick={() => setExpanded(isOpen ? null : index)}
                    aria-expanded={isOpen}
                  >
                    <span className="change-tool">{change.tool}</span>
                    <span className="change-summary-text">{summary}</span>
                    <span className="change-caret" aria-hidden="true">{isOpen ? "▾" : "▸"}</span>
                  </button>
                  {isOpen && (
                    <div className="change-item__detail">
                      <pre className="change-detail">{toolCallDetail(change)}</pre>
                      <div className="change-actions">
                        <button
                          type="button"
                          className="btn btn--primary btn--small"
                          data-testid={`accept-${index}`}
                          disabled={busy}
                          onClick={() => onAccept(change)}
                        >
                          Accept
                        </button>
                        <button
                          type="button"
                          className="btn btn--danger btn--small"
                          data-testid={`reject-${index}`}
                          disabled={busy}
                          onClick={() => onReject(change)}
                        >
                          Reject
                        </button>
                      </div>
                      {sessionId && (
                        <p className="change-session">Session: {sessionId}</p>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
