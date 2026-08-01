"""Tests for the UI-bead read endpoints on the agent HTTP facade.

These are the endpoints the React Agent Deck consumes beyond /chat and
/confirm (planning doc 9.1 + UI bead contract):

- ``GET /tools``  -- MCP tool introspection (Tools panel);
- ``POST /tool``  -- generic in-process tool passthrough (Diff accept/reject,
  History rollback, Export dialog, manual read triggers);
- ``GET /history`` -- ``co log`` timeline (History panel);
- ``GET /diff``   -- commit-to-commit diff (History preview / Diff panel).

Every endpoint returns the *same* tool result shapes as the MCP server, so the
UI stays aligned with the agent/versioning bead contracts.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tests.agent.conftest import ScriptedLLM

from coffice.agent import ChatMessage
from coffice.agent.agent_api import (
    AgentSessions,
    handle_diff,
    handle_history,
    handle_tool_call,
    handle_tools,
)


@pytest.fixture()
def harness(agent_server, round_tracker) -> SimpleNamespace:
    """AgentSessions wired with a scripted LLM and the fake doc/co server."""
    llm = ScriptedLLM([ChatMessage(content="Hello!")])
    sessions = AgentSessions(
        llm_client=llm, mcp_server=agent_server, round_tracker=round_tracker
    )
    return SimpleNamespace(sessions=sessions, llm=llm)


# ---------------------------------------------------------------------------
# GET /tools (introspection)
# ---------------------------------------------------------------------------


def test_handle_tools_lists_registered_tools(harness) -> None:
    status, payload = handle_tools(harness.sessions)
    assert status == 200
    assert payload["ok"] is True
    names = {tool["name"] for tool in payload["tools"]}
    assert "getOutline" in names
    assert "getHistory" in names
    assert "exportDoc" in names
    assert "insertText" in names
    # human-only confirmation helpers are never surfaced
    assert "confirmOp" not in names
    assert "rejectOp" not in names
    for tool in payload["tools"]:
        assert isinstance(tool["description"], str) and tool["description"]
        assert isinstance(tool["inputSchema"], dict)


# ---------------------------------------------------------------------------
# POST /tool (generic passthrough)
# ---------------------------------------------------------------------------


def test_handle_tool_call_read_tool(harness) -> None:
    status, payload = handle_tool_call(
        harness.sessions, {"name": "getOutline", "arguments": {}}
    )
    assert status == 200
    assert payload["docId"] == "default"
    assert "outline" in payload


def test_handle_tool_call_missing_name_400(harness) -> None:
    status, payload = handle_tool_call(harness.sessions, {})
    assert status == 400
    assert payload["ok"] is False


def test_handle_tool_call_bad_arguments_400(harness) -> None:
    status, payload = handle_tool_call(
        harness.sessions, {"name": "getOutline", "arguments": "nope"}
    )
    assert status == 400
    assert payload["ok"] is False


def test_handle_tool_call_unknown_tool_500(harness) -> None:
    status, payload = handle_tool_call(
        harness.sessions, {"name": "definitelyNotATool", "arguments": {}}
    )
    assert status == 500
    assert payload["ok"] is False


# ---------------------------------------------------------------------------
# GET /history (co log timeline)
# ---------------------------------------------------------------------------


def test_handle_history_returns_commits_newest_first(
    harness, co_client, doc
) -> None:
    co_client.commit(doc._path, "first snapshot")  # noqa: SLF001
    co_client.commit(doc._path, "second snapshot")  # noqa: SLF001
    status, payload = handle_history(harness.sessions, {})
    assert status == 200
    commits = payload["commits"]
    assert len(commits) == 2
    assert commits[0]["message"] == "second snapshot"
    assert commits[1]["message"] == "first snapshot"
    for commit in commits:
        assert commit["hash"]
        assert commit["author"] is not None
        assert commit["timestamp"] is not None


def test_handle_history_bad_limit_400(harness) -> None:
    status, payload = handle_history(harness.sessions, {"limit": "abc"})
    assert status == 400
    assert payload["ok"] is False


# ---------------------------------------------------------------------------
# GET /diff
# ---------------------------------------------------------------------------


def test_handle_diff_returns_entries(harness) -> None:
    status, payload = handle_diff(
        harness.sessions, {"h1": "aaaaaaa", "h2": "bbbbbbb"}
    )
    assert status == 200
    assert payload["diff"]
    assert payload["diff"][0]["path"] == "word/document.xml"


def test_handle_diff_requires_both_hashes_400(harness) -> None:
    status, payload = handle_diff(harness.sessions, {"h1": "aaaaaaa"})
    assert status == 400
    assert payload["ok"] is False


# ---------------------------------------------------------------------------
# socket-level: the real server exposes the read endpoints
# ---------------------------------------------------------------------------


def test_http_facade_read_endpoints(harness) -> None:
    import json
    import threading
    import urllib.error
    import urllib.request

    from coffice.agent.agent_api import create_http_server

    server = create_http_server(harness.sessions, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/tools", timeout=10) as response:
            payload = json.loads(response.read())
        names = {tool["name"] for tool in payload["tools"]}
        assert "getOutline" in names

        request = urllib.request.Request(
            f"{base}/tool",
            data=json.dumps({"name": "getOutline", "arguments": {}}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
        assert "outline" in payload

        with urllib.request.urlopen(
            f"{base}/diff?h1=a&h2=b", timeout=10
        ) as response:
            payload = json.loads(response.read())
        assert payload["diff"]

        try:
            urllib.request.urlopen(f"{base}/history?limit=abc", timeout=10)
            raised = False
        except urllib.error.HTTPError as exc:
            raised = exc.code == 400
        assert raised
    finally:
        server.shutdown()
        thread.join(timeout=5)
