"""End-to-end test: a real MCP stdio client against ``python -m coffice.mcp.server``.

Covers the acceptance criterion "an MCP client can list the 7 read tools".
Listing tools needs no LibreOffice, so this runs everywhere; invoking
``getOutline`` in a sandbox without LO returns the server's structured error
instead of crashing (also asserted). The full document round trip lives in
``test_integration.py``, gated on LO availability.
"""

from __future__ import annotations

import os
import sys

import pytest
from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters

SERVER_ARGS = ["-m", "coffice.mcp.server"]


@pytest.fixture()
async def session():
    """A connected stdio MCP client session to the real server process."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=SERVER_ARGS,
        env={**os.environ, "COFFICE_LOG_LEVEL": "WARNING"},
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            yield client


@pytest.mark.anyio
async def test_stdio_client_lists_7_read_tools(session: ClientSession) -> None:
    tools = await session.list_tools()
    names = [tool.name for tool in tools.tools]
    assert names[:7] == [
        "getOutline",
        "getSelection",
        "getStyles",
        "getTables",
        "getRedlines",
        "getHistory",
        "getDiff",
    ]
    assert "renderTile" in names[7:]
    for tool in tools.tools[:7]:
        assert tool.description
        assert tool.inputSchema.get("type") == "object"


@pytest.mark.anyio
async def test_stdio_client_get_outline_without_lo_returns_error(
    session: ClientSession,
) -> None:
    """Without LibreOffice the tool must respond (structured error), not crash."""
    result = await session.call_tool("getOutline", {"docId": "default"})
    text = "".join(block.text for block in result.content if hasattr(block, "text"))
    assert text, "expected a text result from getOutline"
