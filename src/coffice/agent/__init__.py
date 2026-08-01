"""LLM orchestration: client, tool-calling session loop, and HTTP facade (planning doc ch. 3/4).

The agent execution layer takes a user prompt, lets an LLM call the MCP
tools in-process to edit the document, and returns the final reply plus a
change summary. Public API::

    from coffice.agent import AgentSession, LLMClient, create_http_server

    llm = LLMClient()                      # env: COFFICE_LLM_BASE_URL/MODEL/API_KEY
    session = AgentSession(llm_client=llm, server=create_server())
    result = session.chat("Add a section about pricing")  # one round
    result = session.confirm(token)        # resume after needs_confirmation

    from coffice.agent import build_sessions
    httpd = create_http_server(build_sessions())  # POST /chat, /confirm

The non-streaming MVP (doc 10.2: no streaming/Ghost Text; whole-paragraph
inserts only) and the doc 4.1 per-round snapshot boundary are both enforced
here: one ``co commit -m "pre: <round_id>"`` per round, medium/high-risk ops
paused for human confirmation with snapshot-hash rollback.
"""

from coffice.agent.agent_api import (
    AgentSessions,
    build_handler,
    build_sessions,
    create_http_server,
    handle_chat,
    handle_confirm,
)
from coffice.agent.agent_loop import (
    DEFAULT_MAX_TOOL_CALLS,
    HUMAN_ONLY_TOOLS,
    AgentSession,
    AppliedToolCall,
    ChatResult,
    McpToolExecutor,
    PendingConfirmation,
    ToolSpec,
    build_system_prompt,
)
from coffice.agent.llm_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    ChatMessage,
    LLMClient,
    LLMConfigError,
    LLMError,
    LLMRequestError,
    ToolCall,
    function_tool,
    parse_chat_response,
)

__all__ = [
    "AgentSession",
    "AgentSessions",
    "AppliedToolCall",
    "ChatMessage",
    "ChatResult",
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_MODEL",
    "HUMAN_ONLY_TOOLS",
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMRequestError",
    "McpToolExecutor",
    "PendingConfirmation",
    "ToolCall",
    "ToolSpec",
    "build_handler",
    "build_sessions",
    "build_system_prompt",
    "create_http_server",
    "function_tool",
    "handle_chat",
    "handle_confirm",
    "parse_chat_response",
]
