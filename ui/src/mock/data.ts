// Mock data so the Agent Deck renders standalone (npm run dev / make run-ui)
// before the agent API + MCP server are up. Every API client falls back here.

import type {
  AppliedToolCall,
  ChatResult,
  CommitInfo,
  DiffEntry,
  DocumentContext,
  McpToolInfo,
} from "../types";

export const MOCK_TOOLS: McpToolInfo[] = [
  {
    name: "getOutline",
    description:
      "Return the document outline: headings with character offsets (start/end into the full text), level, paragraph style, and text. Use this first to understand the document structure.",
    inputSchema: {
      type: "object",
      properties: { docId: { type: "string" } },
    },
  },
  {
    name: "getSelection",
    description:
      "Return the text and style of a character range ({start, end} offsets into the full text); omit range for the whole document.",
    inputSchema: {
      type: "object",
      properties: {
        docId: { type: "string" },
        range: { type: "object", properties: { start: { type: "integer" }, end: { type: "integer" } } },
      },
    },
  },
  {
    name: "getStyles",
    description:
      "Return the available paragraph and character style names of the document, for use as styleId in edits.",
    inputSchema: { type: "object", properties: { docId: { type: "string" } } },
  },
  {
    name: "getTables",
    description:
      "Return all tables in the document: name, row/column counts, and cell contents (cells[row][col]).",
    inputSchema: { type: "object", properties: { docId: { type: "string" } } },
  },
  {
    name: "getRedlines",
    description:
      "Return the tracked-changes (redline) list: id, type, author, timestamp, text, comment.",
    inputSchema: { type: "object", properties: { docId: { type: "string" } } },
  },
  {
    name: "getHistory",
    description:
      "Return the co version history of the document as a commit list (hash, author, message, timestamp), newest first; limit caps the number of commits.",
    inputSchema: {
      type: "object",
      properties: { docId: { type: "string" }, limit: { type: "integer" } },
    },
  },
  {
    name: "getDiff",
    description:
      "Return the diff between two commit hashes (h1, h2) as changed zip-internal paths (e.g. word/document.xml) with A/D/M status.",
    inputSchema: {
      type: "object",
      properties: {
        h1: { type: "string" },
        h2: { type: "string" },
        docId: { type: "string" },
      },
      required: ["h1", "h2"],
    },
  },
  {
    name: "insertText",
    description:
      "Insert text at a character offset into the full document text (pos may be an integer offset or the string 'end'). Optional style is a paragraph style name. Low risk: runs immediately with track changes on.",
    inputSchema: {
      type: "object",
      properties: {
        docId: { type: "string" },
        pos: { type: ["integer", "string"] },
        text: { type: "string" },
        style: { type: "string" },
      },
    },
  },
  {
    name: "replaceRange",
    description:
      "Replace the text in a range ({start, end} character offsets) with new text. Deleting more than 50 characters is medium risk and returns needs_confirmation.",
    inputSchema: {
      type: "object",
      properties: {
        docId: { type: "string" },
        range: { type: "object", properties: { start: { type: "integer" }, end: { type: "integer" } } },
        text: { type: "string" },
      },
    },
  },
  {
    name: "applyStyle",
    description:
      "Apply a paragraph style (styleId from getStyles) to all paragraphs in a range ({start, end} character offsets). Low risk: runs immediately with track changes on.",
    inputSchema: {
      type: "object",
      properties: {
        docId: { type: "string" },
        range: { type: "object", properties: { start: { type: "integer" }, end: { type: "integer" } } },
        styleId: { type: "string" },
      },
    },
  },
  {
    name: "generateTable",
    description:
      "Append a new table with rows x cols cells, optionally filled from data (list of rows of cell values). Low risk: runs immediately with track changes on.",
    inputSchema: {
      type: "object",
      properties: {
        docId: { type: "string" },
        rows: { type: "integer" },
        cols: { type: "integer" },
        data: { type: "array", items: { type: "array" } },
        style: { type: "string" },
      },
    },
  },
  {
    name: "refactorSection",
    description:
      "Replace an entire section (sectionId is the index into getOutline) with the given instruction output. High risk (deletes a whole section): double confirmation required.",
    inputSchema: {
      type: "object",
      properties: {
        docId: { type: "string" },
        sectionId: { type: "integer" },
        instruction: { type: "string" },
      },
    },
  },
  {
    name: "setPageLayout",
    description:
      "Set page layout options: margins {top,bottom,left,right} in mm, page_size {width,height} in mm, and columns (int). Low risk: runs immediately.",
    inputSchema: { type: "object", properties: { docId: { type: "string" }, opts: { type: "object" } } },
  },
  {
    name: "snapshot",
    description:
      "Create a manual version snapshot of the current document state (co commit -m <label>); returns the new commit hash.",
    inputSchema: {
      type: "object",
      properties: { docId: { type: "string" }, label: { type: "string" } },
    },
  },
  {
    name: "rollback",
    description:
      "Restore the document contents to a previous commit hash (co checkout <hash>). The on-disk document is replaced with that snapshot's contents.",
    inputSchema: {
      type: "object",
      properties: { docId: { type: "string" }, hash: { type: "string" } },
      required: ["hash"],
    },
  },
  {
    name: "exportDoc",
    description:
      "Export the document honoring ADR-005. Defaults to a PURE export: the copy carries no .co/ version history (100% compatible with Word/WPS) and history is written to a <file>.co-bundle companion (includeBundle defaults to true).",
    inputSchema: {
      type: "object",
      properties: {
        docId: { type: "string" },
        includeCo: { type: "boolean" },
        includeBundle: { type: "boolean" },
        path: { type: "string" },
        package: { type: "boolean" },
      },
    },
  },
  {
    name: "importBundle",
    description:
      "Restore version history from a <file>.co-bundle companion onto the document (doc 14.6 scenario B).",
    inputSchema: {
      type: "object",
      properties: { docId: { type: "string" }, bundlePath: { type: "string" } },
      required: ["bundlePath"],
    },
  },
];

