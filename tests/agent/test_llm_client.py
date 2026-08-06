"""Unit tests for the OpenAI-compatible LLM client (planning doc 3.3/4)."""

from __future__ import annotations

import json

import httpx
import pytest

from coffice.agent import (
    LLMClient,
    LLMError,
    LLMRequestError,
    ToolCall,
    function_tool,
    parse_chat_response,
    parse_sse_events,
)
from coffice.agent.llm_client import DEFAULT_BASE_URL, ENV_BASE_URL, ENV_MODEL


def _sse_lines(payloads: list[object]) -> list[str]:
    """Build SSE ``data:`` lines from JSON payloads / the ``[DONE]`` marker."""
    lines: list[str] = []
    for payload in payloads:
        if payload == "[DONE]":
            lines.append("data: [DONE]")
        else:
            lines.append("data: " + json.dumps(payload))
    return lines


def _stream_choice(content: str | None = None, tool_calls: list[dict] | None = None) -> dict:
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}


def _finish_choice(reason: str = "stop") -> dict:
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def _openai_response(
    content: str | None = None, tool_calls: list[dict] | None = None
) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }


def test_parse_plain_text_response() -> None:
    msg = parse_chat_response(_openai_response(content="Hello, human."))
    assert msg.content == "Hello, human."
    assert msg.has_tool_calls is False
    assert msg.finish_reason == "stop"


def test_parse_tool_call_response() -> None:
    raw = _openai_response(
        content=None,
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "insertText",
                    "arguments": json.dumps({"pos": "end", "text": "Hi"}),
                },
            }
        ],
    )
    msg = parse_chat_response(raw)
    assert msg.content is None
    assert msg.has_tool_calls is True
    (call,) = msg.tool_calls
    assert isinstance(call, ToolCall)
    assert call.id == "call_1"
    assert call.name == "insertText"
    assert call.arguments == {"pos": "end", "text": "Hi"}


def test_parse_tolerates_missing_choices_message_wrapper() -> None:
    raw = {"message": {"role": "assistant", "content": "fallback"}}
    msg = parse_chat_response(raw)
    assert msg.content == "fallback"


def test_parse_invalid_arguments_uses_empty_dict() -> None:
    raw = _openai_response(
        tool_calls=[
            {
                "id": "c",
                "type": "function",
                "function": {"name": "getOutline", "arguments": "not json{"},
            }
        ]
    )
    msg = parse_chat_response(raw)
    assert msg.tool_calls[0].arguments == {}


def test_parse_no_choices_raises() -> None:
    with pytest.raises(LLMRequestError):
        parse_chat_response({"choices": []})


def test_parse_non_dict_raises() -> None:
    with pytest.raises(LLMRequestError):
        parse_chat_response("nope")


def test_function_tool_shape() -> None:
    tool = function_tool(
        "getOutline", "Return the outline", {"type": "object", "properties": {}}
    )
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "getOutline"
    assert tool["function"]["parameters"]["type"] == "object"


def test_client_env_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BASE_URL, "http://llm:9000/v1/")
    monkeypatch.setenv(ENV_MODEL, "llama3.1")
    client = LLMClient()
    assert client.base_url == "http://llm:9000/v1"
    assert client.model == "llama3.1"


def test_client_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_BASE_URL, raising=False)
    monkeypatch.delenv(ENV_MODEL, raising=False)
    client = LLMClient()
    assert client.base_url == DEFAULT_BASE_URL
    assert client.model


def test_client_transport_receives_payload() -> None:
    seen: dict = {}

    def transport(payload: dict) -> dict:
        seen.update(payload)
        return _openai_response(content="ok")

    client = LLMClient(
        base_url="http://llm.test/v1",
        model="fake-model",
        api_key="secret",
        transport=transport,
    )
    msg = client.chat(
        [{"role": "user", "content": "hi"}],
        tools=[function_tool("getOutline", "d", {"type": "object"})],
    )
    assert msg.content == "ok"
    assert seen["model"] == "fake-model"
    assert seen["stream"] is False
    assert seen["tools"][0]["function"]["name"] == "getOutline"
    assert seen["tool_choice"] == "auto"


def test_client_transport_without_tools_sends_none(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def transport(payload: dict) -> dict:
        seen.update(payload)
        return _openai_response(content="ok")

    client = LLMClient(
        base_url="http://llm.test/v1", model="fake-model", transport=transport
    )
    client.chat([{"role": "user", "content": "hi"}])
    assert "tools" not in seen
    assert "tool_choice" not in seen


def test_client_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_post(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    import coffice.agent.llm_client as module

    monkeypatch.setattr(module.httpx, "post", failing_post)
    client = LLMClient(base_url="http://llm.test/v1", model="fake-model")
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])


