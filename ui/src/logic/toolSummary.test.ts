// Unit tests for tool-call summaries ("insertText → 2 paragraphs", doc 9.1).

import { describe, expect, it } from "vitest";
import { countParagraphs, summarizeToolCall } from "./toolSummary";
import type { AppliedToolCall } from "../types";

describe("countParagraphs", () => {
  it("counts non-empty lines as paragraphs", () => {
    expect(countParagraphs("a\n\nb\nc")).toBe(3);
    expect(countParagraphs("")).toBe(0);
    expect(countParagraphs("single")).toBe(1);
  });
});

describe("summarizeToolCall", () => {
  it('renders "insertText → N paragraphs"', () => {
    const change: AppliedToolCall = {
      tool: "insertText",
      arguments: { pos: "end", text: "First paragraph.\nSecond paragraph." },
      result: { ok: true },
      ok: true,
    };
    expect(summarizeToolCall(change)).toBe("insertText → 2 paragraphs");
  });

  it("marks failed calls", () => {
    const change: AppliedToolCall = {
      tool: "insertText",
      arguments: {},
      result: { ok: false, error: "boom" },
      ok: false,
    };
    expect(summarizeToolCall(change)).toBe("insertText → failed");
  });

  it("summarizes other tools without crashing", () => {
    const change: AppliedToolCall = {
      tool: "rollback",
      arguments: { hash: "9f3a1c7e" },
      result: { ok: true },
      ok: true,
    };
    expect(summarizeToolCall(change)).toBe("rollback → 9f3a1c7");
  });
});
