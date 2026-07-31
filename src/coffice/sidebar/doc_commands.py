"""Execution of the document commands from the hosting contract.

The UI bead talks to the shell through the message protocol in
:mod:`coffice.sidebar.contract`; the *effect* of each command (open doc,
focus, save) is implemented here. The UNO work is injected through a small
bridge object so this module stays pure Python and is fully unit-testable
without LibreOffice. The extension's native panel buttons and (in a future
LO with a WebView) the JS postMessage handler both funnel into
:func:`execute`.

Bridge interface (duck-typed, see ``_FakeBridge`` in the tests)
----------------------------------------------------------------
``open_path(path: str) -> str | None``
    Open the document at ``path``; return a doc id (or the path).
``prompt_open() -> str | None``
    Show the OS file-open dialog and open the selection; return a doc id.
``focus() -> None``
    Bring the current document window/controller to the foreground.
``save(path: str | None = None) -> str | None``
    Save the current document (to ``path`` when given); return the saved path.
``current_doc_id() -> str | None``
    Identifier of the currently focused document, if any.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from coffice.sidebar.contract import DOC_COMMANDS

_CMD_OPEN_DOC = "openDoc"
_CMD_FOCUS = "focus"
_CMD_SAVE = "save"


class CommandError(Exception):
    """Raised by handlers for user-facing failures (bridge errors)."""


def execute(command: str, args: Mapping[str, Any] | None, bridge: Any) -> dict[str, Any]:
    """Run ``command`` against ``bridge`` and return a contract result payload.

    Always returns ``{"ok": bool, "result": dict | None, "error": str | None}``
    (never raises) so the shell can forward it verbatim as a
    ``coffice.result`` message.
    """
    if command not in DOC_COMMANDS:
        return {"ok": False, "result": None, "error": f"unknown document command: {command!r}"}
    handler = _HANDLERS[command]
    try:
        result = handler(dict(args or {}), bridge)
        return {"ok": True, "result": result, "error": None}
    except CommandError as exc:
        return {"ok": False, "result": None, "error": str(exc)}
    except Exception as exc:  # defensive: bridge/UNO failures become errors
        return {"ok": False, "result": None, "error": f"{type(exc).__name__}: {exc}"}


def _open_doc(args: Mapping[str, Any], bridge: Any) -> dict[str, Any]:
    path = args.get("path")
    if path:
        if not isinstance(path, str) or not path.strip():
            raise CommandError("openDoc: 'path' must be a non-empty string")
        doc_id = bridge.open_path(path.strip())
    else:
        doc_id = bridge.prompt_open()
    return {"path": path or None, "docId": doc_id}


def _focus_doc(args: Mapping[str, Any], bridge: Any) -> dict[str, Any]:
    bridge.focus()
    return {"docId": bridge.current_doc_id()}


def _save_doc(args: Mapping[str, Any], bridge: Any) -> dict[str, Any]:
    path = args.get("path")
    if path is not None and (not isinstance(path, str) or not path.strip()):
        raise CommandError("save: 'path' must be a non-empty string")
    saved = bridge.save(path.strip() if path else None)
    return {"path": saved}


_HANDLERS: dict[str, Any] = {
    _CMD_OPEN_DOC: _open_doc,
    _CMD_FOCUS: _focus_doc,
    _CMD_SAVE: _save_doc,
}
