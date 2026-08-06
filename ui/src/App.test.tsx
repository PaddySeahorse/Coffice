// Acceptance-level render test: the Agent Deck renders the Chat / Context /
// Tools / Diff / History sections plus the status bar with mock data, and the
// Export dialog exposes the doc 14.6 checkboxes (bundle default-on, warning
// on "包含版本历史").

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import { INCLUDE_CO_WARNING_TEXT } from "./logic/exportDialog";

const mocks = vi.hoisted(() => ({
  commits: [
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
  ],
  tools: [
    {
      name: "getOutline",
      description: "Return the document outline.",
      inputSchema: {},
    },
    {
      name: "insertText",
      description: "Insert text at an offset.",
      inputSchema: {},
    },
  ],
}));

vi.mock("./api/agentApi", () => ({
  checkHealth: vi.fn(async () => false),
  sendChatStream: vi.fn(
    async (
      _message: string,
      _sessionId: string | undefined,
      _operator: string | undefined,
      onEvent?: (event: { event: string; data: Record<string, unknown> }) => void,
    ) => {
      onEvent?.({ event: "token", data: { text: "已完成。" } });
      return {
        status: "complete",
        reply: "已完成。",
        change_summary: [],
        round_id: "r1",
        needs_confirmation: false,
        confirmation: null,
        error: null,
        session_id: "s1",
      };
    },
  ),
  sendConfirmationStream: vi.fn(
    async (
      _request: unknown,
      onEvent?: (event: { event: string; data: Record<string, unknown> }) => void,
    ) => {
      onEvent?.({ event: "token", data: { text: "已确认。" } });
      return {
        status: "complete",
        reply: "已确认。",
        change_summary: [],
        round_id: "r1",
        needs_confirmation: false,
        confirmation: null,
        error: null,
        session_id: "s1",
      };
    },
  ),
  sendChat: vi.fn(async () => ({
    status: "complete",
    reply: "已完成。",
    change_summary: [],
    round_id: "r1",
    needs_confirmation: false,
    confirmation: null,
    error: null,
    session_id: "s1",
  })),
  sendConfirmation: vi.fn(async () => ({
    status: "complete",
    reply: "已确认。",
    change_summary: [],
    round_id: "r1",
    needs_confirmation: false,
    confirmation: null,
    error: null,
    session_id: "s1",
  })),
}));

vi.mock("./api/mcpApi", () => ({
  callTool: vi.fn(async () => ({ ok: true })),
  exportDoc: vi.fn(async () => ({
    ok: true,
    path: "/tmp/out.docx",
    bundlePath: "/tmp/out.docx.co-bundle",
    packagePath: null,
    includeCo: false,
    warning: "",
    warnings: [],
  })),
  fetchDiff: vi.fn(async () => []),
  fetchHistory: vi.fn(async () => mocks.commits),
  fetchTools: vi.fn(async () => mocks.tools),
  importBundle: vi.fn(async () => ({ ok: true })),
}));

vi.mock("./api/shell", () => ({
  onShellHandshake: () => () => undefined,
  pingShell: () => undefined,
}));

describe("Agent Deck", () => {
  it("renders the section rail and the status bar with mock data", async () => {
    render(<App />);
    expect(screen.getByTestId("app")).toBeInTheDocument();
    // status bar strip: Agent idle + co commit count (history loads async)
    expect(screen.getByTestId("statusbar-agent")).toHaveTextContent("Agent: idle");
    expect(await screen.findByTestId("statusbar-co")).toHaveTextContent("co: 2 commits");
    // section rail
    for (const section of ["chat", "context", "tools", "diff", "history"]) {
      expect(screen.getByTestId(`rail-${section}`)).toBeInTheDocument();
    }
    // chat panel is the default section
    expect(screen.getByTestId("chat-panel")).toBeInTheDocument();
  });

  it("switches between the Tools, Diff and History panels", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByTestId("rail-tools"));
    const toolsPanel = screen.getByTestId("tools-panel");
    expect(toolsPanel).toBeInTheDocument();
    expect(await within(toolsPanel).findByText("getOutline")).toBeInTheDocument();

    await user.click(screen.getByTestId("rail-diff"));
    expect(screen.getByTestId("diff-panel")).toBeInTheDocument();

    await user.click(screen.getByTestId("rail-history"));
    const historyPanel = screen.getByTestId("history-panel");
    expect(historyPanel).toBeInTheDocument();
    expect(await within(historyPanel).findByText(/9f3a1c7 · AI-Writer/)).toBeInTheDocument();
  });

  it("opens the export dialog with bundle checked by default and the include-co warning hidden", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByTestId("rail-export"));

    const dialog = screen.getByTestId("export-dialog");
    expect(dialog).toBeInTheDocument();
    const bundleCheckbox = within(dialog).getByTestId("checkbox-include-bundle");
    expect(bundleCheckbox).toHaveTextContent("同时导出 .co-bundle");
    expect(within(bundleCheckbox).getByRole("checkbox")).toBeChecked();

    const coCheckbox = within(dialog).getByTestId("checkbox-include-co");
    expect(within(coCheckbox).getByRole("checkbox")).not.toBeChecked();
    // no mandatory warning while 包含版本历史 is off
    expect(screen.queryByTestId("export-warning")).not.toBeInTheDocument();

    // checking 包含版本历史 reveals the mandatory warning (doc 14.6)
    await user.click(within(coCheckbox).getByRole("checkbox"));
    expect(await screen.findByTestId("export-warning")).toHaveTextContent(
      INCLUDE_CO_WARNING_TEXT,
    );
  });

  it("opens the command palette with Ctrl+K", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.keyboard("{Control>}k{/Control}");
    const palette = await screen.findByTestId("command-palette");
    expect(palette).toBeInTheDocument();
    expect(screen.getByText("回滚到上一步")).toBeInTheDocument();
    expect(screen.getByText("查看历史")).toBeInTheDocument();
    expect(screen.getByText("创建分支")).toBeInTheDocument();
  });

  it("streams assistant tokens into the chat while running", async () => {
    const { sendChatStream } = await import("./api/agentApi");
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("消息输入"), "写一段总结");
    await user.click(screen.getByRole("button", { name: "发送" }));

    // the streamed token lands in the assistant message
    expect(await screen.findByText("已完成。")).toBeInTheDocument();
    expect(sendChatStream).toHaveBeenCalledWith(
      "写一段总结",
      undefined,
      "sidebar-user",
      expect.any(Function),
    );
  });
});