def test_client_http_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def ok_post(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(
            200, json=_openai_response(content="Hello"), request=httpx.Request("POST", url)
        )

    import coffice.agent.llm_client as module

    monkeypatch.setattr(module.httpx, "post", ok_post)
    client = LLMClient(base_url="http://llm.test/v1", model="fake-model")
    msg = client.chat([{"role": "user", "content": "hi"}])
    assert msg.content == "Hello"


# --------------------------------------------------------------------------
# streaming (SSE) parsing + chat_stream client
# --------------------------------------------------------------------------


def test_parse_sse_content_stream_concatenates_deltas() -> None:
    lines = _sse_lines(
        [
            _stream_choice(content="Hel"),
            _stream_choice(content="lo, "),
            _stream_choice(content="world"),
            _finish_choice("stop"),
        ]
    )
    events = list(parse_sse_events(lines))
    texts = [ev.text for ev in events if ev.kind == "content"]
    assert texts == ["Hel", "lo, ", "world"]
    done = events[-1]
    assert done.kind == "done"
    assert done.message is not None
    assert done.message.content == "Hello, world"
    assert done.message.finish_reason == "stop"
    assert done.message.has_tool_calls is False


def test_parse_sse_tool_call_deltas_accumulate() -> None:
    lines = _sse_lines(
        [
            _stream_choice(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "insertText", "arguments": ""},
                    }
                ]
            ),
            _stream_choice(
                tool_calls=[{"index": 0, "function": {"arguments": '{"pos": "end",'}}]
            ),
            _stream_choice(
                tool_calls=[{"index": 0, "function": {"arguments": '"text": "Hi"}'}}]
            ),
            _finish_choice("tool_calls"),
        ]
    )
    events = list(parse_sse_events(lines))
    calls = [ev.tool_call for ev in events if ev.kind == "tool_call" and ev.tool_call is not None]
    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].name == "insertText"
    assert calls[0].arguments == {"pos": "end", "text": "Hi"}
    done = events[-1]
    assert done.message is not None
    assert done.message.has_tool_calls is True
    assert done.message.content is None


def test_parse_sse_multiple_tool_calls_kept_in_index_order() -> None:
    lines = _sse_lines(
        [
            _stream_choice(
                tool_calls=[
                    {"index": 1, "id": "b", "function": {"name": "getSelection", "arguments": "{}"}}
                ]
            ),
            _stream_choice(
                tool_calls=[
                    {"index": 0, "id": "a", "function": {"name": "getOutline", "arguments": "{}"}}
                ]
            ),
            _finish_choice("tool_calls"),
        ]
    )
    events = list(parse_sse_events(lines))
    names = [
        ev.tool_call.name
        for ev in events
        if ev.kind == "tool_call" and ev.tool_call is not None
    ]
    assert names == ["getOutline", "getSelection"]


def test_parse_sse_done_marker_terminates() -> None:
    lines = _sse_lines(
        [_stream_choice(content="abc"), "[DONE]"]
    )
    events = list(parse_sse_events(lines))
    assert [ev.kind for ev in events] == ["content", "done"]
    assert events[-1].message.content == "abc"


def test_client_chat_stream_payload_and_events() -> None:
    seen: dict = {}

    def stream_transport(payload: dict) -> list[str]:
        seen.update(payload)
        return _sse_lines(
            [
                _stream_choice(content="Working"),
                _finish_choice("stop"),
            ]
        )

    client = LLMClient(
        base_url="http://llm.test/v1",
        model="fake-model",
        api_key="secret",
        stream_transport=stream_transport,
    )
    events = list(
        client.chat_stream(
            [{"role": "user", "content": "hi"}],
            tools=[function_tool("getOutline", "d", {"type": "object"})],
        )
    )
    assert seen["stream"] is True
    assert seen["tools"][0]["function"]["name"] == "getOutline"
    texts = [ev.text for ev in events if ev.kind == "content"]
    assert texts == ["Working"]
    assert events[-1].kind == "done"


def test_client_chat_stream_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_stream(method: str, url: str, **kwargs: object) -> object:
        raise httpx.ConnectError("refused")

    import coffice.agent.llm_client as module

    monkeypatch.setattr(module.httpx, "stream", failing_stream)
    client = LLMClient(base_url="http://llm.test/v1", model="fake-model")
    with pytest.raises(LLMError):
        list(client.chat_stream([{"role": "user", "content": "hi"}]))
