// Chat channel to the agent HTTP facade (agent bead, planning doc 9.1).
//
// POST /chat  -- run one agent round (stream:true -> SSE token stream)
// POST /confirm -- confirm/reject a pending medium/high-risk change
// GET  /health -- liveness
//
// Every call falls back to mock data when the facade is unreachable so the
// UI stays usable standalone (make run-ui without the backend).

import { getApiConfig } from "./config";
import { mockChatReply } from "../mock/data";
import type { ChatResult, ChatStreamEvent } from "../types";

export interface ConfirmRequest {
  sessionId: string;
  token: string;
  action: "confirm" | "reject";
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const cfg = getApiConfig();
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), cfg.requestTimeoutMs);
  try {
    const response = await fetch(`${cfg.agentBaseUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`agent API ${path} -> HTTP ${response.status}`);
    }
    return (await response.json()) as T;
  } finally {
    window.clearTimeout(timer);
  }
}

/** Parse a streamed ``text/event-stream`` response, invoking ``onEvent`` per frame. */
async function readSse(
  response: Response,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  const reader = response.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    for (;;) {
      const separator = buffer.indexOf("\n\n");
      if (separator === -1) break;
      const frame = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      let name = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) {
        onEvent({
          event: name as ChatStreamEvent["event"],
          data: JSON.parse(data) as Record<string, unknown>,
        });
      }
    }
  }
}

function chatResultFromTerminal(data: Record<string, unknown>): ChatResult {
  return {
    status: (data.status as ChatResult["status"]) ?? "complete",
    reply: String(data.reply ?? ""),
    change_summary: Array.isArray(data.change_summary)
      ? (data.change_summary as ChatResult["change_summary"])
      : [],
    round_id: data.round_id != null ? String(data.round_id) : null,
    needs_confirmation: Boolean(data.needs_confirmation),
    confirmation: null,
    error: data.error != null ? String(data.error) : null,
    session_id: data.session_id != null ? String(data.session_id) : undefined,
  };
}

/**
 * Run one chat round over the streaming SSE channel. The assistant's natural
 * language streams via ``onEvent`` (token/tool/needs_confirmation), while
 * edits still apply atomically after each complete tool call. Resolves with
 * the final round result; falls back to a mock round when the API is down.
 */
export async function sendChatStream(
  message: string,
  sessionId: string | undefined,
  humanOperator: string | undefined,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<ChatResult> {
  const cfg = getApiConfig();
  try {
    const response = await fetch(`${cfg.agentBaseUrl}chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({
        message,
        session_id: sessionId,
        human_operator: humanOperator,
        stream: true,
      }),
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as {
        error?: string;
      } | null;
      throw new Error(payload?.error || `agent API chat -> HTTP ${response.status}`);
    }
    let final: ChatResult | null = null;
    await readSse(response, (event) => {
      if (event.event === "done" || event.event === "needs_confirmation") {
        final = chatResultFromTerminal(event.data);
      } else if (event.event === "error") {
        final = {
          status: "error",
          reply: "",
          change_summary: [],
          round_id: null,
          needs_confirmation: false,
          confirmation: null,
          error: String(event.data.error ?? "agent 处理出错"),
          session_id:
            event.data.session_id != null ? String(event.data.session_id) : undefined,
        };
      }
      onEvent(event);
    });
    if (final) return final;
    throw new Error("chat stream ended without a terminal event");
  } catch (err) {
    console.error("[agentApi] sendChatStream failed, falling back to mock:", err);
    const mock = mockChatReply(message, sessionId ?? "mock-session");
    for (const change of mock.change_summary) {
      onEvent({
        event: "tool",
        data: { tool: change.tool, arguments: change.arguments, result: change.result, ok: change.ok },
      });
    }
    onEvent({ event: "token", data: { text: mock.reply } });
    onEvent({
      event: "done",
      data: {
        status: mock.status,
        reply: mock.reply,
        change_summary: mock.change_summary,
        round_id: mock.round_id,
        session_id: mock.session_id,
      },
    });
    return mock;
  }
}

