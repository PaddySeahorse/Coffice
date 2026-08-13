// Agent Deck root: owns the agent/tool state, wires the Chat / Context /
// Tools / Diff / History panels to the agent HTTP facade (with mock fallback),
// and hosts the Status bar strip, Export dialog, and Cmd+K palette.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  checkHealth,
  sendChatStream,
  sendConfirmationStream,
} from "./api/agentApi";
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
import { SettingsPanel } from "./components/SettingsPanel";
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

/** Map the agent's confirmation dict (snake_case) to the UI's PendingConfirmation. */
function toPendingConfirmation(raw: unknown): PendingConfirmation | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const data = raw as Record<string, unknown>;
  return {
    token: String(data.token ?? ""),
    tool: String(data.tool ?? ""),
    summary: String(data.summary ?? ""),
    snapshotHash: data.snapshot_hash != null ? String(data.snapshot_hash) : null,
  };
}

interface Banner {
  kind: "info" | "success" | "error";
  text: string;
}

export default function App() {
  const [activeSection, setActiveSection] = useState<SectionId>("chat");
  const [collapsed, setCollapsed] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    const stored = localStorage.getItem("theme");
    if (stored === "light" || stored === "dark") return stored;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  });
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

  // Theme sync
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

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

  /** Append a streamed token to the live assistant message (creates it first). */
  const appendToken = useCallback((delta: string, streaming: { id: string | null }) => {
    if (!streaming.id) {
      streaming.id = nextMessageId();
      setMessages((current) => [
        ...current,
        {
          id: streaming.id!,
          role: "assistant",
          content: delta,
          timestamp: new Date().toISOString(),
        },
      ]);
      return;
    }
    setMessages((current) =>
      current.map((message) =>
        message.id === streaming.id ? { ...message, content: message.content + delta } : message,
      ),
    );
  }, []);

  /** Attach a pending confirmation to the live assistant message (or create one). */
  const attachPending = useCallback(
    (pending: PendingConfirmation | undefined, fallbackText: string, streaming: { id: string | null }) => {
      if (streaming.id) {
        setMessages((current) =>
          current.map((message) =>
            message.id === streaming.id ? { ...message, pending } : message,
          ),
        );
      } else {
        appendMessage({
          role: "assistant",
          content: pending?.summary || fallbackText || "This change requires confirmation",
          pending,
        });
      }
    },
    [appendMessage],
  );

  const pruneEmptyStream = useCallback((streaming: { id: string | null }) => {
    if (!streaming.id) return;
    setMessages((current) =>
      current.filter((message) => !(message.id === streaming.id && !message.content.trim() && !message.pending)),
    );
  }, []);

  /** Clear the confirmation prompt on the message carrying the given token. */
  const clearPending = useCallback((token: string) => {
    setMessages((current) =>
      current.map((message) =>
        message.pending && message.pending.token === token
          ? { ...message, pending: undefined }
          : message,
      ),
    );
  }, []);

  const handleSend = useCallback(
    async (text: string) => {
      appendMessage({ role: "user", content: text });
      setBusyChat(true);
      setAgentStatus("working");
      const streaming = { id: null as string | null };
      const result = await sendChatStream(
        text,
        sessionId ?? undefined,
        "sidebar-user",
        (event) => {
          if (event.event === "token") {
            appendToken(String(event.data.text ?? ""), streaming);
          } else if (event.event === "tool") {
            appendMessage({
              role: "tool",
              content: summarizeToolCall(event.data as unknown as AppliedToolCall),
            });
          } else if (event.event === "needs_confirmation") {
            attachPending(
              toPendingConfirmation(event.data.confirmation),
              String(event.data.reply ?? ""),
              streaming,
            );
          }
        },
      );
      setSessionId(result.session_id ?? sessionId);
      setAgentStatus(
        result.status === "error"
          ? "error"
          : result.status === "needs_confirmation"
            ? "needs_confirmation"
            : "idle",
      );
      setBusyChat(false);
      pruneEmptyStream(streaming);

      if (result.status === "error") {
        appendMessage({
          role: "system",
          content: result.error ?? "An error occurred",
          error: true,
        });
        return;
      }
      if (result.change_summary.length > 0) {
        setChangeSummary((current) => [...current, ...result.change_summary]);
        void refreshHistory();
      }
    },
    [
      appendMessage,
      appendToken,
      attachPending,
      pruneEmptyStream,
      refreshHistory,
      sessionId,
    ],
  );

  const handleConfirm = useCallback(
    async (pending: PendingConfirmation, action: "confirm" | "reject"): Promise<boolean> => {
      if (!sessionId) {
        showBanner("error", "No available session ID");
        return false;
      }
      setBusyChat(true);
      setAgentStatus("working");
      const streaming = { id: null as string | null };
      const result = await sendConfirmationStream(
        { sessionId, token: pending.token, action },
        (event) => {
          if (event.event === "token") {
            appendToken(String(event.data.text ?? ""), streaming);
          } else if (event.event === "tool") {
            appendMessage({
              role: "tool",
              content: summarizeToolCall(event.data as unknown as AppliedToolCall),
            });
          } else if (event.event === "needs_confirmation") {
            attachPending(
              toPendingConfirmation(event.data.confirmation),
              String(event.data.reply ?? ""),
              streaming,
            );
          }
        },
      );
      setAgentStatus(
        result.status === "error"
          ? "error"
          : result.status === "needs_confirmation"
            ? "needs_confirmation"
            : "idle",
      );
      setBusyChat(false);
      pruneEmptyStream(streaming);
      if (result.status === "error") {
        appendMessage({ role: "system", content: result.error ?? "Operation failed", error: true });
        return false;
      }
      // The pending change was resolved: hide its confirm prompt so the same
      // token cannot be confirmed again (the backend clears it after one use).
      clearPending(pending.token);
      if (result.change_summary.length > 0) {
        setChangeSummary((current) => [...current, ...result.change_summary]);
        void refreshHistory();
      }
      void refreshHistory();
      return true;
    },
    [
      appendMessage,
      appendToken,
      attachPending,
      clearPending,
      pruneEmptyStream,
      refreshHistory,
      sessionId,
      showBanner,
    ],
  );

  const handleAcceptChange = useCallback(
    async (change: AppliedToolCall) => {
      const pending = messages.find((message) => message.pending)?.pending;
      if (pending) {
        const accepted = await handleConfirm(pending, "confirm");
        if (!accepted) return;
      }
      setChangeSummary((current) => current.filter((item) => item !== change));
      showBanner("success", `Accepted: ${summarizeToolCall(change)}`);
    },
    [handleConfirm, messages, showBanner],
  );

  const handleRejectChange = useCallback(
    async (change: AppliedToolCall) => {
      const pending = messages.find((message) => message.pending)?.pending;
      if (pending) {
        const rejected = await handleConfirm(pending, "reject");
        if (!rejected) return;
      }
      setChangeSummary((current) => current.filter((item) => item !== change));
      showBanner("info", `Rejected: ${summarizeToolCall(change)} (removed from change list)`);
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
          showBanner("error", String(result.error ?? "Revert failed"));
        } else {
          showBanner("success", `Reverted to ${hash.slice(0, 7)}`);
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
        label: "Revert to previous step",
        hint: "Revert to previous commit",
        run: () => {
          if (history.length >= 2) {
            void handleRollback(history[1].hash);
          } else {
            showBanner("info", "No commits to revert to (requires at least two commits)");
          }
        },
      },
      {
        id: "view-history",
        label: "View History",
        hint: "Open version history panel",
        run: () => setActiveSection("history"),
      },
      {
        id: "create-branch",
        label: "Create Branch",
        hint: "Create named branch at current commit",
        run: () => {
          const name = window.prompt("Branch name:");
          if (!name?.trim()) return;
          void callTool("branch", { docId: context.docId, name: name.trim() })
            .then((result) => {
              if (result.ok === false) {
                showBanner("error", String(result.error ?? "Create branch failed"));
              } else {
                showBanner("success", `Created branch ${name.trim()}`);
              }
            })
            .catch((error: unknown) =>
              showBanner("error", error instanceof Error ? error.message : String(error)),
            );
        },
      },
      {
        id: "import-bundle",
        label: "Import version history (.co-bundle)",
        hint: "Restore history from .co-bundle",
        run: () => {
          const path = window.prompt(".co-bundle path:");
          if (!path?.trim()) return;
          void importBundle(path.trim(), context.docId)
            .then((result) => {
              if (result.ok === false) {
                showBanner("error", String(result.error ?? "Import failed"));
              } else {
                showBanner("success", "Version history imported");
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
      case "settings":
        return <SettingsPanel onNotify={showBanner} />;
    }
  })();

  return (
    <div className="app" data-testid="app">
      <a href="#main" className="skip-link">
        Skip to main content
      </a>
      <h1 className="sr-only">Coffice Agent Deck</h1>
      <Sidebar
        activeSection={activeSection}
        collapsed={collapsed}
        onSelectSection={setActiveSection}
        onToggleCollapsed={() => setCollapsed((value) => !value)}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        onOpenExport={() => setExportOpen(true)}
        onOpenPalette={() => setPaletteOpen(true)}
      >
        <main id="main" tabIndex={-1} className="sidebar-content">
          {sectionPanel}
        </main>
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
