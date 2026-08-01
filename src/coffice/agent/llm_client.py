"""OpenAI-compatible chat-completions client (planning doc 3.3 / ch. 4).

The agent loop talks to *any* OpenAI-compatible chat-completions endpoint --
Ollama, LM Studio, OpenAI, or an Anthropic-compatible proxy -- through one
thin client. It is deliberately lighter than the ``openai`` SDK: a single
``httpx.post`` to ``{base_url}/chat/completions`` with ``stream: false``
(non-streaming is a Phase 1 requirement; Ghost Text/streaming land in
Phase 2). Tool calling is the only extra feature beyond plain text chat.

Configuration comes from the environment:

- ``COFFICE_LLM_BASE_URL`` -- base URL of the OpenAI-compatible API
  (default ``http://127.0.0.1:11434/v1``, the Ollama convention; LM Studio
  uses ``http://127.0.0.1:1234/v1``, OpenAI ``https://api.openai.com/v1``).
- ``COFFICE_LLM_MODEL``    -- model id (e.g. ``qwen2.5:7b``, ``llama3.1``,
  ``gpt-4o-mini``).
- ``COFFICE_LLM_API_KEY``  -- bearer token; optional (Ollama/LM Studio need
  none).

The transport is pluggable so tests inject a scripted fake without a network;
``LLMClient.chat`` delegates to ``transport(payload)`` when one is given.
:func:`parse_chat_response` turns the raw JSON into a :class:`ChatMessage`
with the tool calls extracted, and is a pure function unit-tested directly.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ENV_BASE_URL = "COFFICE_LLM_BASE_URL"
ENV_MODEL = "COFFICE_LLM_MODEL"
ENV_API_KEY = "COFFICE_LLM_API_KEY"

#: Default endpoint: the Ollama OpenAI-compatible API (``/v1/chat/completions``).
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
#: Default model when COFFICE_LLM_MODEL is unset (a strong local coder).
DEFAULT_MODEL = "qwen2.5:7b"


class LLMError(Exception):
    """Base class for all LLM client errors."""


class LLMConfigError(LLMError):
    """The client was constructed without usable configuration."""


class LLMRequestError(LLMError):
    """The upstream endpoint failed or returned an unparseable response."""


@dataclass(frozen=True)
class ToolCall:
    """One ``function`` tool call requested by the assistant message."""

    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True)
class ChatMessage:
    """A parsed assistant turn: optional text plus zero-or-more tool calls."""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def function_tool(
    name: str, description: str, parameters: Mapping[str, Any]
) -> dict[str, Any]:
    """Build one OpenAI ``tools`` entry from an MCP tool's schema."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": dict(parameters),
        },
    }


def parse_chat_response(payload: Any) -> ChatMessage:
    """Extract a :class:`ChatMessage` from a chat-completions response.

    Handles the standard OpenAI shape (``choices[0].message`` with optional
    ``tool_calls``) plus tolerant fallbacks (a top-level ``message``, or a
    ``content`` string) for the loose proxies the MVP supports. Raises
    :class:`LLMRequestError` when nothing usable is found.
    """
    message: Any = None
    finish: Any = None
    if isinstance(payload, dict) and "content" in payload and "role" in payload:
        message = payload
        finish = payload.get("finish_reason")
    elif isinstance(payload, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            message = choice.get("message") if isinstance(choice, dict) else None
            finish = choice.get("finish_reason")
            if message is None and isinstance(choice, dict):
                message = choice  # some proxies omit the message wrapper
        elif isinstance(payload.get("message"), dict):
            message = payload["message"]
            finish = payload.get("finish_reason")
        if message is None:
            raise LLMRequestError(
                "chat-completions response has no choices[0].message: "
                f"{json.dumps(payload)[:300]}"
            )
    else:
        raise LLMRequestError(
            f"unexpected chat-completions response type: {type(payload).__name__}"
        )

    content = message.get("content") if isinstance(message, dict) else None
    tool_calls: list[ToolCall] = []
    raw_calls = message.get("tool_calls") if isinstance(message, dict) else None
    if raw_calls:
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function")
            if not isinstance(fn, dict):
                fn = call
            arguments_raw = fn.get("arguments")
            try:
                if isinstance(arguments_raw, str):
                    arguments = json.loads(arguments_raw)
                else:
                    arguments = dict(arguments_raw or {})
            except (json.JSONDecodeError, ValueError, TypeError):
                logger.warning("unparseable tool arguments %r; using empty", arguments_raw)
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=str(call.get("id") or f"call_{len(tool_calls)}"),
                    name=str(fn.get("name") or ""),
                    arguments=arguments,
                )
            )
    return ChatMessage(
        content=str(content) if content is not None else None,
        tool_calls=tool_calls,
        finish_reason=str(finish) if finish is not None else None,
    )


#: transport signature: ``transport(payload) -> raw chat-completions JSON``.
ChatTransport = Callable[[dict[str, Any]], Any]


class LLMClient:
    """Minimal OpenAI-compatible chat-completions client with tool calling.

    Args:
        base_url: API base URL (defaults to ``COFFICE_LLM_BASE_URL`` then
            :data:`DEFAULT_BASE_URL`).
        model: model id (defaults to ``COFFICE_LLM_MODEL`` then
            :data:`DEFAULT_MODEL`).
        api_key: bearer token; ``None``/``""`` sends no auth header (local
            servers need none).
        timeout: per-request timeout in seconds.
        transport: optional callable replacing the HTTP layer (tests inject
            a scripted fake here; the production path is httpx).
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        transport: ChatTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL
        self.api_key = api_key if api_key is not None else os.environ.get(ENV_API_KEY)
        self.timeout = timeout
        self._transport = transport
        if not self.base_url or not self.model:
            raise LLMConfigError(
                "LLM client needs base_url and model "
                f"(set {ENV_BASE_URL} / {ENV_MODEL})"
            )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ChatMessage:
        """Send one chat-completions turn and return the parsed message.

        ``tools`` uses the OpenAI ``[{type: "function", ...}]`` format (see
        :func:`function_tool`). ``stream`` is pinned to ``False`` (Phase 1
        is explicitly non-streaming).
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload.setdefault("tool_choice", "auto")
        payload.update(kwargs)
        if self._transport is not None:
            raw = self._transport(payload)
        else:
            raw = self._post(payload)
        return parse_chat_response(raw)

    # -- HTTP layer ----------------------------------------------------------

    def _post(self, payload: dict[str, Any]) -> Any:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=self.timeout
            )
        except httpx.HTTPError as exc:
            raise LLMRequestError(f"LLM request to {url} failed: {exc}") from exc
        if response.status_code >= 400:
            raise LLMRequestError(
                f"LLM returned HTTP {response.status_code} for {url}: "
                f"{response.text[:500]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise LLMRequestError(
                f"LLM returned non-JSON response from {url}"
            ) from exc


__all__ = [
    "ChatMessage",
    "ChatTransport",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "ENV_API_KEY",
    "ENV_BASE_URL",
    "ENV_MODEL",
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMRequestError",
    "ToolCall",
    "function_tool",
    "parse_chat_response",
]
