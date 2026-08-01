"""Per-conversation agent session loop (planning doc 3.3 Agent Flow + ch. 4).

:class:`AgentSession` holds the state of one conversation with the LLM and
drives the tool-calling loop that turns a user prompt into document edits:

1. :meth:`AgentSession.chat` calls ``start_round`` on the round tracker
   (doc 4.1), firing the middleware hook that takes exactly one
   ``co commit -m "pre: <round_id>"`` snapshot before the round's first edit;
2. it builds the system prompt (agent identity, the *live* MCP tool list with
   schemas, and the read-first / one-change-per-call / no-forbidden-ops rules);
3. it calls the LLM with the tools and executes each requested tool call
   against the MCP tool implementations *in-process* (no subprocess MCP in the
   MVP -- :class:`McpToolExecutor` calls the very same registered handlers the
   stdio server uses, so governance guardrails, rate limits, protected
   regions, redlines, and the snapshot middleware all apply);
4. it loops until the model stops calling tools, then returns the final
   assistant message plus a session change summary (the list of applied tool
   calls, in order);
5. when a write tool returns ``{status: "needs_confirmation", token}`` the
   loop pauses and surfaces the confirmation to the caller (the UI shows a
   confirm dialog); :meth:`AgentSession.confirm` / :meth:`AgentSession.reject`
   resume the loop after the human decides, with the pre-op snapshot hash
   backing the rollback.

Tool-call history is kept in OpenAI message format, so any compatible endpoint
(Ollama, LM Studio, OpenAI, Anthropic-compatible proxies) can resume the
conversation across confirmations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from coffice.agent.llm_client import ChatMessage, LLMError, ToolCall
from coffice.governance import AI_WRITER_ID
from coffice.mcp.middleware import Round, RoundTracker, get_tracker
from coffice.mcp.registry import DEFAULT_DOC_ID

logger = logging.getLogger(__name__)

#: tools resolved by the human (UI confirm dialog), never offered to the model.
HUMAN_ONLY_TOOLS = frozenset({"confirmOp", "rejectOp"})

#: default cap on tool calls per round (safety valve against runaway loops).
DEFAULT_MAX_TOOL_CALLS = 24


class ChatCompleter(Protocol):
    """Anything with an OpenAI-style ``chat`` (LLMClient, scripted fakes)."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatMessage: ...


class ToolExecutor(Protocol):
    """In-process MCP tool runner (McpToolExecutor, scripted fakes)."""

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    def list_tools(self) -> list[ToolSpec]: ...


@dataclass(frozen=True)
class ToolSpec:
    """Self-describing tool as exposed by the MCP server (doc 4.1)."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        from coffice.agent.llm_client import function_tool

        return function_tool(self.name, self.description, self.input_schema)


def build_system_prompt(
    tools: list[ToolSpec], agent_id: str = AI_WRITER_ID
) -> str:
    """Compose the doc 3.3/4 system prompt: identity + live tool list + rules."""
    lines = [
        f"You are {agent_id}, an AI writing assistant collaborating with a "
        "human editor through symmetric access to a LibreOffice Writer "
        "document. You edit the document exclusively by calling the tools "
        "below; you never write text directly in this chat.",
        "",
        "Rules:",
        "1. Read before you write: call getOutline/getSelection to understand "
        "the document before making any edit.",
        "2. Make one logical change per tool call; keep edits to whole "
        "paragraphs (this session is non-streaming).",
        "3. Never attempt destructive or forbidden operations (they are "
        "blocked by governance before any edit): no clearDocument, no "
        "encryption/signing/macro, no operations outside this tool list.",
        "4. When a tool returns {status: needs_confirmation}, the human must "
        "approve the change; stop calling tools and wait.",
        "5. Version snapshots happen once per round, not per tool call.",
        "",
        "Available tools:",
    ]
    if not tools:
        lines.append("- (none)")
    for tool in tools:
        schema = json.dumps(tool.input_schema, ensure_ascii=False)
        lines.append(f"- {tool.name}: {tool.description} Parameters: {schema}")
    lines.append("")
    lines.append(
        "After your edits, reply with a concise summary of what you changed "
        "for the human."
    )
    return "\n".join(lines)


def _server_call(server: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke a FastMCP tool in-process and return its structured dict result."""
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    if isinstance(result, dict):
        return result
    raise TypeError(
        f"unexpected result from server.call_tool({name!r}): {type(result).__name__}"
    )


