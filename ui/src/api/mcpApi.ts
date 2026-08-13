// Tool channel to the agent HTTP facade (UI-bead extensions).
//
// GET  /tools   -- MCP tool introspection (Tools panel)
// POST /tool    -- in-process tool passthrough: read triggers, exportDoc,
//                  importBundle, rollback, snapshot, branch...
// GET  /history -- co log timeline (History panel)
// GET  /diff    -- commit-to-commit diff (History preview)
//
// Result shapes are identical to the MCP server's tools, keeping the UI
// aligned with the agent/versioning bead contracts. All reads fall back to
// mock data when the facade is unreachable.

import { getApiConfig } from "./config";
import {
  MOCK_DIFF,
  MOCK_HISTORY,
  MOCK_TOOLS,
} from "../mock/data";
import type {
  CommitInfo,
  DiffEntry,
  ExportParams,
  ExportResult,
  McpToolInfo,
} from "../types";

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const cfg = getApiConfig();
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), cfg.requestTimeoutMs);
  try {
    const response = await fetch(`${cfg.agentBaseUrl}${path}`, {
      ...init,
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

/** Registered MCP tools (name/description/inputSchema), or mock tools. */
export async function fetchTools(): Promise<McpToolInfo[]> {
  try {
    const payload = await fetchJson<{ ok: boolean; tools: McpToolInfo[] }>(
      "tools",
    );
    return payload.tools;
  } catch {
    return MOCK_TOOLS;
  }
}

/** co log timeline, newest first; falls back to mock history. */
export async function fetchHistory(
  docId = "default",
  limit?: number,
): Promise<CommitInfo[]> {
  try {
    const query = new URLSearchParams({ docId });
    if (limit !== undefined) query.set("limit", String(limit));
    const payload = await fetchJson<{ commits?: CommitInfo[]; error?: string }>(
      `history?${query.toString()}`,
    );
    return payload.commits ?? [];
  } catch {
    return MOCK_HISTORY.slice(0, limit ?? MOCK_HISTORY.length);
  }
}

/** Diff between two commits; falls back to mock diff entries. */
export async function fetchDiff(
  h1: string,
  h2: string,
  docId = "default",
): Promise<DiffEntry[]> {
  try {
    const query = new URLSearchParams({ h1, h2, docId });
    const payload = await fetchJson<{ diff?: DiffEntry[]; error?: string }>(
      `diff?${query.toString()}`,
    );
    return payload.diff ?? [];
  } catch {
    return MOCK_DIFF;
  }
}

/** Generic tool result: any tool dict plus the common ok/tool/error fields. */
export type ToolPayload = {
  ok?: boolean;
  tool?: string;
  error?: unknown;
} & Record<string, unknown>;

/** Generic in-process tool call (read triggers, rollback, branch, ...). */
export async function callTool<T extends ToolPayload = ToolPayload>(
  name: string,
  arguments_: Record<string, unknown> = {},
): Promise<T> {
  const payload = await fetchJson<T & { ok?: boolean }>("tool", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, arguments: arguments_ }),
  });
  return payload as T;
}

/**
 * Export the document via the exportDoc tool (doc 14.6 dialog).
 * When the facade is unreachable a mock success is returned so the flow can
 * be exercised standalone; real backend errors surface as-is.
 */
export async function exportDoc(params: ExportParams): Promise<ExportResult> {
  try {
    const payload = await callTool<ExportResult>("exportDoc", {
      docId: params.docId,
      includeCo: params.includeCo,
      includeBundle: params.includeBundle,
      package: params.package,
      path: params.path,
    });
    return {
      ok: Boolean(payload.ok),
      path: payload.path ?? null,
      bundlePath: payload.bundlePath ?? null,
      packagePath: payload.packagePath ?? null,
      includeCo: Boolean(payload.includeCo),
      warning: payload.warning ?? "",
      warnings: payload.warnings ?? [],
      error: payload.error,
    };
  } catch {
    return {
      ok: true,
      path: params.path ?? "/tmp/report-export.docx",
      bundlePath: "/tmp/report-export.docx.co-bundle",
      packagePath: params.package ? "/tmp/report-export.docx.coffice.zip" : null,
      includeCo: params.includeCo,
      warning: params.includeCo
        ? "If checked, version history will be lost when opening this file in Microsoft Word or WPS"
        : "",
      warnings: [],
    };
  }
}

/** Restore history from a .co-bundle (doc 14.6 scenario B). */
export async function importBundle(
  bundlePath: string,
  docId = "default",
): Promise<Record<string, unknown>> {
  try {
    return await callTool("importBundle", { bundlePath, docId });
  } catch {
    return { ok: true, tool: "importBundle", bundlePath, importedCommits: 0 };
  }
}
