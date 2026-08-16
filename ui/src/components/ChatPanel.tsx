// Chat panel (planning doc 9.1): conversation with the AI over the agent HTTP
// facade (POST /chat with stream:true -> SSE tokens, POST /confirm). Renders
// assistant replies as they stream in, tool-call summaries, and a confirm/
// reject prompt for pending changes.

import { useState } from "react";
import type { AppliedToolCall, ChatMessageItem, PendingConfirmation } from "../types";
import { PendingChanges } from "./PendingChanges";

interface ChatPanelProps {
  messages: ChatMessageItem[];
  busy: boolean;
  onSend: (message: string) => void;
  onConfirm: (pending: PendingConfirmation, action: "confirm" | "reject") => void;
  changes: AppliedToolCall[];
  onAcceptChange: (change: AppliedToolCall) => void;
  onRejectChange: (change: AppliedToolCall) => void;
  onAcceptAll: () => void;
  onRejectAll: () => void;
}

export function ChatPanel({
  messages,
  busy,
  onSend,
  onConfirm,
  changes,
  onAcceptChange,
  onRejectChange,
  onAcceptAll,
  onRejectAll,
}: ChatPanelProps) {
  const [draft, setDraft] = useState("");

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    onSend(text);
  };

  return (
    <section className="panel chat-panel" data-testid="chat-panel">
      <div className="panel-scroll" role="log" aria-live="polite" aria-label="Chat transcript">
        {messages.length === 0 ? (
          <p className="empty-hint">
            Send a message to the AI to start collaborating. The AI will read the document and call tools to make modifications.
          </p>
        ) : (
          messages.map((message) => (
            <article
              key={message.id}
              className={`chat-message chat-message--${message.role}${
                message.error ? " chat-message--error" : ""
              }`}
            >
              {message.role !== "user" && (
                <header className="chat-message__meta">
                  {message.role === "assistant"
                      ? "AI"
                      : message.role === "tool"
                        ? "Tool"
                        : "System"}
                </header>
              )}
              <div className="chat-message__content">{message.content}</div>
              {message.toolSummary && (
                <span className="tool-chip">{message.toolSummary}</span>
              )}
              {message.pending && (
                <div className="confirm-row" data-testid="confirm-row">
                  <span className="confirm-summary">{message.pending.summary}</span>
                  <button
                    type="button"
                    className="btn btn--primary btn--small"
                    data-testid="btn-confirm"
                    onClick={() => onConfirm(message.pending!, "confirm")}
                  >
                    Confirm
                  </button>
                  <button
                    type="button"
                    className="btn btn--danger btn--small"
                    data-testid="btn-reject"
                    onClick={() => onConfirm(message.pending!, "reject")}
                  >
                    Reject
                  </button>
                </div>
              )}
            </article>
          ))
        )}
        {busy && (
          <p className="chat-typing" data-testid="chat-typing" aria-live="polite">
            Agent is processing…
          </p>
        )}
      </div>
      <PendingChanges
        changes={changes}
        busy={busy}
        onAccept={onAcceptChange}
        onReject={onRejectChange}
        onAcceptAll={onAcceptAll}
        onRejectAll={onRejectAll}
      />
      <form className="chat-composer" onSubmit={submit}>
        <input
          type="text"
          name="chat-message"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Enter instructions, e.g., add a summary paragraph at the end of the document…"
          aria-label="Message Input"
          autoComplete="off"
          spellCheck={false}
          disabled={busy}
        />
        <button type="submit" className="btn btn--primary" disabled={busy || !draft.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
