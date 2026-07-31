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
make smoke        # end-to-end smoke test (lands in the final integration bead)
```

The `co` CLI (version-control snapshots) is an external dependency installed by
`make install-co` / `scripts/install_co.sh` (binary lands in `~/.local/bin/co`).
The Python wrapper (`coffice.versioning`) reads `CO_BIN` and degrades gracefully
with clear instructions when `co` is missing. LibreOffice is also external and
not vendored; see `docs/route1.md` for install instructions.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
