"""Tests for the WebUI LLM settings endpoints (agent_api /settings).

Covers the read-safe settings payload (masked API key), applying + persisting
a change to the in-process LLMClient, the connection-probe endpoint, and the
socket-level HTTP contract.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from coffice.agent import (
    ChatMessage,
    LLMClient,
    apply_to_client,
    effective_settings,
    handle_get_settings,
    handle_set_settings,
    handle_settings_test,
    load_settings,
    mask_api_key,
    save_settings,
)
from coffice.agent.agent_api import AgentSessions, create_http_server


def _post(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    import urllib.error

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


def _llm_sessions(agent_server: Any, round_tracker: Any) -> AgentSessions:
    llm = LLMClient(base_url="http://llm.test/v1", model="fake-model", api_key="sk-secret-abcd")
    return AgentSessions(
        llm_client=llm, mcp_server=agent_server, round_tracker=round_tracker
    )


# --------------------------------------------------------------------------
# masking
# --------------------------------------------------------------------------


def test_mask_api_key_none_when_unset() -> None:
    assert mask_api_key(None) is None
    assert mask_api_key("") is None


def test_mask_api_key_short() -> None:
    assert mask_api_key("abc") == "***"


def test_mask_api_key_preview() -> None:
    assert mask_api_key("sk-0123456789wxyz") == "sk-0***wxyz"


def test_effective_settings_masks_key(agent_server: Any, round_tracker: Any) -> None:
    settings = effective_settings(_llm_sessions(agent_server, round_tracker)._llm)
    assert settings["base_url"] == "http://llm.test/v1"
    assert settings["model"] == "fake-model"
    assert settings["api_key"] == "sk-s***abcd"
    assert settings["api_key_set"] is True


# --------------------------------------------------------------------------
# handler level
# --------------------------------------------------------------------------


def test_handle_get_settings(agent_server: Any, round_tracker: Any) -> None:
    status, payload = handle_get_settings(_llm_sessions(agent_server, round_tracker))
    assert status == 200
    settings = payload["settings"]
    assert settings["base_url"] == "http://llm.test/v1"
    assert settings["api_key_set"] is True
    assert "sk-secret-abcd" not in json.dumps(payload)


def test_handle_set_settings_updates_client(
    agent_server: Any, round_tracker: Any
) -> None:
    sessions = _llm_sessions(agent_server, round_tracker)
    status, payload = handle_set_settings(
        sessions, {"base_url": "http://new.test/v1", "model": "qwen2.5:14b"}
    )
    assert status == 200
    assert sessions._llm.base_url == "http://new.test/v1"  # noqa: SLF001
    assert sessions._llm.model == "qwen2.5:14b"  # noqa: SLF001
    assert sessions._llm.api_key == "sk-secret-abcd"  # noqa: SLF001 - unchanged
    assert payload["settings"]["base_url"] == "http://new.test/v1"


def test_handle_set_settings_clears_api_key(
    agent_server: Any, round_tracker: Any
) -> None:
    sessions = _llm_sessions(agent_server, round_tracker)
    status, payload = handle_set_settings(sessions, {"api_key": ""})
    assert status == 200
    assert sessions._llm.api_key is None  # noqa: SLF001
    assert payload["settings"]["api_key_set"] is False


def test_handle_set_settings_requires_something(
    agent_server: Any, round_tracker: Any
) -> None:
    status, payload = handle_set_settings(
        _llm_sessions(agent_server, round_tracker), {}
    )
    assert status == 400
    assert "no settings" in payload["error"]


def test_handle_set_settings_rejects_non_string(
    agent_server: Any, round_tracker: Any
) -> None:
    status, payload = handle_set_settings(
        _llm_sessions(agent_server, round_tracker), {"model": 42}
    )
    assert status == 400
    assert "'model' must be a string" in payload["error"]


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "llm.json"
    monkeypatch.setenv("COFFICE_LLM_CONFIG", str(config_path))
    save_settings(
        {"base_url": "http://llm.test/v1", "model": "fake-model", "api_key": "sk-abc"}
    )
    loaded = load_settings()
    assert loaded == {
        "base_url": "http://llm.test/v1",
        "model": "fake-model",
        "api_key": "sk-abc",
    }
    assert (config_path.stat().st_mode & 0o777) == 0o600


def test_load_missing_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COFFICE_LLM_CONFIG", str(tmp_path / "missing.json"))
    assert load_settings() == {}


def test_apply_to_client_overlay(agent_server: Any, round_tracker: Any) -> None:
    sessions = _llm_sessions(agent_server, round_tracker)
    apply_to_client(
        sessions._llm,  # noqa: SLF001
        {"base_url": "http://applied.test/v1", "model": "applied-model", "api_key": "k2"},
    )
    assert sessions._llm.base_url == "http://applied.test/v1"  # noqa: SLF001
    assert sessions._llm.api_key == "k2"  # noqa: SLF001


def test_apply_to_client_tolerates_non_configure_client() -> None:
    class FakeClient:
        base_url = "http://x/v1"
        model = "m"
        api_key = None

    client = FakeClient()
    apply_to_client(client, {"base_url": "http://y/v1"})
    assert client.base_url == "http://x/v1"  # unchanged, no exception


# --------------------------------------------------------------------------
# connection probe
# --------------------------------------------------------------------------


def test_handle_settings_test_ok(
    agent_server: Any, round_tracker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProbe:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def chat(self, messages: list[dict], **kwargs: Any) -> ChatMessage:
            return ChatMessage(content="pong")

    import coffice.agent.agent_api as module

    monkeypatch.setattr(module, "LLMClient", FakeProbe)
    status, payload = handle_settings_test(
        _llm_sessions(agent_server, round_tracker),
        {"base_url": "http://candidate.test/v1", "model": "candidate"},
    )
    assert status == 200
    assert payload["ok"] is True
    assert payload["reply"] == "pong"


def test_handle_settings_test_failure_reports_error(
    agent_server: Any, round_tracker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coffice.agent import LLMRequestError

    class FailingProbe:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def chat(self, messages: list[dict], **kwargs: Any) -> ChatMessage:
            raise LLMRequestError("boom: HTTP 401")

    import coffice.agent.agent_api as module

    monkeypatch.setattr(module, "LLMClient", FailingProbe)
    status, payload = handle_settings_test(
        _llm_sessions(agent_server, round_tracker), {}
    )
    assert status == 200
    assert payload["ok"] is False
    assert "401" in payload["error"]


# --------------------------------------------------------------------------
# socket level: the real HTTP server exposes /settings
# --------------------------------------------------------------------------


@pytest.fixture()
def settings_http(agent_server: Any, round_tracker: Any) -> str:
    server = create_http_server(
        _llm_sessions(agent_server, round_tracker), host="127.0.0.1", port=0
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    yield url
    server.shutdown()
    thread.join(timeout=5)


def test_http_facade_get_settings(settings_http: str) -> None:
    with urllib.request.urlopen(f"{settings_http}/settings", timeout=10) as response:
        assert response.status == 200
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["settings"]["model"] == "fake-model"
    assert payload["settings"]["api_key_set"] is True
    assert "sk-secret-abcd" not in json.dumps(payload)


def test_http_facade_post_settings(settings_http: str) -> None:
    status, payload = _post(
        settings_http + "/settings",
        {"base_url": "http://socket.test/v1", "model": "socket-model", "api_key": ""},
    )
    assert status == 200
    assert payload["settings"]["base_url"] == "http://socket.test/v1"
    assert payload["settings"]["api_key_set"] is False


def test_http_facade_settings_path_unknown(settings_http: str) -> None:
    status, _ = _post(settings_http + "/settings/nope", {"base_url": "http://x/v1"})
    assert status == 404
