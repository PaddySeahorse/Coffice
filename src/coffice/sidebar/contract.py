"""Hosting contract between the LibreOffice sidebar shell and the React Agent Deck UI.

This module is the single source of truth for *how* the React UI bead (``ui/``)
connects to the LibreOffice extension shell (``extension/``). It is pure Python
(no ``uno`` import) so it is unit-testable without LibreOffice and so the build
script can copy it verbatim into the .oxt (``extension/python/coffice/contract.py``).

The shell and the UI talk over two independent channels (planning doc 9.1):

1. **HTTP (agent channel)** -- the React app talks to the agent API
   (``coffice.agent.agent_api``, POST /chat and /confirm) over localhost HTTP.
   This channel is defined by the agent bead; it is not part of this module.

2. **postMessage (document channel)** -- the UI requests document operations
   (open doc, focus, save) from the panel host. In the MVP the panel is a
   native AWT panel (no WebView), so this channel is a *contract*: the same
   command/result message shapes are used by the JS bridge (when a WebView
   host is available in a future LO) and by the native panel buttons, which
   execute the identical commands through :mod:`coffice.sidebar.doc_commands`.

URL the panel loads
-------------------
The Agent Deck is served by ``make run-ui`` (Vite dev server) or a static
bundle. The shell resolves the URL at runtime from ``COFFICE_UI_URL``
(full URL) or ``COFFICE_UI_PORT`` (port on 127.0.0.1), defaulting to
:data:`DEFAULT_UI_URL` (:data:`DEFAULT_UI_PORT`). The UI bead must serve its
app on that origin; the shell never embeds UI logic.
"""

from __future__ import annotations

import os
from typing import Any

PROTOCOL_VERSION = 1

#: Default origin served by ``make run-ui`` (ui/ Vite dev server or dist/).
DEFAULT_UI_PORT = 8787
DEFAULT_UI_URL = f"http://127.0.0.1:{DEFAULT_UI_PORT}/"

ENV_UI_URL = "COFFICE_UI_URL"
ENV_UI_PORT = "COFFICE_UI_PORT"

#: Message types of the document channel (``type`` field of postMessage payloads).
TYPE_COMMAND = "coffice.command"
TYPE_RESULT = "coffice.result"
TYPE_HANDSHAKE = "coffice.handshake"
TYPE_PING = "coffice.ping"
TYPE_PONG = "coffice.pong"

#: Document commands the UI may send to the shell.
DOC_COMMANDS = ("openDoc", "focus", "save")

#: Field names shared by every document-channel message.
TYPE_FIELD = "type"
ID_FIELD = "id"
COMMAND_FIELD = "command"
ARGS_FIELD = "args"
OK_FIELD = "ok"
RESULT_FIELD = "result"
ERROR_FIELD = "error"


def ui_url() -> str:
    """Resolve the Agent Deck URL the panel loads.

    Order: ``COFFICE_UI_URL`` (full URL), then ``COFFICE_UI_PORT``
    (port on 127.0.0.1), then :data:`DEFAULT_UI_URL`.
    """
    explicit = os.environ.get(ENV_UI_URL)
    if explicit:
        return explicit if explicit.endswith("/") else explicit + "/"
    try:
        port = int(os.environ.get(ENV_UI_PORT, str(DEFAULT_UI_PORT)))
    except ValueError:
        port = DEFAULT_UI_PORT
    return f"http://127.0.0.1:{port}/"


# ---------------------------------------------------------------------------
# Message shapes
# ---------------------------------------------------------------------------


def make_command(
    command: str, message_id: str | None = None, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a UI -> shell command message.

    ``message_id`` links the result back to the request; a random id is used
    when omitted. ``args`` carries command-specific parameters (e.g.
    ``{"path": "/tmp/report.docx"}`` for ``openDoc``).
    """
    return {
        TYPE_FIELD: TYPE_COMMAND,
        ID_FIELD: message_id or _random_id(),
        COMMAND_FIELD: command,
        ARGS_FIELD: dict(args or {}),
    }


def make_result(
    message_id: str,
    ok: bool,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a shell -> UI result message answering ``message_id``."""
    return {
        TYPE_FIELD: TYPE_RESULT,
        ID_FIELD: message_id,
        OK_FIELD: bool(ok),
        RESULT_FIELD: result,
        ERROR_FIELD: error,
    }


def make_handshake(url: str | None = None, doc_id: str | None = None) -> dict[str, Any]:
    """Build the shell -> UI handshake sent when the panel attaches to the UI.

    Carries the panel's resolved URL (what it displays / would load) and the
    current document id so the UI can show document state immediately.
    """
    return {
        TYPE_FIELD: TYPE_HANDSHAKE,
        "version": PROTOCOL_VERSION,
        "url": url or ui_url(),
        "docId": doc_id,
    }


def make_pong() -> dict[str, Any]:
    """Build the shell -> UI reply to a ``coffice.ping``."""
    return {TYPE_FIELD: TYPE_PONG, "version": PROTOCOL_VERSION}


def is_command_message(message: Any) -> bool:
    """True when ``message`` is a well-formed UI -> shell command message."""
    if not isinstance(message, dict):
        return False
    return (
        message.get(TYPE_FIELD) == TYPE_COMMAND
        and isinstance(message.get(ID_FIELD), str)
        and message.get(COMMAND_FIELD) in DOC_COMMANDS
        and isinstance(message.get(ARGS_FIELD, {}), dict)
    )


def is_result_message(message: Any) -> bool:
    """True when ``message`` is a well-formed shell -> UI result message."""
    if not isinstance(message, dict):
        return False
    return (
        message.get(TYPE_FIELD) == TYPE_RESULT
        and isinstance(message.get(ID_FIELD), str)
        and isinstance(message.get(OK_FIELD), bool)
    )


def is_handshake_message(message: Any) -> bool:
    """True when ``message`` is a well-formed shell -> UI handshake."""
    if not isinstance(message, dict):
        return False
    return message.get(TYPE_FIELD) == TYPE_HANDSHAKE and isinstance(message.get("version"), int)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _random_id() -> str:
    import uuid

    return uuid.uuid4().hex
