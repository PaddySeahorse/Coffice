# Coffice

![CI](https://github.com/PaddySeahorse/Coffice/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-AGPL--3.0-orange.svg)

Coffice is an AI-first office suite: LibreOffice as the rendering core, a Python
MCP server bridging an LLM agent to the document via PyUNO, and the `co` CLI
(<https://github.com/PaddySeahorse/co>) providing Git-style version-control
snapshots.

> Full integration guide (prerequisites, quickstart, architecture mapping to
> the planning doc chapters, MVP scope boundaries, known limitations): see
> [`docs/route1.md`](docs/route1.md).

## Route 1 (MVP, Phase 1) scope

Route 1 = LibreOffice native app + a sidebar ("Agent Deck") hosting a React UI,
with a PyUNO bridge, an MCP server, and `co` CLI subprocess integration. The
agent's chat reply streams to the sidebar over SSE; document edits still apply
atomically per tool call (no Ghost Text, no multimodal vision / `renderTile`),
and the setup is single-user, single-agent.

## Architecture

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

Python package layout under `src/coffice/`:

- `core/` — LibreOffice/PyUNO bridge: soffice lifecycle manager + Document wrapper (planning doc ch. 5)
- `mcp/` — MCP server + tool registrations (ch. 4)
- `versioning/` — `co` CLI subprocess wrapper (ch. 14)
- `governance/` — agent identity, permissions, guardrails, audit log (ch. 8)
- `agent/` — LLM orchestration loop + HTTP facade for the UI (ch. 3/4)
- `sidebar/` — LibreOffice extension shell hosting the React UI (ch. 9)
- `ui/` — React Agent Deck sidebar app (ch. 9)

## Getting started

### Linux (Debian/Ubuntu)

```sh
sudo apt install python3 python3-venv libreoffice python3-uno git cmake g++ zlib1g-dev libssl-dev
```

`python3-uno` makes `uno` importable from the system Python; alternatively use
LibreOffice's bundled interpreter (see `docs/route1.md` §1.2). Debian's stock
`nodejs` is usually below Node 20 — install Node.js 20+ via
[NodeSource](https://github.com/nodesource/distributions) or nvm before
`make setup`.

### macOS and Windows

The Python runtime, LibreOffice bridge, UI, and `co` client support macOS and
Windows. Install Python 3.11+, LibreOffice, Node.js 20+, CMake, and a C++17
toolchain using the platform's package manager or installers. Set
`COFFICE_SOFFICE_BIN` when LibreOffice is installed in a non-standard location.

On macOS, the default LibreOffice application path is detected automatically.
On Windows, `soffice.exe` and `co.exe` are detected from `PATH`, standard
LibreOffice locations, and `%LOCALAPPDATA%`. The Windows Python commands use
`.venv\\Scripts\\python.exe` rather than the Unix `.venv/bin` paths used by the
Makefile:

```powershell
py -3.11 -m venv .venv
.venv\\Scripts\\python.exe -m pip install -e ".[dev]"
.venv\\Scripts\\python.exe -m pytest
npm --prefix ui ci
npm --prefix ui run build
```

Build the extension from Git Bash with `bash extension/build.sh`, or run that
script in the shell provided by your LibreOffice/Unix compatibility layer.
Windows users can install `co` with
`powershell -ExecutionPolicy Bypass -File scripts\\install_co.ps1` and set
`CO_BIN` to the resulting `co.exe`. `make install-co` remains the simplest path
on macOS and Linux.

```sh
make setup        # create .venv, install Python deps, and npm install the Agent Deck UI
make install-co   # clone + build the co CLI (sets CO_BIN; requires cmake + C++17 toolchain)
make lint         # ruff check
make test         # pytest
make run-mcp      # run the MCP server over stdio
make run-agent    # run the agent HTTP facade (chat/confirm + read endpoints)
make run-ui       # serve the React Agent Deck on http://127.0.0.1:8787/
make build-sidebar # build the React UI (Vite) -> ui/dist/
make ui-test      # Vitest unit tests for the Agent Deck
make build-oxt    # build the LibreOffice sidebar extension -> dist/Coffice.oxt
make smoke        # end-to-end Route 1 smoke test (skips gracefully when LO/co are absent)
```

The LLM endpoint is configured via `COFFICE_LLM_BASE_URL` /
`COFFICE_LLM_MODEL` / `COFFICE_LLM_API_KEY` (Ollama, LM Studio, or any
OpenAI-compatible cloud API — see `docs/route1.md` §1.4). These env vars are
the boot-time defaults; the Agent Deck's **Settings** panel can also change
them at runtime (base URL / model / API key, plus a connection test) and
persists the change to `~/.coffice/llm.json` for the next launch.

The `co` CLI (version-control snapshots) is a separate project
(<https://github.com/PaddySeahorse/co>, Git-style snapshots); its quickstart
lives in that repo. `make install-co` (== `bash scripts/install_co.sh`) clones,
builds with CMake, and installs the binary to `~/.local/bin/co` — this takes a
few minutes and needs git, cmake >= 3.16, and a C++17 toolchain (the script
prints the exact `apt` command if a piece is missing). The Python wrapper
(`coffice.versioning`) reads `CO_BIN` (or probes `PATH` / `~/.local/bin`) and
degrades gracefully with clear instructions when `co` is missing, so a failed
build leaves the UI and agent fully usable. LibreOffice is also external and
not vendored; see `docs/route1.md` for install instructions.

## Installing the LibreOffice extension (sidebar shell)

The Coffice sidebar lives inside the LibreOffice window (planning doc 9.1, the
left "Agent Deck"). The shell is an LO extension built from `extension/`; the
hosting contract it implements is documented in
`src/coffice/sidebar/__init__.py` and `src/coffice/sidebar/contract.py`.

### Build

```sh
make build-oxt     # runs extension/build.sh -> dist/Coffice.oxt
```

The build is pure Python (no `zip`/image toolchain required): it packages the
UNO component (`extension/python/coffice_panel.py`) plus the shared hosting
contract copied from `src/coffice/sidebar/`, and generates the deck icon.

### Install

```sh
unopkg add --force dist/Coffice.oxt        # or: make install-oxt
```

Requires LibreOffice >= 6.0 with the Python scripting engine (`python3-uno` on
Debian/Ubuntu, or the bundled interpreter — see `docs/route1.md`).

### Use

1. Open a Writer document.
2. The "Coffice" deck appears in the sidebar tab bar (context: Writer). Click
   the tab, or use **Tools → Coffice** (also the panel title-bar menu) to
   toggle the deck.
3. The panel shows the Agent Deck URL it loads — by default
   `http://127.0.0.1:8787/` (served by `make run-ui`; see `ui/README.md`).
   Override with the `COFFICE_UI_URL` environment variable or `COFFICE_UI_PORT`:
   ```sh
   COFFICE_UI_URL=http://localhost:3000/ soffice report.docx
   ```
4. The MVP panel is a native launcher/status panel (no WebView exists on the
   UNO surface; see the hosting-decision docstring in
   `src/coffice/sidebar/__init__.py`): **Open Agent Deck** opens the URL in the
   system browser, and **Open Document / Focus / Save** execute the document
   commands of the hosting contract natively.

The React UI bead consumes the contract in `src/coffice/sidebar/contract.py`
(the URL + the `window.postMessage` command/result protocol for `openDoc`,
`focus`, `save`); the shell contains no UI logic.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
