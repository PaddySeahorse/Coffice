"""Minimal HTTP facade for the React Agent Deck sidebar (planning doc 9.1).

The React sidebar talks to the agent over localhost HTTP with exactly two
endpoints (this is the contract the UI bead consumes):

- ``POST /chat``    -- ``{"message": "...", "session_id": "?", "human_operator": "?"}``
                       runs one agent round and returns the final reply, the
                       session change summary, and (when a write tool needs
                       approval) a ``needs_confirmation`` payload carrying the
                       ``token`` and ``snapshot_hash``.
- ``POST /confirm`` -- ``{"session_id": "...", "token": "...", "action": "confirm"|"reject"}``
                       resolves the pending change (confirm executes the edit;
                       reject checks out the pre-op snapshot) and resumes the
                       agent loop until it finishes.

The UI bead extends this facade with read-only introspection and a generic
in-process tool passthrough (same tool names/result shapes the MCP server
exposes, so contracts stay aligned):

- ``GET /tools``    -- registered MCP tools (name/description/inputSchema) for
                       the Tools panel; the ``confirmOp``/``rejectOp`` helpers
                       are hidden (they are human-only, never agent/UI listed).
- ``POST /tool``    -- ``{"name": "...", "arguments": {...}}`` executes any
                       registered tool in-process (read tools, exportDoc,
                       importBundle, rollback, ...) and returns its dict result.
                       Used by the Diff accept/reject, History rollback, Export
                       dialog, and the Tools panel's manual read triggers.
- ``GET /history``  -- ``co log`` timeline via the ``getHistory`` tool
                       (``?docId=&limit=``).
- ``GET /diff``     -- diff between two commit hashes via the ``getDiff`` tool
                       (``?h1=&h2=&docId=``).

The WebUI also configures the LLM endpoint at runtime through the Settings
panel:

- ``GET /settings``        -- current LLM base URL / model / masked API key.
- ``POST /settings``       -- ``{"base_url": "...", "model": "...", "api_key": "..."}``
                              hot-swaps the in-process LLMClient and persists
                              the change to ``~/.coffice/llm.json`` (env vars
                              stay the boot-time defaults).
- ``POST /settings/test``  -- pings a candidate endpoint with a one-token
                              probe call; never persists.

A ``GET /health`` reports liveness. Everything else is stdlib
``http.server`` (a ``ThreadingHTTPServer``) so the MVP keeps dependencies
light; swap for FastAPI in Phase 2 if needed.

Run it standalone::

    python -m coffice.agent.agent_api

Configuration mirrors the MCP server (``COFFICE_DOC_PATH``,
``COFFICE_LO_PORT``, ``COFFICE_AUDIT_LOG``, ...) plus the LLM env vars
(``COFFICE_LLM_BASE_URL`` / ``COFFICE_LLM_MODEL`` / ``COFFICE_LLM_API_KEY``)
and the listener port ``COFFICE_AGENT_PORT`` (default 8790, bound to
``127.0.0.1``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from coffice.agent.agent_loop import (
    HUMAN_ONLY_TOOLS,
    AgentSession,
    ChatResult,
    _server_call,
)
from coffice.agent.llm_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LLMClient,
    LLMError,
)
from coffice.agent.llm_settings import (
    apply_persisted,
    apply_to_client,
    current_values,
    effective_settings,
    save_settings,
)
from coffice.mcp.middleware import RoundTracker
from coffice.mcp.server import create_server

logger = logging.getLogger(__name__)

#: default listener port (the sidebar contract's agent channel).
DEFAULT_AGENT_PORT = 8790
ENV_AGENT_HOST = "COFFICE_AGENT_HOST"
ENV_AGENT_PORT = "COFFICE_AGENT_PORT"

_EMPTY_CHAT_RESULT = ChatResult(status="error", error="empty user message")


class AgentSessions:
    """Conversation registry keyed by ``session_id``.

    One shared FastMCP server and round tracker back every session, so the
    pre-round snapshot hook (registered once by ``create_server``) and the
    governance guardrails apply identically across conversations.
    """

    def __init__(
        self,
        llm_client: Any,
        mcp_server: Any,
        round_tracker: RoundTracker | None = None,
        human_operator: str | None = None,
        max_tool_calls: int = 24,
    ) -> None:
        self._llm = llm_client
        self._server = mcp_server
        self._tracker = round_tracker or RoundTracker()
        self._human_operator = human_operator
        self._max_tool_calls = max_tool_calls
        self._sessions: dict[str, AgentSession] = {}

    def get_or_create(
        self, session_id: str | None = None, human_operator: str | None = None
    ) -> tuple[str, AgentSession]:
        """Return ``(session_id, session)``, creating the session on first use."""
        sid = session_id or uuid.uuid4().hex[:12]
        if sid not in self._sessions:
            self._sessions[sid] = AgentSession(
                llm_client=self._llm,
                server=self._server,
                round_tracker=self._tracker,
                human_operator=(
                    human_operator
                    if human_operator is not None
                    else self._human_operator
                ),
                max_tool_calls=self._max_tool_calls,
                session_id=sid,
            )
            logger.info("created agent session %s", sid)
        return sid, self._sessions[sid]

    def get(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._sessions


def build_sessions(
    mcp_server: Any | None = None,
    llm_client: Any | None = None,
    round_tracker: RoundTracker | None = None,
    human_operator: str | None = None,
) -> AgentSessions:
    """Wire an :class:`AgentSessions` from env (LLM + doc server).

    ``mcp_server`` / ``llm_client`` / ``round_tracker`` are injectable for
    tests; the defaults build a real in-process MCP server (env-wired) and a
    real :class:`LLMClient`.
    """
    tracker = round_tracker or RoundTracker()
    mcp = mcp_server or create_server(round_tracker=tracker)
    llm = llm_client or LLMClient()
    apply_persisted(llm)
    return AgentSessions(
        llm_client=llm,
        mcp_server=mcp,
        round_tracker=tracker,
        human_operator=human_operator,
    )


# ============================================================================
# request handling (pure functions, unit-testable without sockets)
# ============================================================================


def handle_chat(sessions: AgentSessions, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Process a ``POST /chat`` body; returns ``(status_code, payload)``."""
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        return 400, {"ok": False, "error": "'message' (non-empty string) is required"}
    session_id = body.get("session_id")
    human_operator = body.get("human_operator")
    sid, session = sessions.get_or_create(session_id, human_operator=human_operator)
    result = session.chat(message)
    payload = result.to_dict()
    payload["session_id"] = sid
    return 200, payload