class McpToolExecutor:
    """Executes MCP tools against a FastMCP server without a subprocess.

    This is the MVP's in-process bridge: the agent loop calls the *same*
    registered handlers the stdio MCP server would, so middleware (pre-round
    snapshot, governance guard, redlines) behaves identically.
    """

    def __init__(self, server: Any) -> None:
        self._server = server
        self._tools: list[ToolSpec] | None = None

    def list_tools(self) -> list[ToolSpec]:
        if self._tools is None:
            specs: list[ToolSpec] = []
            for tool in asyncio.run(self._server.list_tools()):
                name = getattr(tool, "name", None)
                if not name or name in HUMAN_ONLY_TOOLS:
                    continue
                specs.append(
                    ToolSpec(
                        name=str(name),
                        description=str(getattr(tool, "description", "") or ""),
                        input_schema=dict(
                            getattr(tool, "inputSchema", None) or {}
                        ),
                    )
                )
            self._tools = specs
        return self._tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return _server_call(self._server, name, arguments)


# ============================================================================
# Result + confirmation records
# ============================================================================


@dataclass
class AppliedToolCall:
    """One executed tool call recorded in the session change summary."""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "result": self.result,
            "ok": self.ok,
        }


@dataclass
class PendingConfirmation:
    """A ``needs_confirmation`` response awaiting the human's decision."""

    token: str
    tool: str
    doc_id: str
    snapshot_hash: str | None
    summary: str
    tool_call_id: str
    #: index into ``session._messages`` of the tool message to update on
    #: confirm/reject, so the conversation history stays consistent.
    message_index: int


@dataclass
class ChatResult:
    """Outcome of one chat turn / confirmation step."""

    #: ``"complete"`` | ``"needs_confirmation"`` | ``"error"``
    status: str
    reply: str = ""
    change_summary: list[dict[str, Any]] = field(default_factory=list)
    round_id: str | None = None
    needs_confirmation: bool = False
    confirmation: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reply": self.reply,
            "change_summary": self.change_summary,
            "round_id": self.round_id,
            "needs_confirmation": self.needs_confirmation,
            "confirmation": self.confirmation,
            "error": self.error,
        }


