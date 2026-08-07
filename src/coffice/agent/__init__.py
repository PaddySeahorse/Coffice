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

The doc 4.1 per-round snapshot boundary is enforced here: one
``co commit -m "pre: <round_id>"`` per round, medium/high-risk ops
paused for human confirmation with snapshot-hash rollback. The agent's chat
reply streams to the Agent Deck over SSE (``chat_stream``); document edits
still apply atomically per complete tool call.
"""

from coffice.agent.agent_api import (
    AgentSessions,
    build_handler,
    build_sessions,
    create_http_server,
    handle_chat,
    handle_confirm,
    handle_get_settings,
    handle_set_settings,
    handle_settings_test,
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
    ChatStreamEvent,
    LLMClient,
    LLMConfigError,
    LLMError,
    LLMRequestError,
    ToolCall,
    function_tool,
    parse_chat_response,
    parse_sse_events,
)
from coffice.agent.llm_settings import (
    apply_persisted,
    apply_to_client,
    current_values,
    effective_settings,
    load_settings,
    mask_api_key,
    save_settings,
    settings_path,
)

__all__ = [
    "AgentSession",
    "AgentSessions",
    "AppliedToolCall",
    "ChatMessage",
    "ChatResult",
    "ChatStreamEvent",
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
    "apply_persisted",
    "apply_to_client",
    "build_handler",
    "build_sessions",
    "build_system_prompt",
    "create_http_server",
    "current_values",
    "effective_settings",
    "function_tool",
    "handle_chat",
    "handle_confirm",
    "handle_get_settings",
    "handle_set_settings",
    "handle_settings_test",
    "load_settings",
    "mask_api_key",
    "parse_chat_response",
    "parse_sse_events",
    "save_settings",
    "settings_path",
]
