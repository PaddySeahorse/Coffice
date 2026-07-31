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
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from coffice.mcp import tools_read
from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry
from coffice.versioning.co_client import CoClient, default_client

logger = logging.getLogger(__name__)

SERVER_NAME = "coffice"
SERVER_VERSION = "0.1.0"

SERVER_INSTRUCTIONS = (
    "Coffice MCP server: you are connected to a LibreOffice Writer document "
    "and may read it through the get* tools. Read the document with "
    "getOutline/getSelection before suggesting or making edits. This Phase 1 "
    "MVP only exposes read tools; write/version/governance tools land in "
    "later beads. renderTile (AI vision) is NOT supported in the MVP."
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
    name: str = SERVER_NAME,
    version: str = SERVER_VERSION,
) -> FastMCP:
    """Build the FastMCP server with the document registry and read tools.

    Args:
        registry: document registry; defaults to one wired from the env
            (``COFFICE_DOC_PATH`` / ``COFFICE_LO_PORT``).
        co_client: co CLI wrapper used by ``getHistory``/``getDiff``; defaults
            to the standard binary discovery client.
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
    mcp = FastMCP(
        name=name,
        instructions=SERVER_INSTRUCTIONS,
    )
    # FastMCP does not expose a version kwarg; the underlying MCPServer carries
    # it and reports it in the initialize handshake (serverInfo.version).
    mcp._mcp_server.version = version  # type: ignore[attr-defined]
    _register_tools(mcp, registry, co_client)
    return mcp


def _register_tools(
    mcp: FastMCP, registry: DocumentRegistry, co_client: CoClient
) -> None:
    """Register the read tools + renderTile stub on ``mcp``.

    The closures below define the JSON-Schema input shapes agents see via
    ``tools/list`` (e.g. ``getSelection`` accepts an optional ``range``
    mapping; ``getDiff`` requires ``h1``/``h2`` hashes); the docstrings become
    the tool descriptions.

    Every document-backed call is guarded: when the document cannot be opened
    (e.g. LibreOffice is not installed yet) the tool returns a structured
    ``{"error": ...}`` result instead of failing the whole request, so the
    agent sees a readable explanation.
    """

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