export const MOCK_HISTORY: CommitInfo[] = [
  {
    hash: "9f3a1c7e",
    author: "AI-Writer",
    email: "ai@coffice.local",
    message: "pre: round-7f3a9b2",
    timestamp: "2026-07-31T14:12:05+00:00",
  },
  {
    hash: "4d0b8a21",
    author: "human:张三",
    email: "zhangsan@example.com",
    message: "撰写引言部分",
    timestamp: "2026-07-31T13:48:33+00:00",
  },
  {
    hash: "a7e22c90",
    author: "AI-Writer",
    email: "ai@coffice.local",
    message: "pre: round-2b91c04",
    timestamp: "2026-07-31T13:41:12+00:00",
  },
  {
    hash: "1b6d5f08",
    author: "human:李四",
    email: "lisi@example.com",
    message: "初始大纲：项目背景、目标与交付物",
    timestamp: "2026-07-31T09:05:47+00:00",
  },
];

export const MOCK_DIFF: DiffEntry[] = [
  { status: "M", path: "word/document.xml" },
  { status: "A", path: "word/styles.xml" },
];

export const MOCK_CONTEXT: DocumentContext = {
  docId: "default",
  path: "/tmp/report.docx",
  outline: [
    { start: 0, end: 18, level: 1, style: "Heading 1", text: "## 项目背景" },
    { start: 24, end: 40, level: 1, style: "Heading 1", text: "## 目标与交付物" },
    { start: 47, end: 60, level: 2, style: "Heading 2", text: "### 里程碑" },
  ],
  selection: "第 1 段（共 3 段），样式：正文",
};

export const MOCK_CHANGES: AppliedToolCall[] = [
  {
    tool: "insertText",
    arguments: { pos: "end", text: "本报告总结本季度进展。\n下一步计划包括发布路线图。", style: "正文" },
    result: { ok: true, docId: "default", inserted_at: 312, redline: "redline-1" },
    ok: true,
  },
  {
    tool: "applyStyle",
    arguments: { range: { start: 0, end: 18 }, styleId: "Heading 1" },
    result: { ok: true, docId: "default", applied_to: 1 },
    ok: true,
  },
];

/** Simulated /chat response used when the agent API is unreachable. */
export function mockChatReply(message: string, sessionId: string): ChatResult {
  const summary: AppliedToolCall[] = [
    {
      tool: "insertText",
      arguments: { pos: "end", text: "根据您的指示：\n" + message },
      result: { ok: true, docId: "default", inserted_at: 320, redline: "redline-mock" },
      ok: true,
    },
  ];
  return {
    status: "complete",
    reply:
      "已完成：我已根据您的指示插入了一段内容，并保持了文档现有风格。您可以在「Diff」面板查看本次改动，或通过「导出」保存带版本历史的副本。",
    change_summary: summary,
    round_id: "mock-round",
    needs_confirmation: false,
    confirmation: null,
    error: null,
    session_id: sessionId,
  };
}
