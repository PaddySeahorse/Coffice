// LLM settings channel to the agent HTTP facade (Settings panel).
//
// GET  /settings        -- current endpoint config (masked API key)
// POST /settings        -- apply + persist a base_url/model/api_key change
// POST /settings/test   -- ping the candidate endpoint with a one-token probe
//
// Reads fall back to defaults when the facade is unreachable (standalone UI);
// saves surface failures to the user instead of silently mocking success.

import { getApiConfig } from "./config";
import type { LlmSettings, LlmSettingsUpdate, SettingsTestResult } from "../types";

export const DEFAULT_LLM_SETTINGS: LlmSettings = {
  base_url: "http://127.0.0.1:11434/v1",
  model: "qwen2.5:7b",
  api_key: null,
  api_key_set: false,
};

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

/** Current LLM endpoint config; falls back to defaults when offline. */
export async function fetchSettings(): Promise<LlmSettings> {
  try {
    const cfg = getApiConfig();
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), cfg.requestTimeoutMs);
    try {
      const response = await fetch(`${cfg.agentBaseUrl}settings`, {
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`agent API settings -> HTTP ${response.status}`);
      const payload = (await response.json()) as { settings?: LlmSettings };
      return payload.settings ?? DEFAULT_LLM_SETTINGS;
    } finally {
      window.clearTimeout(timer);
    }
  } catch {
    return DEFAULT_LLM_SETTINGS;
  }
}

/**
 * Apply + persist an endpoint change. The API key is only sent when the user
 * actually typed one (blank keeps the stored key); an explicit empty string
 * would clear it. Throws on failure so the panel can show the error.
 */
export async function saveSettings(update: LlmSettingsUpdate): Promise<LlmSettings> {
  const body: Record<string, string> = {
    base_url: update.base_url,
    model: update.model,
  };
  if (update.api_key) body.api_key = update.api_key;
  const payload = await postJson<{ settings: LlmSettings }>("settings", body);
  return payload.settings;
}

/** Ping the candidate endpoint; resolves with the probe result, never throws. */
export async function testSettings(
  update: LlmSettingsUpdate,
): Promise<SettingsTestResult> {
  const body: Record<string, string> = {
    base_url: update.base_url,
    model: update.model,
  };
  if (update.api_key) body.api_key = update.api_key;
  try {
    return await postJson<SettingsTestResult>("settings/test", body);
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
