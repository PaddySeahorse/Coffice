// Status bar strip (planning doc 9.1/9.2): Agent state + co commit count.

import type { AgentStatus } from "../types";

interface StatusBarProps {
  agentStatus: AgentStatus;
  commitCount: number;
}

const STATUS_LABEL: Record<AgentStatus, string> = {
  idle: "idle",
  working: "working",
  needs_confirmation: "needs confirmation",
  error: "error",
};

export function StatusBar({ agentStatus, commitCount }: StatusBarProps) {
  return (
    <footer className="statusbar" data-testid="statusbar">
      <span className="statusbar-item">
        <span className={`status-dot status-dot--${agentStatus}`} aria-hidden="true" />
        <span data-testid="statusbar-agent">Agent: {STATUS_LABEL[agentStatus]}</span>
      </span>
      <span className="statusbar-item" data-testid="statusbar-co">
        co: {commitCount} commit{commitCount === 1 ? "" : "s"}
      </span>
    </footer>
  );
}
