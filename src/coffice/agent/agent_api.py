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

import json
import logging
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from coffice.agent.agent_loop import AgentSession, ChatResult
from coffice.agent.llm_client import LLMClient
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
            if self.path.rstrip("/") == "/chat":
                status, payload = handle_chat(sessions, body)
            elif self.path.rstrip("/") == "/confirm":
                status, payload = handle_confirm(sessions, body)
            else:
                status, payload = 404, {"ok": False, "error": "not found"}
            self._send_json(status, payload)

        def do_GET(self) -> None:  # noqa: N802 - http.server dispatch name
            if self.path.rstrip("/") in ("/health", "/"):
                self._send_json(200, {"ok": True, "service": "coffice-agent"})
            else:
                self._send_json(404, {"ok": False, "error": "not found"})

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
