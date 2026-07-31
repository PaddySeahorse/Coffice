"""LibreOffice extension shell hosting the React Agent Deck sidebar (planning doc 9.1).

This package defines the *hosting contract* between the LO extension
(``extension/``) and the React Agent Deck UI (``ui/``). It contains no UI
logic -- just the shell contract, per the bead scope:

- :mod:`coffice.sidebar.contract` -- the URL the panel loads and the
  JS<->panel postMessage message protocol (document commands: openDoc,
  focus, save).
- :mod:`coffice.sidebar.doc_commands` -- execution of those document
  commands against an injected UNO bridge (unit-testable without LO).

Hosting decision (investigated for Route 1 / Phase 1)
-----------------------------------------------------
Embedding the React UI inside the LO window requires a WebView control.
Investigation of the UNO surface on current LibreOffice (7.x):

- ``com.sun.star.awt.WebBrowser`` does **not** exist; ``offapi`` exposes no
  WebView/HTML control in ``com.sun.star.awt`` (verified against the LO
  source tree: the AWT package has only the classic ``UnoControl*`` widgets,
  dialogs and docking windows).
- LibreOffice *does* ship an internal VCL WebView (GTK3/WebKitGTK) used by
  the Help window, but it is not exposed through UNO, and exposing it would
  require patching LibreOffice itself -- which ADR-003 forbids ("no LO
  source modifications").

=> **Decision: the MVP uses the accepted Route 1 fallback design -- a
   dockable/detachable native AWT sidebar panel** (registered through the LO
   Sidebar API: ``com.sun.star.ui.XUIElementFactory`` via ``Sidebar.xcu`` +
   ``Factories.xcu``) **that hosts the UI over localhost HTTP**. The panel is
   a native launcher/status panel: it displays the configured Agent Deck URL
   (``http://127.0.0.1:<port>`` served by ``make run-ui`` or a bundled
   ``dist/``), can open that URL in the system browser, and implements the
   document-command protocol natively (open doc / focus / save). If a future
   LibreOffice exposes a UNO WebView, the same URL and the same postMessage
   protocol drop straight into the panel -- the contract is unchanged.

Why not just a browser tab? The sidebar keeps the Agent Deck *inside* the LO
window (planning doc 9.1, "left Agent Deck"), so a full browser window is
only the manual-verification path, not the product.
"""

from __future__ import annotations

from coffice.sidebar.contract import (
    DEFAULT_UI_PORT,
    DEFAULT_UI_URL,
    DOC_COMMANDS,
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

__all__ = [
    "DEFAULT_UI_PORT",
    "DEFAULT_UI_URL",
    "DOC_COMMANDS",
    "PROTOCOL_VERSION",
    "TYPE_COMMAND",
    "TYPE_HANDSHAKE",
    "TYPE_PING",
    "TYPE_PONG",
    "TYPE_RESULT",
    "is_command_message",
    "is_handshake_message",
    "is_result_message",
    "make_command",
    "make_handshake",
    "make_pong",
    "make_result",
    "ui_url",
]
