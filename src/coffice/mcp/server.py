"""MCP server bridging an LLM agent to the document (planning doc ch. 4).

This is the agent bridge: the LLM reaches the document *exclusively* through
the tools registered here (doc 4.1: discoverability -- every tool carries a
name, description and JSON-Schema input so agents can discover capabilities
via ``tools/list``).

Startup
-------
Run over stdio (used by MCP clients such as Claude Desktop)::

    python -m coffice.mcp.server

Configuration comes from the environment:

- ``COFFICE_DOC_PATH`` -- path of the .docx/.odt to open for the ``"default"``
  docId (opened on demand at the first tool call; if unset an empty in-memory
  document is created).
- ``COFFICE_LO_PORT``  -- UNO socket port for the LibreOffice instance
  (defaults to the core's ``2002``).
- ``COFFICE_LOG_LEVEL`` -- logging verbosity (default ``INFO``).

The registry opens documents lazily, so the server starts over stdio even
before LibreOffice is up; tools that need the document return a structured
error until it can be opened.

Write tools + snapshot middleware
---------------------------------
The server also registers the write tools (planning doc 4.2) and the
per-round snapshot middleware (doc 4.1): starting a round with
``start_round`` fires a hook that takes exactly one ``pre: <round_id>``
``co commit`` per round; medium/high-risk edits additionally snapshot
immediately before execution and require ``confirmOp``/``rejectOp``
confirmation (see ``coffice.mcp.tools_write``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from coffice.mcp import tools_read, tools_write
from coffice.mcp.middleware import RoundTracker, get_tracker
from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry
from coffice.versioning.co_client import CoClient, default_client

logger = logging.getLogger(__name__)

SERVER_NAME = "coffice"
SERVER_VERSION = "0.1.0"

SERVER_INSTRUCTIONS = (
    "Coffice MCP server: you are connected to a LibreOffice Writer document "
    "and may read it through the get* tools and edit it through the write "
    "tools (insertText, replaceRange, applyStyle, generateTable, "
    "refactorSection, setPageLayout). Read the document with "
    "getOutline/getSelection before making edits. Medium/high-risk edits "
    "return {status: needs_confirmation} and only run after confirmOp; "
    "version snapshots happen once per round, not per tool call. "
    "renderTile (AI vision) is NOT supported in the MVP."
)

#: The 7 read tools required by the acceptance criteria, in registration order.
READ_TOOL_NAMES = (
    "getOutline",
    "getSelection",
    "getStyles",
    "getTables",
    "getRedlines",
    "getHistory",
    "getDiff",
)

#: The 6 write tools (planning doc 4.2 write table) + confirmation helpers.
WRITE_TOOL_NAMES = (
    "insertText",
    "replaceRange",
    "applyStyle",
    "generateTable",
    "refactorSection",
    "setPageLayout",
    "confirmOp",
    "rejectOp",
)


def _env_doc_path() -> str | None:
    value = os.environ.get("COFFICE_DOC_PATH")
    return value or None


def _env_lo_port() -> int | None:
    value = os.environ.get("COFFICE_LO_PORT")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        logger.warning("ignoring non-integer COFFICE_LO_PORT=%r", value)
        return None


def create_server(
    registry: DocumentRegistry | None = None,
    co_client: CoClient | None = None,
    round_tracker: RoundTracker | None = None,
    name: str = SERVER_NAME,
    version: str = SERVER_VERSION,
) -> FastMCP:
    """Build the FastMCP server with the document registry and MCP tools.

    Args:
        registry: document registry; defaults to one wired from the env
            (``COFFICE_DOC_PATH`` / ``COFFICE_LO_PORT``).
        co_client: co CLI wrapper used by the version read tools and the
            write-tools snapshot middleware; defaults to the standard binary
            discovery client.
        round_tracker: tracker whose ``start_round`` fires the pre-round
            snapshot hook; defaults to the process-wide tracker (so the
            module-level :func:`coffice.mcp.start_round` snapshots too).
        name: MCP server name (default ``"coffice"``).
        version: MCP server version (default ``"0.1.0"``).

    Returns a fully-registered FastMCP instance. Call ``server.run(
    transport="stdio")`` to serve, or use ``server.list_tools()`` /
    ``server.call_tool()`` directly in-process (tests do this).
    """
    registry = registry or DocumentRegistry(
        doc_path=_env_doc_path(), lo_port=_env_lo_port()
    )
    co_client = co_client or default_client()
    tracker = round_tracker or get_tracker()
    mcp = FastMCP(
        name=name,
        instructions=SERVER_INSTRUCTIONS,
    )
    # FastMCP does not expose a version kwarg; the underlying MCPServer carries
    # it and reports it in the initialize handshake (serverInfo.version).
    mcp._mcp_server.version = version  # type: ignore[attr-defined]
    _register_tools(mcp, registry, co_client, tracker)
    return mcp


def _register_tools(
    mcp: FastMCP,
    registry: DocumentRegistry,
    co_client: CoClient,
    tracker: RoundTracker,
) -> None:
    """Register the read, write, and confirmation tools on ``mcp``.

    The closures below define the JSON-Schema input shapes agents see via
    ``tools/list`` (e.g. ``getSelection`` accepts an optional ``range``
    mapping; ``getDiff`` requires ``h1``/``h2`` hashes); the docstrings become
    the tool descriptions.

    Every document-backed call is guarded: when the document cannot be opened
    (e.g. LibreOffice is not installed yet) the tool returns a structured
    ``{"error": ...}`` result instead of failing the whole request, so the
    agent sees a readable explanation.

    The write tools share one :class:`tools_write.WriteContext` (pending
    confirmations, audit callback) and register the pre-round snapshot hook
    on ``tracker``: each ``start_round`` produces exactly one
    ``pre: <round_id>`` commit.
    """
    write_ctx = tools_write.WriteContext(registry=registry, co_client=co_client)

    def _round_snapshot_hook(round_) -> None:
        try:
            results = tools_write.pre_round_snapshot(round_, write_ctx)
            logger.info(
                "pre-round snapshot: round_id=%s results=%s",
                round_.round_id,
                results,
            )
        except Exception:  # noqa: BLE001 - a hook must never break the round
            logger.exception(
                "pre-round snapshot hook failed: round_id=%s", round_.round_id
            )

    tracker.add_round_start_hook(_round_snapshot_hook)

    def _guarded(
        handler: Callable[..., dict[str, Any]],
        doc_id: str,
        *args: Any,
    ) -> dict[str, Any]:
        try:
            return handler(registry, doc_id, *args)
        except Exception as exc:  # noqa: BLE001 - surface any open/read failure
            logger.warning("read tool failed for docId=%r: %s", doc_id, exc)
            return {"docId": doc_id, "error": f"{type(exc).__name__}: {exc}"}

    def _guarded_write(
        handler: Callable[..., dict[str, Any]],
        doc_id: str,
        *args: Any,
    ) -> dict[str, Any]:
        try:
            return handler(write_ctx, doc_id, *args)
        except Exception as exc:  # noqa: BLE001 - surface any open/write failure
            logger.warning("write tool failed for docId=%r: %s", doc_id, exc)
            return {
                "ok": False,
                "docId": doc_id,
                "error": f"{type(exc).__name__}: {exc}",
            }

    @mcp.tool(
        name="getOutline",
        description=(
            "Return the document outline: headings with character offsets "
            "(start/end into the full text), level, paragraph style, and text. "
            "Use this first to understand the document structure."
        ),
    )
    def get_outline(docId: str = DEFAULT_DOC_ID) -> dict[str, Any]:
        return _guarded(tools_read.get_outline, docId)

    @mcp.tool(
        name="getSelection",
        description=(
            "Return the text and style of a character range "
            "({start, end} offsets into the full text); omit range for the "
            "whole document."
        ),
    )
    def get_selection(
        docId: str = DEFAULT_DOC_ID,
        range: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        return _guarded(tools_read.get_selection, docId, range)

    @mcp.tool(
        name="getStyles",
        description=(
            "Return the available paragraph and character style names of the "
            "document, for use as styleId in edits."
        ),
    )
    def get_styles(docId: str = DEFAULT_DOC_ID) -> dict[str, Any]:
        return _guarded(tools_read.get_styles, docId)

    @mcp.tool(
        name="getTables",
        description=(
            "Return all tables in the document: name, row/column counts, and "
            "cell contents (cells[row][col])."
        ),
    )
    def get_tables(docId: str = DEFAULT_DOC_ID) -> dict[str, Any]:
        return _guarded(tools_read.get_tables, docId)

    @mcp.tool(
        name="getRedlines",
        description=(
            "Return the tracked-changes (redline) list: id, type, author, "
            "timestamp, text, comment."
        ),
    )
    def get_redlines(docId: str = DEFAULT_DOC_ID) -> dict[str, Any]:
        return _guarded(tools_read.get_redlines, docId)

    @mcp.tool(
        name="getHistory",
        description=(
            "Return the co version history of the document as a commit list "
            "(hash, author, message, timestamp), newest first; limit caps the "
            "number of commits."
        ),
    )
    def get_history(
        docId: str = DEFAULT_DOC_ID, limit: int | None = None
    ) -> dict[str, Any]:
        return _guarded(tools_read.get_history, docId, co_client, limit)

    @mcp.tool(
        name="getDiff",
        description=(
            "Return the diff between two commit hashes (h1, h2) as changed "
            "zip-internal paths (e.g. word/document.xml) with A/D/M status."
        ),
    )
    def get_diff(
        h1: str, h2: str, docId: str = DEFAULT_DOC_ID
    ) -> dict[str, Any]:
        return _guarded(tools_read.get_diff, docId, co_client, h1, h2)

    @mcp.tool(
        name="renderTile",
        description=(
            "AI vision tile rendering (planning doc 6.2). NOT supported in "
            "the Phase 1 MVP; returns a structured error. Requires Phase 2 "
            "(LOKit tiled rendering)."
        ),
    )
    def render_tile(
        docId: str = DEFAULT_DOC_ID,
        page: int = 1,
        x: int = 0,
        y: int = 0,
        width: int = 512,
        height: int = 512,
    ) -> dict[str, Any]:
        return tools_read.render_tile(docId, page, x, y, width, height)

    # ------------------------------------------------------------------
    # write tools (planning doc 4.2 write table) + confirmation helpers
    # ------------------------------------------------------------------

    @mcp.tool(
        name="insertText",
        description=(
            "Insert text at a character offset into the full document text "
            "(pos may be an integer offset or the string 'end'). Optional "
            "style is a paragraph style name. Low risk: runs immediately "
            "with track changes on."
        ),
    )
    def insert_text(
        docId: str = DEFAULT_DOC_ID,
        pos: int | str = 0,
        text: str = "",
        style: str | None = None,
    ) -> dict[str, Any]:
        return _guarded_write(tools_write.insert_text, docId, pos, text, style)

    @mcp.tool(
        name="replaceRange",
        description=(
            "Replace the text in a range ({start, end} character offsets) "
            "with new text. Deleting more than 50 characters is medium risk "
            "and returns needs_confirmation; a range covering the whole "
            "document is high risk (double confirm)."
        ),
    )
    def replace_range(
        docId: str = DEFAULT_DOC_ID,
        range: dict[str, int] | None = None,
        text: str = "",
    ) -> dict[str, Any]:
        return _guarded_write(
            tools_write.replace_range, docId, range or {"start": 0, "end": 0}, text
        )

    @mcp.tool(
        name="applyStyle",
        description=(
            "Apply a paragraph style (styleId from getStyles) to all "
            "paragraphs in a range ({start, end} character offsets). "
            "Low risk: runs immediately with track changes on."
        ),
    )
    def apply_style(
        docId: str = DEFAULT_DOC_ID,
        range: dict[str, int] | None = None,
        styleId: str = "",
    ) -> dict[str, Any]:
        return _guarded_write(
            tools_write.apply_style, docId, range or {"start": 0, "end": 0}, styleId
        )

    @mcp.tool(
        name="generateTable",
        description=(
            "Append a new table with rows x cols cells, optionally filled "
            "from data (list of rows of cell values). Low risk: runs "
            "immediately with track changes on."
        ),
    )
    def generate_table(
        docId: str = DEFAULT_DOC_ID,
        rows: int = 2,
        cols: int = 2,
        data: list[list[Any]] | None = None,
        style: str | None = None,
    ) -> dict[str, Any]:
        return _guarded_write(
            tools_write.generate_table, docId, rows, cols, data, style
        )

    @mcp.tool(
        name="refactorSection",
        description=(
            "Replace an entire section (sectionId is the index into "
            "getOutline) with the given instruction output; the agent "
            "pre-computes the replacement text and passes it as instruction. "
            "High risk (deletes a whole section): double confirmation "
            "required."
        ),
    )
    def refactor_section(
        docId: str = DEFAULT_DOC_ID,
        sectionId: int = 0,
        instruction: str = "",
    ) -> dict[str, Any]:
        return _guarded_write(
            tools_write.refactor_section, docId, sectionId, instruction
        )

    @mcp.tool(
        name="setPageLayout",
        description=(
            "Set page layout options: margins {top,bottom,left,right} in mm, "
            "page_size {width,height} in mm, and columns (int). Low risk: "
            "runs immediately."
        ),
    )
    def set_page_layout(
        docId: str = DEFAULT_DOC_ID,
        opts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _guarded_write(tools_write.set_page_layout, docId, opts or {})

    @mcp.tool(
        name="confirmOp",
        description=(
            "Confirm a pending medium/high-risk operation returned as "
            "{status: needs_confirmation, token} by a write tool. High-risk "
            "operations need two confirms. On confirmation the operation "
            "executes and returns the edit result."
        ),
    )
    def confirm_op(
        token: str,
        docId: str = DEFAULT_DOC_ID,
    ) -> dict[str, Any]:
        return _guarded_write(tools_write.confirm_op, docId, token)

    @mcp.tool(
        name="rejectOp",
        description=(
            "Reject a pending operation (its token came from a "
            "needs_confirmation response). The pre-op snapshot is checked "
            "back out via co, rolling the document back to before the "
            "operation."
        ),
    )
    def reject_op(
        token: str,
        docId: str = DEFAULT_DOC_ID,
    ) -> dict[str, Any]:
        return _guarded_write(tools_write.reject_op, docId, token)


def main() -> None:
    """CLI entrypoint: ``python -m coffice.mcp.server`` (stdio transport)."""
    logging.basicConfig(
        level=os.environ.get("COFFICE_LOG_LEVEL", "INFO").upper()
    )
    logger.info(
        "starting coffice MCP server (doc_path=%r, lo_port=%r)",
        _env_doc_path(),
        _env_lo_port(),
    )
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
