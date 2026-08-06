// Human-readable summaries for agent tool calls (chat panel + diff panel),
// e.g. "insertText → 2 paragraphs".

import type { AppliedToolCall } from "../types";

export function countParagraphs(text: string): number {
  if (!text) return 0;
  return text.split("\n").filter((line) => line.trim().length > 0).length;
}

function rangeOf(args: Record<string, unknown>): { start: number; end: number } | null {
  const range = args.range;
  if (range && typeof range === "object") {
    const start = Number((range as Record<string, unknown>).start ?? 0);
    const end = Number((range as Record<string, unknown>).end ?? start);
    return { start, end };
  }
  return null;
}

/** A short "tool → outcome" line for one applied tool call. */
export function summarizeToolCall(change: AppliedToolCall): string {
  const args = change.arguments ?? {};
  const tool = change.tool || "tool";
  if (!change.ok) {
    return `${tool} → 失败`;
  }
  switch (tool) {
    case "insertText": {
      const paragraphs = countParagraphs(String(args.text ?? ""));
      return `${tool} → ${paragraphs} paragraph${paragraphs === 1 ? "" : "s"}`;
    }
    case "applyStyle": {
      const style = String(args.styleId ?? "style");
      return `${tool} → ${style}`;
    }
    case "replaceRange": {
      const range = rangeOf(args);
      const length = range ? Math.max(0, range.end - range.start) : 0;
      return `${tool} → ${length} chars`;
    }
    case "generateTable": {
      const rows = Number(args.rows ?? 0);
      const cols = Number(args.cols ?? 0);
      return `${tool} → ${rows}×${cols} table`;
    }
    case "refactorSection": {
      return `${tool} → section ${Number(args.sectionId ?? 0) + 1}`;
    }
    case "setPageLayout":
      return `${tool} → layout updated`;
    case "snapshot":
      return `${tool} → ${String(args.label ?? "snapshot")}`;
    case "rollback":
      return `${tool} → ${String(args.hash ?? "").slice(0, 7)}`;
    default:
      return `${tool} → ok`;
  }
}

/** Optional extra detail (arguments) shown when a change is expanded. */
export function toolCallDetail(change: AppliedToolCall): string {
  try {
    return JSON.stringify(change.arguments ?? {}, null, 2);
  } catch {
    return String(change.arguments);
  }
}
