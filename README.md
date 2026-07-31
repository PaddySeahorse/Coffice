# Coffice

Coffice is an AI-first office suite: LibreOffice as the rendering core, a Python
MCP server bridging an LLM agent to the document via PyUNO, and the `co` CLI
(<https://github.com/PaddySeahorse/co>) providing Git-style version-control
snapshots.

## Route 1 (MVP, Phase 1) scope

Route 1 = LibreOffice native app + a sidebar ("Agent Deck") hosting a React UI,
with a PyUNO bridge, an MCP server, and `co` CLI subprocess integration. Phase 1
deliberately excludes streaming/Ghost Text and multimodal vision
(`renderTile`), and is a single-user, single-agent setup.

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

```sh
make setup        # create .venv and install Python deps (uv preferred, pip fallback)
make install-co   # clone + build the co CLI (sets CO_BIN; requires cmake + C++17 toolchain)
make lint         # ruff check
make test         # pytest
make run-mcp      # run the MCP server over stdio
make build-sidebar  # build the React UI (placeholder until the UI bead lands)
make build-oxt    # build the LibreOffice sidebar extension -> dist/Coffice.oxt
make smoke        # end-to-end smoke test (lands in the final integration bead)
```

The `co` CLI (version-control snapshots) is an external dependency installed by
`make install-co` / `scripts/install_co.sh` (binary lands in `~/.local/bin/co`).
The Python wrapper (`coffice.versioning`) reads `CO_BIN` and degrades gracefully
with clear instructions when `co` is missing. LibreOffice is also external and
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
   `http://127.0.0.1:8787/` (served by `make run-ui` once the UI bead lands, or
   any static page). Override with the `COFFICE_UI_URL` environment variable or
   `COFFICE_UI_PORT`:
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
