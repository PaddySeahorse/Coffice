// Shared domain types for the Agent Deck. Payload shapes mirror the agent /
// MCP / versioning bead contracts so the UI stays aligned with the backend.

export type AgentStatus = "idle" | "working" | "needs_confirmation" | "error";

export type SectionId = "chat" | "context" | "tools" | "diff" | "history";

export interface PendingConfirmation {
  token: string;
  tool: string;
  summary: string;
  snapshotHash: string | null;
}

export interface ChatMessageItem {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  toolSummary?: string;
  pending?: PendingConfirmation;
  error?: boolean;
  timestamp: string;
}

/** One applied tool call from the agent's ``change_summary``. */
export interface AppliedToolCall {
  tool: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
  ok: boolean;
}

/** Response of POST /chat and POST /confirm (agent_api.ChatResult). */
export interface ChatResult {
  status: "complete" | "needs_confirmation" | "error";
  reply: string;
  change_summary: AppliedToolCall[];
  round_id: string | null;
  needs_confirmation: boolean;
  confirmation: PendingConfirmation | null;
  error: string | null;
  session_id?: string;
}

/** One SSE event from the streaming /chat endpoint (agent_api SSE channel). */
export interface ChatStreamEvent {
  event: "token" | "tool" | "needs_confirmation" | "done" | "error";
  data: Record<string, unknown>;
}

/** Tool descriptor from GET /tools (MCP tool introspection). */
export interface McpToolInfo {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

/** A ``co log`` commit (co_client.CommitInfo). */
export interface CommitInfo {
  hash: string;
  author: string;
  email: string;
  message: string;
  timestamp: string | null;
}

/** A changed zip-internal path from ``co diff`` (co_client.DiffEntry). */
export interface DiffEntry {
  status: "A" | "D" | "M";
  path: string;
}

/** Document context shown in the Context panel (getOutline/getSelection). */
export interface DocumentContext {
  docId: string;
  path: string | null;
  outline: Array<{
    start: number;
    end: number;
    level: number;
    style: string;
    text: string;
  }>;
  selection: string | null;
}

/** Arguments for the exportDoc tool, built by the Export dialog. */
export interface ExportParams {
  docId: string;
  includeCo: boolean;
  includeBundle: boolean;
  package: boolean;
  path: string | null;
}

/** Result of the exportDoc tool (tools_version.export_doc). */
export type ExportResult = {
  ok: boolean;
  path: string | null;
  bundlePath: string | null;
  packagePath: string | null;
  includeCo: boolean;
  warning: string;
  warnings: string[];
  error?: string;
};
