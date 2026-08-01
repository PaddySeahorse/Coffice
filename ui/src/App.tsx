// Agent Deck root: owns the agent/tool state, wires the Chat / Context /
// Tools / Diff / History panels to the agent HTTP facade (with mock fallback),
// and hosts the Status bar strip, Export dialog, and Cmd+K palette.

import { useCallback, useEffect, useMemo, useState } from "react";
import { checkHealth, sendChat, sendConfirmation } from "./api/agentApi";
import {
  callTool,
  exportDoc,
  fetchDiff,
  fetchHistory,
  fetchTools,
  importBundle,
} from "./api/mcpApi";
import { onShellHandshake, pingShell } from "./api/shell";
import { ChatPanel } from "./components/ChatPanel";
import { CommandPalette, type PaletteAction } from "./components/CommandPalette";
import { ContextPanel } from "./components/ContextPanel";
import { DiffPanel } from "./components/DiffPanel";
import { ExportDialog } from "./components/ExportDialog";
import { HistoryPanel } from "./components/HistoryPanel";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { ToolsPanel } from "./components/ToolsPanel";
import { MOCK_CONTEXT } from "./mock/data";
import { summarizeToolCall } from "./logic/toolSummary";
import type {
  AgentStatus,
  AppliedToolCall,
  ChatMessageItem,
  CommitInfo,
  DocumentContext,
  ExportParams,
  ExportResult,
  McpToolInfo,
  PendingConfirmation,
  SectionId,
} from "./types";

let messageCounter = 0;
function nextMessageId(): string {
  messageCounter += 1;
  return `msg-${Date.now().toString(36)}-${messageCounter}`;
}

interface Banner {
  kind: "info" | "success" | "error";
  text: string;
}

