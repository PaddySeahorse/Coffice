# Coffice Route 1 (MVP, Phase 1) — Integration Guide

Coffice is an AI-first office suite: LibreOffice is the rendering core, a
Python MCP server bridges an LLM agent to the open document through PyUNO, and
the `co` CLI (<https://github.com/PaddySeahorse/co>) provides Git-style
version-control snapshots. This document is the Route 1 integration guide:
what you need to install, how to run the closed loop end to end, how the code
maps to the planning doc, and what is deliberately out of scope for the MVP.

The full planning doc is referenced throughout as "planning doc §N"; the
implementation beads that delivered each slice are listed under "Architecture
mapping".

---

## 1. Prerequisites

### 1.1 Python

- Python **>= 3.11** and either `uv` (preferred) or `python3 -m venv` + pip.
- Everything is installed into a local `.venv` by `make setup`; nothing is
  installed system-wide.

### 1.2 LibreOffice + PyUNO

PyUNO (`uno`/`pyuno`) is *not* installable from PyPI — it ships with
LibreOffice. Two supported setups (see `src/coffice/core/lo_manager.py`):

1. **System package (Debian/Ubuntu)** — installs `soffice` on PATH and makes
   `uno` importable from the system Python:

   ```sh
   sudo apt install libreoffice python3-uno
   ```

2. **LibreOffice's bundled interpreter** — LO ships its own Python at
   `<soffice-install>/program/python` (Debian:
   `/usr/lib/libreoffice/program/python`). Running Coffice under *that*
   interpreter makes `uno` importable with no system package; `make setup` +
   `make smoke` under that interpreter work too.

Coffice starts LibreOffice **headless** itself (a UNO socket listener on
`127.0.0.1:2002`, configurable via `COFFICE_LO_PORT`), so you never need to
launch the GUI. If LO is missing, nothing fails at import time — LO-dependent
features return a readable error and the smoke test reports `SKIPPED`.

### 1.3 The `co` CLI (version control)

`co` is a C++17/CMake project. Install it with:

```sh
make install-co        # == bash scripts/install_co.sh
```

The installer clones, builds, and installs the binary to
`~/.local/bin/co`, then prints the exact export line:

```sh
export CO_BIN="$HOME/.local/bin/co"   # optional; $PATH and ~/.local/bin are also probed
```

`scripts/install_co.sh` fails loudly when a toolchain piece is missing
(git / cmake >= 3.16 / C++17 compiler / zlib / OpenSSL headers) with the exact
`apt` command to fix it. The Python wrapper (`coffice.versioning.co_client`)
resolves the binary from `CO_BIN` → `COFFICE_CO_BIN` → `PATH` →
`~/.local/bin/co` → `/usr/local/bin/co`, and degrades gracefully with clear
instructions when it is absent.

### 1.4 An LLM endpoint (OpenAI-compatible)

The agent talks to **any** OpenAI-compatible chat-completions endpoint
(non-streaming only). Configure it with environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `COFFICE_LLM_BASE_URL` | `http://127.0.0.1:11434/v1` | API base URL |
| `COFFICE_LLM_MODEL` | `qwen2.5:7b` | Model id |
| `COFFICE_LLM_API_KEY` | *(none)* | Bearer token; local servers need none |

Common setups:

```sh
# Ollama (localhost)
export COFFICE_LLM_BASE_URL=http://127.0.0.1:11434/v1
export COFFICE_LLM_MODEL=qwen2.5:7b

# LM Studio
export COFFICE_LLM_BASE_URL=http://127.0.0.1:1234/v1
export COFFICE_LLM_MODEL=your-model-name

# Cloud (OpenAI-compatible)
export COFFICE_LLM_BASE_URL=https://api.openai.com/v1
export COFFICE_LLM_MODEL=gpt-4o-mini
export COFFICE_LLM_API_KEY=sk-...
```

Other relevant environment variables:

- `COFFICE_DOC_PATH` — `.docx`/`.odt` the agent opens (default: empty in-memory doc).
- `COFFICE_LO_PORT` — UNO socket port (default `2002`).
- `COFFICE_AUDIT_LOG` — path of the doc 8.4 JSONL audit log (unset = auditing off).
- `COFFICE_WRITE_RATE_LIMIT` — write ops/minute (default `30`).
- `COFFICE_PROTECTED_REGIONS` — `label:start:end[,label:start:end...]` character
  ranges the agent may not edit.
- `COFFICE_AGENT_PORT` / `COFFICE_AGENT_HOST` — agent HTTP facade listener
  (default `127.0.0.1:8790`).
- `COFFICE_UI_URL` / `COFFICE_UI_PORT` — Agent Deck origin the sidebar loads
  (default `http://127.0.0.1:8787/`).

---

## 2. Quickstart

```sh
# 1. one-shot setup: .venv with Python deps + npm install of the React UI
make setup

# 2. external CLI (version control)
make install-co            # installs co -> ~/.local/bin/co
export CO_BIN="$HOME/.local/bin/co"

# 3. point the agent at an LLM (see 1.4)
export COFFICE_LLM_BASE_URL=http://127.0.0.1:11434/v1
export COFFICE_LLM_MODEL=qwen2.5:7b

# 4. open a document for the agent to work on
export COFFICE_DOC_PATH=/path/to/report.docx
```

Then run the pieces (each in its own terminal):

```sh
make run-agent     # agent HTTP facade (chat/confirm + read endpoints) on :8790
make run-ui        # React Agent Deck on http://127.0.0.1:8787/
```

`make run-mcp` serves the same tools over MCP stdio (for MCP clients such as
Claude Desktop):

```sh
make run-mcp
```

To use the in-window sidebar (planning doc 9.1):

```sh
make build-oxt     # -> dist/Coffice.oxt
unopkg add --force dist/Coffice.oxt     # or: make install-oxt
soffice report.docx                     # sidebar: the "Coffice" deck
```

### Verify the closed loop

```sh
make smoke    # end-to-end: LO headless -> sample .docx -> getOutline ->
              # insertText -> snapshot -> rollback -> PURE export + bundle
```

`make smoke` **fails loudly** on any step and **skips gracefully** (exit 0,
`SKIPPED: <reason>`) when LibreOffice/PyUNO or the co CLI are not installed —
so it is safe to run in CI. A full pass prints `SMOKE PASS`.

### Quality gates

```sh
make lint      # ruff check src tests
make test      # pytest (249 tests; LO/co-dependent tests skip cleanly)
make ui-test   # vitest (Agent Deck UI)
make build-oxt # extension build must succeed
```

---

## 3. Architecture mapping to the planning doc

```
                +--------------------------- LibreOffice window ----------------------------+
                |                                                                          |
                |  document (soffice headless, UNO socket)                                 |
                |      ^                                                                   |
                |      | PyUNO                                                            |
                |  +---------+     +-----------+     +-----------+     +-------------+    |
                |  | core/   |     | mcp/      |     | agent/    |     | governance/ |    |
                |  | LO/PyUNO|<--->| MCP server|<--->| LLM loop  |<--->| permissions |    |
                |  | bridge  |     | + tools   |     | + HTTP API|     | + audit log |    |
                |  +---------+     +-----------+     +-----------+     +-------------+    |
                |       ^                 ^                  ^                              |
                |       |                 |                  | HTTP                        |
                |       |                 +------------------+---> React UI (Agent Deck)  |
                |  +-----------+                                                          |
                |  |versioning/|  co CLI (external) -> .co/ history + .co-bundle          |
                |  +-----------+                                                          |
                +--------------------------------------------------------------------------+
```

| Slice | Code | Planning doc |
|---|---|---|
| Core LO/PyUNO bridge | `src/coffice/core/` (`lo_manager.py`, `document.py`) | **ch. 5** — rendering core, soffice lifecycle, Document wrapper |
| MCP server + tools | `src/coffice/mcp/` (`server.py`, `tools_read.py`, `tools_write.py`, `middleware.py`) | **ch. 4** — agent↔document bridge, tool discovery, snapshot middleware |
| Version control + export | `src/coffice/versioning/` (`co_client.py`, `export.py`) + `tools_version.py` | **ch. 14 / ADR-005** — snapshots, rollback, `.co-bundle` export/import |
| Governance | `src/coffice/governance/` (agents, permissions, guardrails, classification, audit) | **ch. 8** — agent identity, permissions, guardrails, audit log |
| Agent loop + HTTP facade | `src/coffice/agent/` (`agent_loop.py`, `llm_client.py`, `agent_api.py`) | **ch. 3/4** — session loop, tool calling, chat API |
| Sidebar shell (LO extension) | `extension/` + `src/coffice/sidebar/` | **ch. 9** — Agent Deck inside the LO window |
| React Agent Deck UI | `ui/` | **ch. 9** — chat/tools/diff/history panels, export dialog |

Closed loop: the React sidebar (ch. 9) talks to the agent HTTP facade
(`agent_api.py`), which runs a session loop (`agent_loop.py`) calling the very
same MCP tools (ch. 4) the stdio server exposes; those tools edit the document
through the PyUNO wrapper (ch. 5), pass through governance (ch. 8), and record
version snapshots via the `co` CLI (ch. 14). `exportDoc` implements the
ADR-005 export flow (PURE copy + `.co-bundle` companion).

---

## 4. MVP scope boundaries (per planning doc 10.2 "不做")

Phase 1 deliberately **excludes**:

- **Streaming / Ghost Text** — the LLM client pins `stream: false`; the
  document is edited via explicit tool calls, never streamed token-by-token.
  (`llm_client.py` — "non-streaming is a Phase 1 requirement").
- **Vision / `renderTile`** — the `renderTile` MCP tool is registered but
  returns a structured error ("requires Phase 2 (LOKit tiled rendering)"). No
  image understanding of the page.
- **Multi-user / multi-agent** — one human operator + one `AI-Writer` agent,
  one document, one co repository.
- **Branch/merge/tag** — upstream `co` does not implement them; the wrapper
  raises `CoUnsupportedError` and the tools surface a readable error (the fake
  test binary emulates them so the API is exercised in unit tests).
- **WebView sidebar** — the MVP LO sidebar panel is a native launcher/status
  panel (no WebView exists on the UNO surface); the React Agent Deck runs in
  the system browser and the postMessage document channel
  (`openDoc`/`focus`/`save`) is a contract the shell implements natively.

What Phase 1 **does** deliver (doc 10.2 checklist): a usable sidebar AI chat,
basic read/write tools, automatic per-round snapshots + manual rollback, and
the ADR-005 export dialog.

---

## 5. Known limitations and next steps toward Phase 2

### Known limitations (Phase 1)

1. **No streaming UI** — every edit lands as a discrete tool call; long
   generations feel coarse and there is no incremental Ghost Text feedback.
2. **No vision** — `renderTile` is stubbed; the agent cannot "see" layout,
   images, or PDF-style page appearance.
3. **Single document / single agent** — the registry is keyed by `docId` but
   the MVP wires one `"default"` document and one `AI-Writer` scope.
4. **`co` binary required for version tools** — `snapshot`/`rollback`/export
   need the external CLI (installed by `scripts/install_co.sh`); without it
   the tools return the install hint and `make smoke` reports `SKIPPED`.
5. **stdlib HTTP facade** — the agent API is `http.server`-based (no FastAPI
   middleware, no auth); fine for a localhost MVP.
6. **Tracked changes accumulate** — every edit runs with LO redlines on; the
   UI can list them but Phase 1 has no one-click accept-all workflow.
7. **Document offsets are state-dependent** — character offsets from
   `getOutline`/`getSelection` are only valid for the document state at the
   moment they were produced; re-read after edits.
8. **Audit log off by default** — `COFFICE_AUDIT_LOG` must be set to record
   doc 8.4 events.

### Next steps toward Phase 2

- **Streaming + Ghost Text** — streamed chat completions with optimistic
  document rendering (the message loop and `LLMClient` are already shaped for
  a pluggable transport).
- **Vision (`renderTile`)** — LOKit tiled rendering to give the agent page
  awareness, then multimodal model support.
- **FastAPI facade** — richer API surface (auth, streaming, SSE), replacing
  the stdlib server when the UI needs it.
- **Multi-document / multi-agent** — explicit docId lifecycle, session
  persistence, and multiple agent scopes with per-agent permissions.
- **WebView sidebar** — a real in-window Agent Deck when a WebView host is
  available in a future LibreOffice (the postMessage contract is already
  defined in `src/coffice/sidebar/contract.py`).
- **Editor UX** — accept/reject-all redline workflows, richer diff
  visualization, and collaborative undo.

---

## 6. Repository layout

```
extension/          LibreOffice sidebar extension shell (build.sh -> dist/Coffice.oxt)
src/coffice/        Python package (core, mcp, agent, governance, versioning, sidebar)
ui/                 React Agent Deck (Vite + Vitest)
scripts/            install_co.sh, smoke_route1.sh
tests/              pytest suite (unit + LO/co-gated integration)
docs/route1.md      this guide
Makefile            make setup / lint / test / run-* / build-oxt / smoke
.github/workflows/  CI (lint, pytest, UI build+tests, extension build, smoke)
```
