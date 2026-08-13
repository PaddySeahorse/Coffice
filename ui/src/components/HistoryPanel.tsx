// History panel (doc 9.2): co log timeline (hash, author, message, timestamp)
// with per-commit preview and a rollback button.

import { useState } from "react";
import type { CommitInfo, DiffEntry } from "../types";
import { commitLabel, formatTimestamp, sortCommitsNewestFirst } from "../logic/history";

interface HistoryPanelProps {
  commits: CommitInfo[];
  busy: boolean;
  onRollback: (hash: string) => void;
  onPreview: (commit: CommitInfo, previous: CommitInfo | null) => Promise<DiffEntry[]>;
}

export function HistoryPanel({ commits, busy, onRollback, onPreview }: HistoryPanelProps) {
  const [expandedHash, setExpandedHash] = useState<string | null>(null);
  const [preview, setPreview] = useState<Record<string, DiffEntry[] | string>>({});

  const toggle = async (commit: CommitInfo, index: number) => {
    const hash = commit.hash;
    if (expandedHash === hash) {
      setExpandedHash(null);
      return;
    }
    setExpandedHash(hash);
    if (!(hash in preview)) {
      const previous = index + 1 < commits.length ? commits[index + 1] : null;
      const entries = await onPreview(commit, previous);
      setPreview((current) => ({ ...current, [hash]: entries }));
    }
  };

  const sorted = sortCommitsNewestFirst(commits);

  return (
    <section className="panel" data-testid="history-panel">
      <div className="panel-scroll">
        <h3 className="section-title">Version History (co log)</h3>
        {sorted.length === 0 ? (
          <p className="empty-hint">No commits yet. Save the document and take a snapshot to start recording history.</p>
        ) : (
          <ol className="history-list">
            {sorted.map((commit, index) => {
              const hash = commit.hash;
              const isOpen = expandedHash === hash;
              const entry = preview[hash];
              return (
                <li key={hash} className="history-item">
                  <button
                    type="button"
                    className="history-item__summary"
                    onClick={() => void toggle(commit, index)}
                    aria-expanded={isOpen}
                    data-testid={`history-${commitLabel(commit).split(" · ")[0]}`}
                  >
                    <span className="history-hash">{commitLabel(commit)}</span>
                    <span className="history-message">{commit.message}</span>
                    <span className="history-time">{formatTimestamp(commit.timestamp)}</span>
                  </button>
                  {isOpen && (
                    <div className="history-item__detail">
                      {typeof entry === "string" ? (
                        <p className="history-preview-note">{entry}</p>
                      ) : entry && entry.length > 0 ? (
                        <ul className="history-diff">
                          {entry.map((diff, diffIndex) => (
                            <li key={`${diff.path}-${diffIndex}`} className={`history-diff--${diff.status.toLowerCase()}`}>
                              <span className="history-diff-status">{diff.status}</span>
                              {diff.path}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="history-preview-note">This commit has no file diffs.</p>
                      )}
                      <button
                        type="button"
                        className="btn btn--warning btn--small"
                        data-testid={`rollback-${commitLabel(commit).split(" · ")[0]}`}
                        disabled={busy}
                        onClick={() => onRollback(hash)}
                      >
                        Revert to this version
                      </button>
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </section>
  );
}
