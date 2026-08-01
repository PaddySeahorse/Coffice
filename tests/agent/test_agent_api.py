"""Tests for the HTTP facade the React sidebar calls (planning doc 9.1).

Covers the acceptance criteria ("HTTP facade responds to /chat and /confirm")
both at the pure handler level and over a real stdlib HTTP server socket.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any

import pytest
from tests.agent.conftest import ScriptedLLM, tool_call

from coffice.agent import ChatMessage
from coffice.agent.agent_api import (
    AgentSessions,
    create_http_server,
    handle_chat,
    handle_confirm,
)


@pytest.fixture()
def harness(agent_server, round_tracker, doc) -> SimpleNamespace:
    """AgentSessions wired with a scripted LLM; tests set ``harness.llm.steps``."""
    llm = ScriptedLLM([ChatMessage(content="Hello!")])
    sessions = AgentSessions(
        llm_client=llm, mcp_server=agent_server, round_tracker=round_tracker
    )
    return SimpleNamespace(sessions=sessions, llm=llm, doc=doc)


def test_handle_chat_requires_message(harness) -> None:
    status, payload = handle_chat(harness.sessions, {})
    assert status == 400
    assert payload["ok"] is False


def test_handle_chat_returns_reply_and_session_id(harness) -> None:
    status, payload = handle_chat(
        harness.sessions, {"message": "hello", "session_id": "abc"}
    )
    assert status == 200
    assert payload["status"] == "complete"
    assert payload["reply"] == "Hello!"
    assert payload["session_id"] == "abc"


def test_handle_confirm_unknown_session_404(harness) -> None:
    status, payload = handle_confirm(
        harness.sessions, {"session_id": "nope", "token": "t"}
    )
    assert status == 404


def test_handle_confirm_unknown_action_400(harness) -> None:
    status, _ = handle_chat(
        harness.sessions, {"message": "hi", "session_id": "abc"}
    )
    assert status == 200
    status, payload = handle_confirm(
        harness.sessions, {"session_id": "abc", "token": "t", "action": "maybe"}
    )
    assert status == 400
    assert "unknown action" in payload["error"]


def test_handle_confirm_rejects_pending_change(harness) -> None:
    harness.doc._text = "A" * 100  # noqa: SLF001
    harness.llm.steps = [
        tool_call("c1", "replaceRange", range={"start": 0, "end": 60}, text="B"),
        ChatMessage(content="Rolled back."),
    ]
    status, payload = handle_chat(
        harness.sessions,
        {"message": "replace", "session_id": "abc", "human_operator": "bob"},
    )
    assert status == 200
    assert payload["status"] == "needs_confirmation"
    token = payload["confirmation"]["token"]
    status, payload = handle_confirm(
        harness.sessions, {"session_id": "abc", "token": token, "action": "reject"}
    )
    assert status == 200
    assert payload["status"] == "complete"
    assert payload["change_summary"][-1]["result"]["status"] == "rejected"


# --------------------------------------------------------------------------
# socket-level test: the real server responds to /chat and /confirm
# --------------------------------------------------------------------------


@pytest.fixture()
def http_server(harness) -> None:
    server = create_http_server(harness.sessions, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)


def _post(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_http_facade_chat_then_confirm(harness, http_server) -> None:
    harness.doc._text = "A" * 100  # noqa: SLF001
    harness.llm.steps = [
        tool_call("c1", "replaceRange", range={"start": 0, "end": 60}, text="B"),
        ChatMessage(content="Done."),
    ]
    status, payload = _post(f"{http_server}/chat", {"message": "replace the intro"})
    assert status == 200
    assert payload["status"] == "needs_confirmation"
    assert payload["needs_confirmation"] is True
    token = payload["confirmation"]["token"]
    session_id = payload["session_id"]

    status, payload = _post(
        f"{http_server}/confirm",
        {"session_id": session_id, "token": token, "action": "confirm"},
    )
    assert status == 200
    assert payload["status"] == "complete"
    assert payload["session_id"] == session_id
    assert harness.doc._text == "B" + "A" * 40  # noqa: SLF001

    # the pending token is consumed
    status, payload = _post(
        f"{http_server}/confirm",
        {"session_id": session_id, "token": token, "action": "confirm"},
    )
    assert status == 200
    assert payload["status"] == "error"


def test_http_facade_health(http_server) -> None:
    with urllib.request.urlopen(f"{http_server}/health", timeout=10) as response:
        assert response.status == 200
        assert json.loads(response.read())["ok"] is True


def test_http_facade_bad_chat_body(http_server) -> None:
    status, payload = _post(f"{http_server}/chat", {})
    assert status == 400
    assert payload["ok"] is False


def test_http_facade_unknown_route(http_server) -> None:
    status, _ = _post(f"{http_server}/nope", {})
    assert status == 404