/** Non-streaming chat (kept for tests / callers that want one JSON response). */
export async function sendChat(
  message: string,
  sessionId?: string,
  humanOperator?: string,
): Promise<ChatResult> {
  try {
    return await postJson<ChatResult>("chat", {
      message,
      session_id: sessionId,
      human_operator: humanOperator,
    });
  } catch (err) {
    console.error("[agentApi] sendChat failed, falling back to mock:", err);
    return mockChatReply(message, sessionId ?? "mock-session");
  }
}

/**
 * Resolve a pending change over the streaming SSE channel (confirm/reject
 * runs governance confirmOp/rejectOp server-side, then the agent loop
 * resumes). Falls back to a mock result when the API is down.
 */
export async function sendConfirmationStream(
  request: ConfirmRequest,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<ChatResult> {
  const cfg = getApiConfig();
  try {
    const response = await fetch(`${cfg.agentBaseUrl}confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({
        session_id: request.sessionId,
        token: request.token,
        action: request.action,
        stream: true,
      }),
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as {
        error?: string;
      } | null;
      throw new Error(payload?.error || `agent API confirm -> HTTP ${response.status}`);
    }
    let final: ChatResult | null = null;
    await readSse(response, (event) => {
      if (event.event === "done" || event.event === "needs_confirmation") {
        final = chatResultFromTerminal(event.data);
      } else if (event.event === "error") {
        final = {
          status: "error",
          reply: "",
          change_summary: [],
          round_id: null,
          needs_confirmation: false,
          confirmation: null,
          error: String(event.data.error ?? "确认操作失败"),
          session_id:
            event.data.session_id != null ? String(event.data.session_id) : undefined,
        };
      }
      onEvent(event);
    });
    if (final) return final;
    throw new Error("confirm stream ended without a terminal event");
  } catch (err) {
    console.error("[agentApi] sendConfirmationStream failed, falling back to mock:", err);
    const mock: ChatResult = {
      status: "complete",
      reply:
        request.action === "confirm"
          ? "已确认该改动并继续执行。"
          : "已拒绝该改动，文档已回滚到操作前状态。",
      change_summary: [],
      round_id: "mock-round",
      needs_confirmation: false,
      confirmation: null,
      error: null,
      session_id: request.sessionId,
    };
    onEvent({ event: "token", data: { text: mock.reply } });
    onEvent({
      event: "done",
      data: {
        status: mock.status,
        reply: mock.reply,
        change_summary: mock.change_summary,
        round_id: mock.round_id,
        session_id: mock.session_id,
      },
    });
    return mock;
  }
}

/** Non-streaming confirm (kept for tests / callers that want one JSON response). */
export async function sendConfirmation(
  request: ConfirmRequest,
): Promise<ChatResult> {
  try {
    return await postJson<ChatResult>("confirm", {
      session_id: request.sessionId,
      token: request.token,
      action: request.action,
    });
  } catch (err) {
    console.error("[agentApi] sendConfirmation failed, falling back to mock:", err);
    return {
      status: "complete",
      reply:
        request.action === "confirm"
          ? "已确认该改动并继续执行。"
          : "已拒绝该改动，文档已回滚到操作前状态。",
      change_summary: [],
      round_id: "mock-round",
      needs_confirmation: false,
      confirmation: null,
      error: null,
      session_id: request.sessionId,
    };
  }
}

/** Liveness probe; false when the facade is not reachable. */
export async function checkHealth(): Promise<boolean> {
  const cfg = getApiConfig();
  try {
    const response = await fetch(`${cfg.agentBaseUrl}health`, {
      signal: AbortSignal.timeout(cfg.requestTimeoutMs),
    });
    return response.ok;
  } catch {
    return false;
  }
}
