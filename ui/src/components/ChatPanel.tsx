// Chat panel (planning doc 9.1): conversation with the AI over the agent HTTP
// facade (POST /chat, /confirm). Non-streaming MVP: renders assistant replies
// plus tool-call summaries, and a confirm/reject prompt for pending changes.

import { useState } from "react";
import type { ChatMessageItem, PendingConfirmation } from "../types";

interface ChatPanelProps {
  messages: ChatMessageItem[];
  busy: boolean;
  onSend: (message: string) => void;
  onConfirm: (pending: PendingConfirmation, action: "confirm" | "reject") => void;
}

export function ChatPanel({ messages, busy, onSend, onConfirm }: ChatPanelProps) {
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
      <div className="panel-scroll">
        {messages.length === 0 ? (
          <p className="empty-hint">
            向 AI 发送消息开始协作。AI 将读取文档并调用工具进行修改。
          </p>
        ) : (
          messages.map((message) => (
            <article
              key={message.id}
              className={`chat-message chat-message--${message.role}${
                message.error ? " chat-message--error" : ""
              }`}
            >
              <header className="chat-message__meta">
                {message.role === "user"
                  ? "You"
                  : message.role === "assistant"
                    ? "AI"
                    : "System"}
              </header>
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
                    确认
                  </button>
                  <button
                    type="button"
                    className="btn btn--danger btn--small"
                    data-testid="btn-reject"
                    onClick={() => onConfirm(message.pending!, "reject")}
                  >
                    拒绝
                  </button>
                </div>
              )}
            </article>
          ))
        )}
        {busy && (
          <p className="chat-typing" data-testid="chat-typing">
            Agent 正在处理…
          </p>
        )}
      </div>
      <form className="chat-composer" onSubmit={submit}>
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="输入指令，例如：在文档末尾添加总结段落"
          aria-label="消息输入"
          disabled={busy}
        />
        <button type="submit" className="btn btn--primary" disabled={busy || !draft.trim()}>
          发送
        </button>
      </form>
    </section>
  );
}
