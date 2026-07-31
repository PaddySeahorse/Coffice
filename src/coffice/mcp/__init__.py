"""MCP server and tool registrations bridging an LLM agent to documents (planning doc ch. 4).

Public API::

    from coffice.mcp import create_server, start_round

    server = create_server()          # document registry wired from env
    server.run(transport="stdio")     # serve over stdio

    round_ = start_round("AI-Writer", "alice")  # per-round snapshot boundary
"""

from coffice.mcp.middleware import Round, RoundTracker, start_round
from coffice.mcp.server import (
    READ_TOOL_NAMES,
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    SERVER_VERSION,
    create_server,
)

__all__ = [
    "READ_TOOL_NAMES",
    "SERVER_INSTRUCTIONS",
    "SERVER_NAME",
    "SERVER_VERSION",
    "Round",
    "RoundTracker",
    "create_server",
    "start_round",
]
