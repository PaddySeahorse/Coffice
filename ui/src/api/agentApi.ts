// Chat channel to the agent HTTP facade (agent bead, planning doc 9.1).
//
// POST /chat  -- run one agent round
// POST /confirm -- confirm/reject a pending medium/high-risk change
// GET  /health -- liveness
//
// Every call falls back to mock data when the facade is unreachable so the
// UI stays usable standalone (make run-ui without the backend).

import { getApiConfig } from "./config";
import { mockChatReply } from "../mock/data";
import type { ChatResult } from "../types";

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

/** Run one chat round. Falls back to a mock round when the API is down. */
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
 * Resolve a pending change (governance confirmOp/rejectOp run server-side).
 * Falls back to a mock complete result when the API is down.
 */
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
