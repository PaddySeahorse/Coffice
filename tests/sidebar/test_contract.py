"""Unit tests for the hosting contract (coffice.sidebar.contract).

These run without LibreOffice -- the contract is pure Python so the React UI
bead can consume it and the extension can embed it.
"""

from __future__ import annotations

import pytest

from coffice.sidebar.contract import (
    DEFAULT_UI_PORT,
    DEFAULT_UI_URL,
    DOC_COMMANDS,
    ENV_UI_PORT,
    ENV_UI_URL,
    PROTOCOL_VERSION,
    TYPE_COMMAND,
    TYPE_HANDSHAKE,
    TYPE_PING,
    TYPE_PONG,
    TYPE_RESULT,
    is_command_message,
    is_handshake_message,
    is_result_message,
    make_command,
    make_handshake,
    make_pong,
    make_result,
    ui_url,
)

# ---------------------------------------------------------------------------
# URL the panel loads
# ---------------------------------------------------------------------------


def test_default_ui_url():
    assert ui_url() == DEFAULT_UI_URL
    assert DEFAULT_UI_URL == f"http://127.0.0.1:{DEFAULT_UI_PORT}/"


def test_ui_url_env_full_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_UI_URL, "http://localhost:9999")
    assert ui_url() == "http://localhost:9999/"


def test_ui_url_env_full_url_keeps_trailing_slash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_UI_URL, "http://localhost:9999/")
    assert ui_url() == "http://localhost:9999/"


def test_ui_url_env_port(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(ENV_UI_URL, raising=False)
    monkeypatch.setenv(ENV_UI_PORT, "4321")
    assert ui_url() == "http://127.0.0.1:4321/"


def test_ui_url_invalid_port_falls_back(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(ENV_UI_URL, raising=False)
    monkeypatch.setenv(ENV_UI_PORT, "not-a-port")
    assert ui_url() == DEFAULT_UI_URL


# ---------------------------------------------------------------------------
# message protocol
# ---------------------------------------------------------------------------


def test_doc_commands_are_the_contract_set():
    assert DOC_COMMANDS == ("openDoc", "focus", "save")


def test_make_command_roundtrip():
    message = make_command("save", message_id="m1", args={"path": "/tmp/a.docx"})
    assert is_command_message(message)
    assert message["type"] == TYPE_COMMAND
    assert message["id"] == "m1"
    assert message["command"] == "save"
    assert message["args"] == {"path": "/tmp/a.docx"}


def test_command_without_id_gets_one():
    message = make_command("focus")
    assert is_command_message(message)
    assert message["id"]


@pytest.mark.parametrize(
    "message",
    [
        None,
        "coffice.command",
        {},
        {"type": "coffice.command", "id": 1, "command": "openDoc", "args": {}},
        {"type": TYPE_COMMAND, "id": "m1", "command": "deleteEverything", "args": {}},
        {"type": TYPE_COMMAND, "id": "m1", "command": "openDoc", "args": "nope"},
    ],
)
def test_invalid_command_messages_rejected(message):
    assert not is_command_message(message)


def test_make_result_roundtrip():
    result = make_result("m1", True, result={"path": "/tmp/a.docx", "docId": None})
    assert is_result_message(result)
    assert result["type"] == TYPE_RESULT
    assert result["id"] == "m1"
    assert result["ok"] is True
    assert result["result"] == {"path": "/tmp/a.docx", "docId": None}
    assert result["error"] is None


def test_error_result():
    result = make_result("m1", False, error="boom")
    assert is_result_message(result)
    assert result["ok"] is False
    assert result["result"] is None
    assert result["error"] == "boom"


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"type": TYPE_RESULT, "id": "m1", "ok": "yes"},
        {"type": TYPE_RESULT, "id": "m1"},
        {"type": TYPE_RESULT, "ok": True},
    ],
)
def test_invalid_result_messages_rejected(message):
    assert not is_result_message(message)


def test_handshake():
    handshake = make_handshake(url="http://127.0.0.1:8787/", doc_id="file:///tmp/a.docx")
    assert is_handshake_message(handshake)
    assert handshake["type"] == TYPE_HANDSHAKE
    assert handshake["version"] == PROTOCOL_VERSION
    assert handshake["url"] == "http://127.0.0.1:8787/"
    assert handshake["docId"] == "file:///tmp/a.docx"
    assert not is_handshake_message({"type": TYPE_HANDSHAKE})
    assert not is_handshake_message({})


def test_ping_pong():
    pong = make_pong()
    assert pong["type"] == TYPE_PONG
    assert pong["version"] == PROTOCOL_VERSION
    assert TYPE_PING == "coffice.ping"
