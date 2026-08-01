"""MCP server and tool registrations bridging an LLM agent to documents (planning doc ch. 4).

Public API::

    from coffice.mcp import create_server, start_round

    server = create_server()          # document registry wired from env
    server.run(transport="stdio")     # serve over stdio

    round_ = start_round("AI-Writer", "alice")  # per-round snapshot boundary
"""

from coffice.mcp.middleware import Round, RoundStartHook, RoundTracker, start_round
from coffice.mcp.server import (
    GOVERNANCE_TOOL_NAMES,
    READ_TOOL_NAMES,
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    SERVER_VERSION,
    WRITE_TOOL_NAMES,
    create_server,
)
from coffice.mcp.tools_write import (
    FORBIDDEN_OPS,
    RiskLevel,
    classify,
    confirms_required,
    requires_confirmation,
)

__all__ = [
    "FORBIDDEN_OPS",
    "GOVERNANCE_TOOL_NAMES",
    "READ_TOOL_NAMES",
    "SERVER_INSTRUCTIONS",
    "SERVER_NAME",
    "SERVER_VERSION",
    "WRITE_TOOL_NAMES",
    "RiskLevel",
    "Round",
    "RoundStartHook",
    "RoundTracker",
    "classify",
    "confirms_required",
    "create_server",
    "requires_confirmation",
    "start_round",
]