def handle_confirm(
    sessions: AgentSessions, body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Process a ``POST /confirm`` body; returns ``(status_code, payload)``."""
    session_id = body.get("session_id")
    if not isinstance(session_id, str):
        return 400, {"ok": False, "error": "'session_id' is required"}
    token = body.get("token")
    if not isinstance(token, str) or not token:
        return 400, {"ok": False, "error": "'token' is required"}
    action = str(body.get("action") or "confirm")
    session = sessions.get(session_id)
    if session is None:
        return 404, {"ok": False, "error": "unknown session_id"}
    if action == "reject":
        result = session.reject(token)
    elif action == "confirm":
        result = session.confirm(token)
    else:
        return 400, {"ok": False, "error": f"unknown action {action!r} (confirm|reject)"}
    payload = result.to_dict()
    payload["session_id"] = session_id
    return 200, payload


# ============================================================================
# streaming handlers: SSE events for POST /chat and POST /confirm
# (stream: true body flag). The agent's natural-language message streams as
# ``token`` events while edits still apply atomically after each complete tool
# call; ``tool`` events report each applied call.
# ============================================================================


def handle_chat_stream(
    sessions: AgentSessions, body: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    """Process a streamed ``POST /chat`` body; yields ``{"event", "data"}``.

    Terminal events are ``done``, ``needs_confirmation``, and ``error``.
    """
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        yield {
            "event": "error",
            "data": {"ok": False, "error": "'message' (non-empty string) is required"},
        }
        return
    sid, session = sessions.get_or_create(
        body.get("session_id"), human_operator=body.get("human_operator")
    )
    for event in session.chat_stream(message):
        yield _sse_event(event, sid)


def handle_confirm_stream(
    sessions: AgentSessions, body: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    """Process a streamed ``POST /confirm`` body; yields ``{"event", "data"}``."""
    session_id = body.get("session_id")
    if not isinstance(session_id, str):
        yield {"event": "error", "data": {"ok": False, "error": "'session_id' is required"}}
        return
    token = body.get("token")
    if not isinstance(token, str) or not token:
        yield {"event": "error", "data": {"ok": False, "error": "'token' is required"}}
        return
    action = str(body.get("action") or "confirm")
    session = sessions.get(session_id)
    if session is None:
        yield {"event": "error", "data": {"ok": False, "error": "unknown session_id"}}
        return
    if action == "reject":
        events = session.reject_stream(token)
    elif action == "confirm":
        events = session.confirm_stream(token)
    else:
        yield {
            "event": "error",
            "data": {"ok": False, "error": f"unknown action {action!r} (confirm|reject)"},
        }
        return
    for event in events:
        yield _sse_event(event, session_id)


def _sse_event(event: dict[str, Any], session_id: str) -> dict[str, Any]:
    """Shape a session-loop event dict into an ``{"event", "data"}`` pair."""
    data = dict(event)
    event_type = data.pop("type", "message")
    data.setdefault("session_id", session_id)
    return {"event": event_type, "data": data}


# ============================================================================
# read introspection + generic tool passthrough (UI bead: Tools/Diff/History/
# Export panels). Same tool names and result shapes as the MCP server.
# ============================================================================


def _registered_tools(sessions: AgentSessions) -> list[dict[str, Any]]:
    """The MCP tools the UI may list/call, as ``{name, description, inputSchema}``.

    ``confirmOp``/``rejectOp`` are human-only (the chat /confirm endpoint
    drives them); they are intentionally not exposed for direct calls.
    """
    tools: list[dict[str, Any]] = []
    for tool in asyncio.run(sessions._server.list_tools()):  # noqa: SLF001
        name = getattr(tool, "name", None)
        if not name or name in HUMAN_ONLY_TOOLS:
            continue
        tools.append(
            {
                "name": str(name),
                "description": str(getattr(tool, "description", "") or ""),
                "inputSchema": dict(getattr(tool, "inputSchema", None) or {}),
            }
        )
    return sorted(tools, key=lambda t: t["name"])


def handle_tools(sessions: AgentSessions) -> tuple[int, dict[str, Any]]:
    """Process ``GET /tools``; returns ``(status_code, payload)``."""
    try:
        return 200, {"ok": True, "tools": _registered_tools(sessions)}
    except Exception as exc:  # noqa: BLE001 - surface the introspection failure
        logger.warning("tools introspection failed: %s", exc)
        return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _call_tool(sessions: AgentSessions, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one registered MCP tool in-process; raises on tool errors."""
    return _server_call(sessions._server, name, arguments)


def handle_tool_call(
    sessions: AgentSessions, body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Process a ``POST /tool`` body; returns ``(status_code, payload)``."""
    name = body.get("name")
    if not isinstance(name, str) or not name:
        return 400, {"ok": False, "error": "'name' (non-empty string) is required"}
    arguments = body.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return 400, {"ok": False, "error": "'arguments' must be a JSON object"}
    try:
        result = _call_tool(sessions, name, arguments)
    except Exception as exc:  # noqa: BLE001 - structured error for any tool failure
        logger.warning("tool call %s failed: %s", name, exc)
        return 500, {"ok": False, "tool": name, "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(result, dict):
        result = {"ok": True, "result": result}
    result.setdefault("tool", name)
    return 200, result


def handle_history(
    sessions: AgentSessions, params: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Process ``GET /history`` (``co log`` timeline) via the getHistory tool."""
    arguments: dict[str, Any] = {}
    doc_id = params.get("docId")
    if doc_id:
        arguments["docId"] = doc_id
    limit = params.get("limit")
    if limit is not None:
        try:
            arguments["limit"] = int(limit)
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "'limit' must be an integer"}
    try:
        result = _call_tool(sessions, "getHistory", arguments)
    except Exception as exc:  # noqa: BLE001 - structured error for any failure
        logger.warning("getHistory failed: %s", exc)
        return 500, {"ok": False, "tool": "getHistory", "error": f"{type(exc).__name__}: {exc}"}
    return 200, result


def handle_diff(
    sessions: AgentSessions, params: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Process ``GET /diff`` (commit-to-commit diff) via the getDiff tool."""
    h1 = params.get("h1")
    h2 = params.get("h2")
    if not h1 or not h2:
        return 400, {"ok": False, "error": "'h1' and 'h2' commit hashes are required"}
    arguments: dict[str, Any] = {"h1": h1, "h2": h2}
    doc_id = params.get("docId")
    if doc_id:
        arguments["docId"] = doc_id
    try:
        result = _call_tool(sessions, "getDiff", arguments)
    except Exception as exc:  # noqa: BLE001 - structured error for any failure
        logger.warning("getDiff failed: %s", exc)
        return 500, {"ok": False, "tool": "getDiff", "error": f"{type(exc).__name__}: {exc}"}
    return 200, result


# ============================================================================
# LLM settings: the WebUI Settings panel configures the endpoint at runtime.
# GET /settings returns the read-safe payload (masked key); POST /settings
# hot-swaps the in-process LLMClient and persists to ~/.coffice/llm.json;
# POST /settings/test pings the candidate endpoint with a tiny probe call.
# ============================================================================


def handle_get_settings(sessions: AgentSessions) -> tuple[int, dict[str, Any]]:
    """Process ``GET /settings``; returns the current LLM endpoint config."""
    try:
        settings = effective_settings(sessions._llm)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - surface introspection failure
        logger.warning("settings introspection failed: %s", exc)
        return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return 200, {"ok": True, "settings": settings}


def handle_set_settings(
    sessions: AgentSessions, body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Process ``POST /settings``; applies + persists an endpoint change.

    Body fields (all optional) mirror the env vars: ``base_url``, ``model``,
    ``api_key``. An empty ``api_key`` clears the stored token; a missing
    ``api_key`` leaves it untouched.
    """
    updates: dict[str, Any] = {}
    if "base_url" in body:
        updates["base_url"] = body.get("base_url")
    if "model" in body:
        updates["model"] = body.get("model")
    if "api_key" in body:
        updates["api_key"] = body.get("api_key")
    if not updates:
        return 400, {"ok": False, "error": "no settings provided"}
    for field, value in updates.items():
        if value is None or not isinstance(value, str):
            return 400, {"ok": False, "error": f"'{field}' must be a string"}
    apply_to_client(sessions._llm, updates)  # noqa: SLF001
    save_settings(current_values(sessions._llm))  # noqa: SLF001
    logger.info(
        "LLM settings updated via WebUI: base_url=%s model=%s",
        sessions._llm.base_url,  # noqa: SLF001
        sessions._llm.model,  # noqa: SLF001
    )
    return 200, {"ok": True, "settings": effective_settings(sessions._llm)}  # noqa: SLF001


def handle_settings_test(
    sessions: AgentSessions, body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Process ``POST /settings/test``; pings the candidate endpoint.

    Uses the proposed ``base_url``/``model``/``api_key`` (falling back to the
    current client config) with a one-token probe chat; never persists.
    """
    current = current_values(sessions._llm)  # noqa: SLF001
    base_url = body.get("base_url") or current["base_url"] or DEFAULT_BASE_URL
    model = body.get("model") or current["model"] or DEFAULT_MODEL
    api_key = body.get("api_key")
    if api_key is None:
        api_key = current["api_key"]
    try:
        probe = LLMClient(
            base_url=str(base_url), model=str(model), api_key=api_key, timeout=20.0
        )
        reply = probe.chat([{"role": "user", "content": "ping"}], max_tokens=8)
        return 200, {"ok": True, "reply": (reply.content or "")[:200]}
    except LLMError as exc:
        return 200, {"ok": False, "error": str(exc)}


# ============================================================================
# http.server facade
# ============================================================================


def build_handler(sessions: AgentSessions) -> type[BaseHTTPRequestHandler]:
    """Create an ``http.server`` handler class bound to ``sessions``."""

    class AgentAPIHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_sse(self, events: Iterator[dict[str, Any]]) -> None:
            """Write a streamed chat round as an SSE event stream."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for event in events:
                event_type = event.get("event", "message")
                data = json.dumps(event.get("data", {}), ensure_ascii=False).encode(
                    "utf-8"
                )
                self.wfile.write(f"event: {event_type}\n".encode())
                self.wfile.write(b"data: " + data + b"\n\n")
                self.wfile.flush()

        def _read_body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}

        def do_POST(self) -> None:  # noqa: N802 - http.server dispatch name
            body = self._read_body()
            path = self.path.split("?", 1)[0].rstrip("/")
            if path == "/chat":
                if body.get("stream") is True:
                    self._send_sse(handle_chat_stream(sessions, body))
                    return
                status, payload = handle_chat(sessions, body)
            elif path == "/confirm":
                if body.get("stream") is True:
                    self._send_sse(handle_confirm_stream(sessions, body))
                    return
                status, payload = handle_confirm(sessions, body)
            elif path == "/tool":
                status, payload = handle_tool_call(sessions, body)
            elif path == "/settings":
                status, payload = handle_set_settings(sessions, body)
            elif path == "/settings/test":
                status, payload = handle_settings_test(sessions, body)
            else:
                status, payload = 404, {"ok": False, "error": "not found"}
            self._send_json(status, payload)

        def do_GET(self) -> None:  # noqa: N802 - http.server dispatch name
            path, _, query = self.path.partition("?")
            path = path.rstrip("/")
            params = {
                key: values[0] if values else ""
                for key, values in urllib.parse.parse_qs(query).items()
            }
            if path in ("/health", "/"):
                self._send_json(200, {"ok": True, "service": "coffice-agent"})
                return
            if path == "/tools":
                status, payload = handle_tools(sessions)
            elif path == "/history":
                status, payload = handle_history(sessions, params)
            elif path == "/diff":
                status, payload = handle_diff(sessions, params)
            elif path == "/settings":
                status, payload = handle_get_settings(sessions)
            else:
                status, payload = 404, {"ok": False, "error": "not found"}
            self._send_json(status, payload)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.debug("agent api: " + format, *args)

    return AgentAPIHandler


def create_http_server(
    sessions: AgentSessions,
    host: str = "127.0.0.1",
    port: int = DEFAULT_AGENT_PORT,
) -> ThreadingHTTPServer:
    """Start a threaded HTTP server exposing /chat, /confirm, and /health."""
    handler = build_handler(sessions)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def _env_port() -> int:
    value = os.environ.get(ENV_AGENT_PORT)
    if not value:
        return DEFAULT_AGENT_PORT
    try:
        return max(1, int(value))
    except ValueError:
        logger.warning("ignoring non-integer %s=%r", ENV_AGENT_PORT, value)
        return DEFAULT_AGENT_PORT


def main() -> None:
    """CLI entrypoint: ``python -m coffice.agent.agent_api``."""
    logging.basicConfig(level=os.environ.get("COFFICE_LOG_LEVEL", "INFO").upper())
    host = os.environ.get(ENV_AGENT_HOST, "127.0.0.1")
    port = _env_port()
    sessions = build_sessions()
    server = create_http_server(sessions, host=host, port=port)
    logger.info("agent API listening on http://%s:%d (health: /health)", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("agent API shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
