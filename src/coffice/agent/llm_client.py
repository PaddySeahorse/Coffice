"""OpenAI-compatible chat-completions client (planning doc 3.3 / ch. 4).

The agent loop talks to *any* OpenAI-compatible chat-completions endpoint --
Ollama, LM Studio, OpenAI, or an Anthropic-compatible proxy -- through one
thin client. It is deliberately lighter than the ``openai`` SDK: a single
``httpx.post`` to ``{base_url}/chat/completions`` with ``stream: false`` for
the non-streaming path, plus a ``httpx.stream``-based :meth:`LLMClient.chat_stream`
(SSE) for the streaming chat channel. Tool calling is the only extra feature
beyond plain text chat.

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
from collections.abc import Callable, Iterable, Iterator, Mapping
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


@dataclass(frozen=True)
class ChatStreamEvent:
    """One event in a streamed chat-completions turn.

    ``kind`` is ``"content"`` (``text`` holds a token delta), ``"tool_call"``
    (``tool_call`` is a fully accumulated call), or ``"done"`` (``message`` is
    the complete assistant turn the caller should act on).
    """

    kind: str
    text: str = ""
    tool_call: ToolCall | None = None
    message: ChatMessage | None = None


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


def parse_sse_events(lines: Iterable[str]) -> Iterator[ChatStreamEvent]:
    """Parse OpenAI-style streaming SSE lines into a sequence of events.

    ``data: [DONE]`` or a ``finish_reason`` terminates the turn. Streamed
    tool-call deltas are accumulated by ``index`` (the ``arguments`` fragments
    are concatenated until the call is complete) and emitted as one
    ``tool_call`` event each, followed by a single ``done`` event carrying the
    complete :class:`ChatMessage`. ``content`` deltas are emitted as they
    arrive. Malformed lines are skipped tolerantly, matching the loose proxies
    the MVP supports.
    """
    content_parts: list[str] = []
    tool_accum: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data:
            continue
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            continue
        choices = obj.get("choices") if isinstance(obj, dict) else None
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice, dict) else None
        if isinstance(delta, dict):
            text = delta.get("content")
            if isinstance(text, str) and text:
                content_parts.append(text)
                yield ChatStreamEvent(kind="content", text=text)
            raw_calls = delta.get("tool_calls")
            if isinstance(raw_calls, list):
                for raw in raw_calls:
                    if not isinstance(raw, dict):
                        continue
                    index = int(raw.get("index", 0))
                    acc = tool_accum.setdefault(
                        index, {"id": None, "name": None, "arguments": ""}
                    )
                    if raw.get("id"):
                        acc["id"] = str(raw["id"])
                    fn = raw.get("function")
                    if isinstance(fn, dict):
                        if fn.get("name"):
                            acc["name"] = str(fn["name"])
                        if isinstance(fn.get("arguments"), str):
                            acc["arguments"] += fn["arguments"]
        finish = choice.get("finish_reason")
        if finish:
            finish_reason = str(finish)
            break
    tool_calls: list[ToolCall] = []
    for index in sorted(tool_accum):
        acc = tool_accum[index]
        arguments: dict[str, Any] = {}
        try:
            if acc["arguments"]:
                parsed = json.loads(acc["arguments"])
                if isinstance(parsed, dict):
                    arguments = parsed
        except (json.JSONDecodeError, ValueError):
            logger.warning("unparseable streamed tool arguments; using empty")
        tool_calls.append(
            ToolCall(
                id=str(acc["id"] or f"call_{index}"),
                name=str(acc["name"] or ""),
                arguments=arguments,
            )
        )
    for call in tool_calls:
        yield ChatStreamEvent(kind="tool_call", tool_call=call)
    content = "".join(content_parts)
    yield ChatStreamEvent(
        kind="done",
        message=ChatMessage(
            content=content or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        ),
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
        stream_transport: optional callable for :meth:`chat_stream` that
            returns an iterable of SSE lines (tests inject a scripted fake
            here; the production path is ``httpx.stream``).
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        transport: ChatTransport | None = None,
        stream_transport: Callable[[dict[str, Any]], Iterable[str]] | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL
        self.api_key = api_key if api_key is not None else os.environ.get(ENV_API_KEY)
        self.timeout = timeout
        self._transport = transport
        self._stream_transport = stream_transport
        if not self.base_url or not self.model:
            raise LLMConfigError(
                "LLM client needs base_url and model "
                f"(set {ENV_BASE_URL} / {ENV_MODEL})"
            )

    def configure(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Hot-swap the endpoint configuration (driven by the WebUI Settings).

        ``None`` leaves a field untouched. An empty ``base_url``/``model``
        resets to the defaults; an empty ``api_key`` clears the bearer token
        (local servers need none). Existing sessions keep their message
        history, so changing the endpoint mid-conversation just points the
        next LLM call at the new server.
        """
        if base_url is not None:
            stripped = str(base_url).strip().rstrip("/")
            self.base_url = stripped or DEFAULT_BASE_URL
        if model is not None:
            stripped = str(model).strip()
            self.model = stripped or DEFAULT_MODEL
        if api_key is not None:
            self.api_key = str(api_key).strip() or None

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ChatMessage:
        """Send one chat-completions turn and return the parsed message.

        ``tools`` uses the OpenAI ``[{type: "function", ...}]`` format (see
        :func:`function_tool`). ``stream`` is pinned to ``False`` (the
        non-streaming path; the UI uses :meth:`chat_stream` instead).
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

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatStreamEvent]:
        """Stream one chat-completions turn as :class:`ChatStreamEvent` events.

        ``stream`` is pinned to ``True`` (the streaming chat path). Tool-call
        deltas are accumulated until the turn ends, so callers still receive a
        complete :class:`ToolCall` to execute (the loop never acts on a
        half-assembled call).
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload.setdefault("tool_choice", "auto")
        payload.update(kwargs)
        if self._stream_transport is not None:
            yield from parse_sse_events(self._stream_transport(payload))
            return
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.stream(
                "POST", url, json=payload, headers=headers, timeout=self.timeout
            ) as response:
                if response.status_code >= 400:
                    raise LLMRequestError(
                        f"LLM returned HTTP {response.status_code} for {url}: "
                        f"{response.read().decode('utf-8', errors='replace')[:500]}"
                    )
                yield from parse_sse_events(response.iter_lines())
        except httpx.HTTPError as exc:
            raise LLMRequestError(f"LLM request to {url} failed: {exc}") from exc

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
    "ChatStreamEvent",
    "ChatTransport",
    "DEFAULT_BASE_URL",
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
    "parse_sse_events",
]
