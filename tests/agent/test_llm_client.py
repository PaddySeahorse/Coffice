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
)
from coffice.agent.llm_client import DEFAULT_BASE_URL, ENV_BASE_URL, ENV_MODEL


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