export default function App() {
  const [activeSection, setActiveSection] = useState<SectionId>("chat");
  const [collapsed, setCollapsed] = useState(false);
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [agentStatus, setAgentStatus] = useState<AgentStatus>("idle");
  const [changeSummary, setChangeSummary] = useState<AppliedToolCall[]>([]);
  const [tools, setTools] = useState<McpToolInfo[]>([]);
  const [history, setHistory] = useState<CommitInfo[]>([]);
  const [context, setContext] = useState<DocumentContext>(MOCK_CONTEXT);
  const [busyChat, setBusyChat] = useState(false);
  const [busyTool, setBusyTool] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [banner, setBanner] = useState<Banner | null>(null);

  const showBanner = useCallback((kind: Banner["kind"], text: string) => {
    setBanner({ kind, text });
    window.setTimeout(() => setBanner(null), 4000);
  }, []);

  const refreshHistory = useCallback(async () => {
    const commits = await fetchHistory();
    setHistory(commits);
  }, []);

  // Initial load: tools introspection + co log timeline (mock fallback).
  useEffect(() => {
    void fetchTools().then(setTools);
    void refreshHistory();
    void checkHealth().then((healthy) => {
      if (healthy) pingShell();
    });
    const unsubscribe = onShellHandshake((handshake) => {
      const docId = handshake.docId;
      if (docId) {
        setContext((current) => ({ ...current, docId }));
      }
    });
    return unsubscribe;
  }, [refreshHistory]);

  // Cmd+K (and Ctrl+K on Windows/Linux) toggles the command palette.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const appendMessage = useCallback((message: Omit<ChatMessageItem, "id" | "timestamp">) => {
    setMessages((current) => [
      ...current,
      { ...message, id: nextMessageId(), timestamp: new Date().toISOString() },
    ]);
  }, []);

  const handleSend = useCallback(
    async (text: string) => {
      appendMessage({ role: "user", content: text });
      setBusyChat(true);
      setAgentStatus("working");
      const result = await sendChat(text, sessionId ?? undefined, "sidebar-user");
      setSessionId(result.session_id ?? sessionId);
      setAgentStatus(result.status === "error" ? "error" : result.status === "needs_confirmation" ? "needs_confirmation" : "idle");
      setBusyChat(false);

      if (result.status === "error") {
        appendMessage({
          role: "system",
          content: result.error ?? "发生错误",
          error: true,
        });
        return;
      }
      for (const change of result.change_summary) {
        appendMessage({
          role: "tool",
          content: summarizeToolCall(change),
        });
      }
      if (result.status === "needs_confirmation") {
        appendMessage({
          role: "assistant",
          content: result.confirmation?.summary || result.reply || "该改动需要确认",
          pending: result.confirmation ?? undefined,
        });
      } else if (result.reply) {
        appendMessage({ role: "assistant", content: result.reply });
      }
      if (result.change_summary.length > 0) {
        setChangeSummary((current) => [...current, ...result.change_summary]);
        void refreshHistory();
      }
    },
    [appendMessage, refreshHistory, sessionId],
  );

  const handleConfirm = useCallback(
    async (pending: PendingConfirmation, action: "confirm" | "reject") => {
      if (!sessionId) {
        showBanner("error", "没有可用的会话 ID");
        return;
      }
      setBusyChat(true);
      setAgentStatus("working");
      const result = await sendConfirmation({ sessionId, token: pending.token, action });
      setAgentStatus(result.status === "error" ? "error" : "idle");
      setBusyChat(false);
      if (result.status === "error") {
        appendMessage({ role: "system", content: result.error ?? "操作失败", error: true });
        return;
      }
      for (const change of result.change_summary) {
        appendMessage({ role: "tool", content: summarizeToolCall(change) });
      }
      if (result.reply) appendMessage({ role: "assistant", content: result.reply });
      if (result.change_summary.length > 0) {
        setChangeSummary((current) => [...current, ...result.change_summary]);
        void refreshHistory();
      }
      void refreshHistory();
    },
    [appendMessage, refreshHistory, sessionId, showBanner],
  );

  const handleAcceptChange = useCallback(
    (change: AppliedToolCall) => {
      const pending = messages.find((message) => message.pending)?.pending;
      if (pending) {
        void handleConfirm(pending, "confirm");
        return;
      }
      showBanner("success", `已接受：${summarizeToolCall(change)}`);
    },
    [handleConfirm, messages, showBanner],
  );

  const handleRejectChange = useCallback(
    (change: AppliedToolCall) => {
      const pending = messages.find((message) => message.pending)?.pending;
      if (pending) {
        void handleConfirm(pending, "reject");
        return;
      }
      setChangeSummary((current) =>
        current.filter((item) => item !== change),
      );
      showBanner("info", `已拒绝：${summarizeToolCall(change)}（已从改动列表移除）`);
    },
    [handleConfirm, messages, showBanner],
  );

  const handleRunTool = useCallback(
    async (tool: McpToolInfo): Promise<Record<string, unknown>> => {
      setBusyTool(true);
      try {
        const result = await callTool(tool.name, { docId: context.docId });
        if (tool.name === "getHistory") void refreshHistory();
        return result;
      } finally {
        setBusyTool(false);
      }
    },
    [context.docId, refreshHistory],
  );

  const handleRollback = useCallback(
    async (hash: string) => {
      setBusyTool(true);
      try {
        const result = await callTool("rollback", { docId: context.docId, hash });
        if (result.ok === false) {
          showBanner("error", String(result.error ?? "回滚失败"));
        } else {
          showBanner("success", `已回滚到 ${hash.slice(0, 7)}`);
        }
        void refreshHistory();
      } catch (error) {
        showBanner("error", error instanceof Error ? error.message : String(error));
      } finally {
        setBusyTool(false);
      }
    },
    [context.docId, refreshHistory, showBanner],
  );

  const handlePreview = useCallback(
    async (commit: CommitInfo, previous: CommitInfo | null) => {
      if (!previous) return [];
      return fetchDiff(commit.hash, previous.hash, context.docId);
    },
    [context.docId],
  );

  const handleRefreshContext = useCallback(async () => {
    setBusyTool(true);
    try {
      const [outlineResult, selectionResult] = await Promise.all([
        callTool("getOutline", { docId: context.docId }),
        callTool("getSelection", { docId: context.docId }),
      ]);
      const outline = Array.isArray(outlineResult.outline) ? outlineResult.outline : [];
      const selection = typeof selectionResult.selection === "string" ? selectionResult.selection : null;
      setContext((current) => ({ ...current, outline, selection }));
    } catch {
      // mock context stays; nothing to surface in standalone mode
    } finally {
      setBusyTool(false);
    }
  }, [context.docId]);

  const handleExport = useCallback(
    async (params: ExportParams): Promise<ExportResult> => exportDoc(params),
    [],
  );

  const paletteActions: PaletteAction[] = useMemo(
    () => [
      {
        id: "rollback-prev",
        label: "回滚到上一步",
        hint: "回滚到上一个版本提交",
        run: () => {
          if (history.length >= 2) {
            void handleRollback(history[1].hash);
          } else {
            showBanner("info", "没有可回滚的提交（至少需要两个提交）");
          }
        },
      },
      {
        id: "view-history",
        label: "查看历史",
        hint: "打开版本历史面板",
        run: () => setActiveSection("history"),
      },
      {
        id: "create-branch",
        label: "创建分支",
        hint: "在当前提交创建命名分支",
        run: () => {
          const name = window.prompt("分支名称：");
          if (!name?.trim()) return;
          void callTool("branch", { docId: context.docId, name: name.trim() })
            .then((result) => {
              if (result.ok === false) {
                showBanner("error", String(result.error ?? "创建分支失败"));
              } else {
                showBanner("success", `已创建分支 ${name.trim()}`);
              }
            })
            .catch((error: unknown) =>
              showBanner("error", error instanceof Error ? error.message : String(error)),
            );
        },
      },
      {
        id: "import-bundle",
        label: "导入版本历史 (.co-bundle)",
        hint: "从 .co-bundle 恢复历史",
        run: () => {
          const path = window.prompt(".co-bundle 路径：");
          if (!path?.trim()) return;
          void importBundle(path.trim(), context.docId)
            .then((result) => {
              if (result.ok === false) {
                showBanner("error", String(result.error ?? "导入失败"));
              } else {
                showBanner("success", "版本历史已导入");
                void refreshHistory();
              }
            })
            .catch((error: unknown) =>
              showBanner("error", error instanceof Error ? error.message : String(error)),
            );
        },
      },
    ],
    [context.docId, handleRollback, history, refreshHistory, showBanner],
  );

  const sectionPanel = (() => {
    switch (activeSection) {
      case "chat":
        return (
          <ChatPanel
            messages={messages}
            busy={busyChat}
            onSend={(text) => void handleSend(text)}
            onConfirm={(pending, action) => void handleConfirm(pending, action)}
          />
        );
      case "context":
        return (
          <ContextPanel
            context={context}
            busy={busyTool}
            onRefresh={() => void handleRefreshContext()}
          />
        );
      case "tools":
        return <ToolsPanel tools={tools} onRunTool={handleRunTool} />;
      case "diff":
        return (
          <DiffPanel
            changes={changeSummary}
            sessionId={sessionId}
            busy={busyChat || busyTool}
            onAccept={handleAcceptChange}
            onReject={handleRejectChange}
          />
        );
      case "history":
        return (
          <HistoryPanel
            commits={history}
            busy={busyTool}
            onRollback={(hash) => void handleRollback(hash)}
            onPreview={handlePreview}
          />
        );
    }
  })();

  return (
    <div className="app" data-testid="app">
      <Sidebar
        activeSection={activeSection}
        collapsed={collapsed}
        onSelectSection={setActiveSection}
        onToggleCollapsed={() => setCollapsed((value) => !value)}
        onOpenExport={() => setExportOpen(true)}
        onOpenPalette={() => setPaletteOpen(true)}
      >
        {sectionPanel}
      </Sidebar>
      <StatusBar agentStatus={agentStatus} commitCount={history.length} />
      {banner && (
        <div className={`banner banner--${banner.kind}`} data-testid="banner" role="status">
          {banner.text}
        </div>
      )}
      <ExportDialog
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        onExport={handleExport}
      />
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        actions={paletteActions}
      />
    </div>
  );
}
