// PendingChanges review bar: sits above the chat composer and shows the
// agent's changes for this session (like a code-review diff). Collapsed it
// shows the change count plus accept/reject-all actions; expanded it lists
// each change with a content preview and per-change accept/reject.

import { useState } from "react";
import type { AppliedToolCall } from "../types";
import {
  previewToolCallText,
  summarizeToolCall,
  toolCallDetail,
} from "../logic/toolSummary";

interface PendingChangesProps {
  changes: AppliedToolCall[];
  busy: boolean;
  onAccept: (change: AppliedToolCall) => void;
  onReject: (change: AppliedToolCall) => void;
  onAcceptAll: () => void;
  onRejectAll: () => void;
}

function changeKey(change: AppliedToolCall, index: number): string {
  return `${change.tool}-${JSON.stringify(change.arguments)}-${index}`;
}

export function PendingChanges({
  changes,
  busy,
  onAccept,
  onReject,
  onAcceptAll,
  onRejectAll,
}: PendingChangesProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  if (changes.length === 0) return null;

  const count = changes.length;
  return (
    <div className="pending-changes" data-testid="pending-changes">
      <div className="pending-changes__bar">
        <button
          type="button"
          className="pending-changes__toggle"
          onClick={() => setCollapsed((value) => !value)}
          aria-expanded={!collapsed}
          data-testid="pending-changes-toggle"
        >
          <span className="change-caret" aria-hidden="true">{collapsed ? "▸" : "▾"}</span>
          <span className="pending-changes__count">
            {count} pending change{count === 1 ? "" : "s"}
          </span>
        </button>
        <div className="pending-changes__actions">
          <button
            type="button"
            className="btn btn--primary btn--small"
            data-testid="pending-accept-all"
            disabled={busy}
            onClick={onAcceptAll}
          >
            Accept all
          </button>
          <button
            type="button"
            className="btn btn--danger btn--small"
            data-testid="pending-reject-all"
            disabled={busy}
            onClick={onRejectAll}
          >
            Reject all
          </button>
        </div>
      </div>
      {!collapsed && (
        <ul className="change-list pending-changes__list">
          {changes.map((change, index) => {
            const key = changeKey(change, index);
            const isOpen = expandedKey === key;
            const preview = previewToolCallText(change);
            const detailId = `pending-change-detail-${index}`;
            return (
              <li key={key} className="change-item">
                <button
                  type="button"
                  className="change-item__summary"
                  onClick={() => setExpandedKey(isOpen ? null : key)}
                  aria-expanded={isOpen}
                  aria-controls={detailId}
                >
                  <span className="change-tool">{change.tool}</span>
                  <span className="change-summary-text">{summarizeToolCall(change)}</span>
                  <span className="change-caret" aria-hidden="true">{isOpen ? "▾" : "▸"}</span>
                </button>
                {preview && <p className="change-preview">{preview}</p>}
                <div className="change-actions">
                  <button
                    type="button"
                    className="btn btn--primary btn--small"
                    data-testid={`pending-accept-${index}`}
                    disabled={busy}
                    onClick={() => onAccept(change)}
                  >
                    Accept
                  </button>
                  <button
                    type="button"
                    className="btn btn--danger btn--small"
                    data-testid={`pending-reject-${index}`}
                    disabled={busy}
                    onClick={() => onReject(change)}
                  >
                    Reject
                  </button>
                </div>
                {isOpen && (
                  <div className="change-item__detail" id={detailId}>
                    <pre className="change-detail">{toolCallDetail(change)}</pre>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