def _json_content(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


# ============================================================================
# Session
# ============================================================================


class AgentSession:
    """One conversation: message history, round state, and the tool loop.

    Args:
        llm_client: chat-completer (an :class:`LLMClient` or any object with
            a ``chat(messages, tools)`` method -- tests use scripted fakes).
        server: FastMCP server whose registered tools the loop executes
            in-process (mutually exclusive with ``tool_executor``).
        tool_executor: alternative in-process tool runner (tests inject a
            fake here); exactly one of ``server``/``tool_executor`` is needed.
        agent_id: governance agent identity (default ``"AI-Writer"``).
        human_operator: the human driving the conversation (audit field 8.4).
        round_tracker: tracker whose ``start_round`` fires the pre-round
            snapshot hook (default: the process-wide tracker, which
            ``create_server`` wires by default too).
        system_prompt: override the generated system prompt.
        max_tool_calls: safety cap on tool calls per round.
    """

    def __init__(
        self,
        llm_client: ChatCompleter,
        server: Any = None,
        tool_executor: ToolExecutor | None = None,
        *,
        agent_id: str = AI_WRITER_ID,
        human_operator: str | None = None,
        round_tracker: RoundTracker | None = None,
        system_prompt: str | None = None,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        session_id: str | None = None,
    ) -> None:
        if server is None and tool_executor is None:
            raise ValueError("AgentSession needs a `server` or `tool_executor`")
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.agent_id = agent_id
        self.human_operator = human_operator
        self.max_tool_calls = max_tool_calls
        self._tracker = round_tracker or get_tracker()
        self._llm = llm_client
        self._executor = tool_executor or McpToolExecutor(server)
        self._tools: list[dict[str, Any]] = [
            spec.to_openai() for spec in self._executor.list_tools()
        ]
        self._system_prompt = system_prompt or build_system_prompt(
            self._executor.list_tools(), agent_id=agent_id
        )
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt}
        ]
        self._pending: PendingConfirmation | None = None
        self._change_summary: list[dict[str, Any]] = []
        self._round: Round | None = None

    # -- public API ---------------------------------------------------------

    def chat(self, user_message: str) -> ChatResult:
        """Start a new round for ``user_message`` and run the tool loop."""
        if self._pending is not None:
            return self._error_result(
                "a change is awaiting confirmation; call confirm() or reject() "
                "before sending a new message"
            )
        if not user_message or not str(user_message).strip():
            return self._error_result("empty user message")
        self._round = self._tracker.start_round(
            self.agent_id, self.human_operator
        )
        self._messages.append({"role": "user", "content": str(user_message)})
        logger.info(
            "session %s: round %s started (agent=%s operator=%s)",
            self.session_id,
            self._round.round_id,
            self.agent_id,
            self.human_operator,
        )
        return self._run_loop()

    def confirm(self, token: str) -> ChatResult:
        """Confirm a pending operation, then resume the tool loop."""
        if self._pending is None or self._pending.token != token:
            return self._error_result(
                f"no pending confirmation for token {token!r}"
            )
        logger.info(
            "session %s: confirming %s (token=%s)",
            self.session_id,
            self._pending.tool,
            token,
        )
        result = self._executor.call(
            "confirmOp", {"token": token, "docId": self._pending.doc_id}
        )
        return self._resolve_confirmation(result)

    def reject(self, token: str) -> ChatResult:
        """Reject a pending operation (rolls back via its snapshot hash)."""
        if self._pending is None or self._pending.token != token:
            return self._error_result(
                f"no pending confirmation for token {token!r}"
            )
        logger.info(
            "session %s: rejecting %s (token=%s)",
            self.session_id,
            self._pending.tool,
            token,
        )
        result = self._executor.call(
            "rejectOp", {"token": token, "docId": self._pending.doc_id}
        )
        return self._resolve_confirmation(result)

    def pending_confirmation(self) -> dict[str, Any] | None:
        """The outstanding confirmation, or ``None``."""
        if self._pending is None:
            return None
        return {
            "token": self._pending.token,
            "tool": self._pending.tool,
            "summary": self._pending.summary,
            "snapshot_hash": self._pending.snapshot_hash,
        }

    # -- internals ----------------------------------------------------------

    def _run_loop(self) -> ChatResult:
        for _ in range(self.max_tool_calls):
            try:
                response = self._llm.chat(self._messages, tools=self._tools or None)
            except LLMError as exc:
                logger.warning(
                    "session %s: LLM call failed: %s", self.session_id, exc
                )
                return self._error_result(f"LLM call failed: {exc}")
            if not isinstance(response, ChatMessage):
                raise TypeError(
                    "chat() must return ChatMessage; got "
                    f"{type(response).__name__}"
                )
            if not response.has_tool_calls:
                reply = response.content or ""
                self._messages.append(
                    {"role": "assistant", "content": reply}
                )
                return ChatResult(
                    status="complete",
                    reply=reply,
                    change_summary=list(self._change_summary),
                    round_id=self._round.round_id if self._round else None,
                )
            self._append_assistant_turn(response)
            for tool_call in response.tool_calls:
                outcome = self._apply_tool_call(tool_call)
                if outcome.get("status") == "needs_confirmation":
                    return self._confirmation_result(outcome, tool_call)
        logger.warning(
            "session %s: tool loop hit max_tool_calls=%d",
            self.session_id,
            self.max_tool_calls,
        )
        return ChatResult(
            status="complete",
            reply="",
            change_summary=list(self._change_summary),
            round_id=self._round.round_id if self._round else None,
        )

    def _append_assistant_turn(self, response: ChatMessage) -> None:
        self._messages.append(
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
        )

    def _apply_tool_call(self, tool_call: ToolCall) -> dict[str, Any]:
        logger.info(
            "session %s: executing tool %s(%s)",
            self.session_id,
            tool_call.name,
            tool_call.arguments,
        )
        try:
            result = self._executor.call(tool_call.name, tool_call.arguments)
        except Exception as exc:  # noqa: BLE001 - surface the tool failure to the LLM
            logger.warning(
                "session %s: tool %s failed: %s", self.session_id, tool_call.name, exc
            )
            result = {"ok": False, "tool": tool_call.name, "error": f"{type(exc).__name__}: {exc}"}
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": tool_call.name,
                "content": _json_content(result),
            }
        )
        if result.get("status") == "needs_confirmation":
            self._pending = PendingConfirmation(
                token=str(result.get("token", "")),
                tool=tool_call.name,
                doc_id=str(result.get("docId") or DEFAULT_DOC_ID),
                snapshot_hash=result.get("snapshot_hash"),
                summary=str(result.get("summary", "")),
                tool_call_id=tool_call.id,
                message_index=len(self._messages) - 1,
            )
            logger.info(
                "session %s: %s needs human confirmation (token=%s)",
                self.session_id,
                tool_call.name,
                self._pending.token,
            )
            return result
        self._change_summary.append(
            AppliedToolCall(
                tool=tool_call.name,
                arguments=tool_call.arguments,
                result=result,
                ok=bool(result.get("ok", True)),
            ).to_dict()
        )
        return result

    def _resolve_confirmation(self, result: dict[str, Any]) -> ChatResult:
        pending = self._pending
        if pending is None:
            return self._error_result("no pending confirmation")
        self._pending = None
        self._messages[pending.message_index]["content"] = _json_content(result)
        if result.get("status") == "needs_confirmation":
            # high-risk ops need a second confirm; keep waiting.
            self._pending = PendingConfirmation(
                token=str(result.get("token") or pending.token),
                tool=pending.tool,
                doc_id=str(result.get("docId") or pending.doc_id),
                snapshot_hash=result.get("snapshot_hash") or pending.snapshot_hash,
                summary=str(result.get("summary") or pending.summary),
                tool_call_id=pending.tool_call_id,
                message_index=pending.message_index,
            )
            return ChatResult(
                status="needs_confirmation",
                needs_confirmation=True,
                confirmation=result,
                change_summary=list(self._change_summary),
                round_id=self._round.round_id if self._round else None,
            )
        self._change_summary.append(
            AppliedToolCall(
                tool=pending.tool,
                arguments={},
                result=result,
                ok=bool(result.get("ok", True)),
            ).to_dict()
        )
        return self._run_loop()

    def _confirmation_result(
        self, outcome: dict[str, Any], tool_call: ToolCall
    ) -> ChatResult:
        return ChatResult(
            status="needs_confirmation",
            needs_confirmation=True,
            confirmation=outcome,
            change_summary=list(self._change_summary),
            round_id=self._round.round_id if self._round else None,
        )

    def _error_result(self, message: str) -> ChatResult:
        return ChatResult(
            status="error",
            reply="",
            error=message,
            change_summary=list(self._change_summary),
            round_id=self._round.round_id if self._round else None,
        )


__all__ = [
    "AI_WRITER_ID",
    "AgentSession",
    "AppliedToolCall",
    "ChatResult",
    "ChatCompleter",
    "DEFAULT_MAX_TOOL_CALLS",
    "HUMAN_ONLY_TOOLS",
    "McpToolExecutor",
    "PendingConfirmation",
    "ToolExecutor",
    "ToolSpec",
    "build_system_prompt",
]
