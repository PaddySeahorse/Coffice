// Unit tests for the settings API channel: the connection probe must use a
// long timeout (the backend /settings/test probe runs up to 20s) instead of
// the generic 3s request timeout, and AbortError must surface as a readable
// timeout message rather than a raw "Fetch is aborted".

import { afterEach, describe, expect, it, vi } from "vitest";
import { probeErrorMessage, testSettings } from "./settingsApi";
import type { LlmSettingsUpdate } from "../types";

const UPDATE: LlmSettingsUpdate = { base_url: "http://llm.test/v1", model: "m" };

describe("settingsApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("translates an aborted probe into a timeout message", () => {
    const err = new DOMException("Fetch is aborted", "AbortError");
    expect(probeErrorMessage(err)).toContain("超时");
    expect(probeErrorMessage(err)).toContain("25 秒");
  });

  it("keeps other error messages as-is", () => {
    expect(probeErrorMessage(new Error("HTTP 401"))).toBe("HTTP 401");
  });

  it("gives the connection test a long timeout instead of the generic 3s", async () => {
    const scheduled: number[] = [];
    vi.stubGlobal("setTimeout", ((_fn: () => void, ms: number) => {
      scheduled.push(ms);
      return 1;
    }) as unknown as typeof setTimeout);
    vi.stubGlobal("clearTimeout", (() => undefined) as unknown as typeof clearTimeout);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ ok: true, reply: "pong" }) })),
    );

    const result = await testSettings(UPDATE);
    expect(result).toEqual({ ok: true, reply: "pong" });
    expect(scheduled).toContain(25_000);
  });

  it("reports a timed-out probe as ok:false with the timeout message", async () => {
    const abortError = new DOMException("Fetch is aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw abortError;
    }));

    const result = await testSettings(UPDATE);
    expect(result.ok).toBe(false);
    expect(result.error).toContain("超时");
  });
});
