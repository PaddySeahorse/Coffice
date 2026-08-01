# Coffice Agent Deck (React UI)

The VSCode-style left sidebar ("Agent Deck") for Coffice Route 1, hosted inside
the LibreOffice extension shell (`extension/`, planning doc ch. 9). Built with
Vite + React + TypeScript, plain CSS (no UI framework), and Vitest.

## Quickstart

```sh
npm install        # install deps
make run-ui        # from the repo root: vite dev server on http://127.0.0.1:8787/
make build-sidebar # production build -> ui/dist/
make ui-test       # vitest
```

The dev server binds `127.0.0.1:8787` — the origin the sidebar shell loads by
default (`src/coffice/sidebar/contract.py`, `COFFICE_UI_PORT` override
honored). The app also builds and serves standalone, so it runs before the
agent API / MCP server are up: every API call falls back to bundled mock data.

## Panels (planning doc 9.1 / 9.2)

- **Chat** — conversation with the AI over the agent HTTP facade (`POST /chat`,
  `POST /confirm`). Non-streaming MVP: assistant replies plus tool-call
  summaries ("insertText → 2 paragraphs"), and a confirm/reject prompt when a
  write tool needs approval.
- **Context** — current document id / path / selection and the outline
  (getOutline/getSelection), with a manual refresh.
- **Tools** — registered MCP tools with descriptions, fetched from the
  introspection endpoint (`GET /tools`); read tools can be triggered manually
  (`POST /tool`).
- **Diff** — per-session change summary (the agent's `change_summary`), each
  change expandable with accept/reject. Pending changes flow through
  `POST /confirm` (governance `confirmOp`/`rejectOp` server-side); already
  applied changes are accepted/rejected locally (demo mode without a backend).
- **History** — `co log` timeline (`GET /history`): hash, author, message,
  timestamp; per-commit preview (`GET /diff` vs. the previous commit) and a
  rollback button (`POST /tool` → `rollback`).
- **Status bar strip** — `Agent: idle/working/needs confirmation/error` and
  `co: N commits`.
- **Export dialog (doc 14.6)** — checkbox 包含版本历史 (.co/) with the mandatory
  warning "勾选后若使用 Microsoft Word 或 WPS 打开此文件，版本历史将会丢失" when
  checked, checkbox 同时导出 .co-bundle (default on), optional .coffice.zip
  packaging, [导出]/[取消]; wired to the `exportDoc` tool.
- **Command palette (Cmd+K / Ctrl+K)** — quick actions 回滚到上一步 / 查看历史 /
  创建分支 (+ 导入版本历史).

## API contract (aligned with the agent / MCP / versioning beads)

All payload shapes mirror the backend tools; the UI never redefines them.

| Endpoint         | Used by                                | Notes                                   |
| ---------------- | -------------------------------------- | --------------------------------------- |
| `POST /chat`     | Chat panel                             | agent bead: `ChatResult` + session_id   |
| `POST /confirm`  | Chat confirm/reject, Diff accept/reject| `{session_id, token, action}`           |
| `GET /health`    | status probe                           |                                         |
| `GET /tools`     | Tools panel introspection              | `{tools: [{name, description, inputSchema}]}` |
| `POST /tool`     | read triggers, export, rollback, branch| `{name, arguments}` → tool result dict  |
| `GET /history`   | History panel                          | `{commits: [{hash, author, email, message, timestamp}]}` |
| `GET /diff`      | History preview                        | `{diff: [{status: A\|D\|M, path}]}`     |

The agent facade is configured with `VITE_COFFICE_AGENT_URL` (default
`http://127.0.0.1:8790/`). When the facade is unreachable, reads return the
mock data in `src/mock/data.ts` and chat/export simulate a successful round,
so the deck is fully usable standalone.

## Shell bridge (postMessage)

`src/api/shell.ts` implements the hosting contract from
`src/coffice/sidebar/contract.py` (`coffice.command` / `coffice.result` /
`coffice.handshake` / ping-pong) for a future WebView host. The MVP shell is a
native AWT panel, so the bridge stays dormant: commands time out to
`undefined` and the app keeps working.

## Layout

```
src/
  api/          agentApi.ts, mcpApi.ts, shell.ts, config.ts
  components/   Sidebar, StatusBar, Chat/Context/Tools/Diff/History panels,
                ExportDialog, CommandPalette
  logic/        exportDialog.ts (pure state machine), toolSummary.ts, history.ts
  mock/data.ts  standalone mock tools/history/context/chat
  types.ts      payload types mirroring the backend contracts
```

## Tests

`make ui-test` / `npm test` (Vitest + Testing Library): export-dialog state
logic (warning visibility, default bundle checked), history list rendering,
tool summaries, and an acceptance render of the panels + status bar + export
dialog with mock data.
