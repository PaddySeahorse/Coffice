// Tools panel: registered MCP tools with descriptions (fetched via the
// introspection endpoint GET /tools) + manual trigger for read tools.

import { useState } from "react";
import type { McpToolInfo } from "../types";

const READ_TOOL_NAMES = new Set([
  "getOutline",
  "getSelection",
  "getStyles",
  "getTables",
  "getRedlines",
  "getHistory",
]);

interface ToolsPanelProps {
  tools: McpToolInfo[];
  onRunTool: (tool: McpToolInfo) => Promise<Record<string, unknown>>;
}

interface ToolRunState {
  name: string;
  running: boolean;
  result: string | null;
  error: string | null;
}

export function ToolsPanel({ tools, onRunTool }: ToolsPanelProps) {
  const [runState, setRunState] = useState<ToolRunState | null>(null);

  const run = async (tool: McpToolInfo) => {
    setRunState({ name: tool.name, running: true, result: null, error: null });
    try {
      const result = await onRunTool(tool);
      setRunState({ name: tool.name, running: false, result: JSON.stringify(result, null, 2), error: null });
    } catch (error) {
      setRunState({
        name: tool.name,
        running: false,
        result: null,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  };

  return (
    <section className="panel" data-testid="tools-panel">
      <div className="panel-scroll">
        <p className="panel-hint">{tools.length} 个已注册工具（MCP 内省）</p>
        <ul className="tool-list">
          {tools.map((tool) => {
            const isRead = READ_TOOL_NAMES.has(tool.name);
            return (
              <li key={tool.name} className="tool-item">
                <div className="tool-item__head">
                  <code className="tool-name">{tool.name}</code>
                  {isRead && (
                    <button
                      type="button"
                      className="btn btn--ghost btn--small"
                      data-testid={`run-${tool.name}`}
                      disabled={runState?.running}
                      onClick={() => void run(tool)}
                    >
                      {runState?.name === tool.name && runState.running ? "运行中…" : "运行"}
                    </button>
                  )}
                </div>
                <p className="tool-desc">{tool.description}</p>
              </li>
            );
          })}
        </ul>
        {runState?.result && (
          <pre className="tool-result" data-testid="tool-result">{runState.result}</pre>
        )}
        {runState?.error && (
          <pre className="tool-result tool-result--error" data-testid="tool-result-error">{runState.error}</pre>
        )}
      </div>
    </section>
  );
}
