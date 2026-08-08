"""UNO component for the Coffice sidebar shell (extension side).

Implements, for the .oxt registered in ``Sidebar.xcu``/``Factories.xcu``/
``ProtocolHandler.xcu``/``Addons.xcu``:

* :class:`CofficePanelFactory` -- a ``com.sun.star.ui.XUIElementFactory``
  (``org.coffice.sidebar.PanelFactory``) that creates the sidebar panel;
* :class:`CofficePanel` -- the native AWT panel itself (no WebView exists on
  the UNO surface, see ``src/coffice/sidebar/__init__.py`` for the hosting
  decision): a launcher/status panel that displays the configured Agent Deck
  URL, can open it in the system browser, and executes the document commands
  of the hosting contract (``openDoc``/``focus``/``save``) natively;
* :class:`CofficeProtocolHandler` -- a ``com.sun.star.frame.ProtocolHandler``
  (``org.coffice.sidebar.ProtocolHandler``) serving ``org.coffice:ToggleSidebar``,
  which opens/closes the Coffice deck (used by the "Coffice" menu item and the
  panel's title-bar menu).

The UNO-free hosting contract and document-command logic live in the
``coffice`` package shipped inside the .oxt (copied from
``src/coffice/sidebar/`` by the build script).
"""

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uno
import unohelper
from com.sun.star.awt import XActionListener, XWindowListener
from com.sun.star.frame import XDispatch, XDispatchProvider
from com.sun.star.lang import XComponent, XInitialization
from com.sun.star.ui import (
    LayoutSize,
    XSidebarPanel,
    XToolPanel,
    XUIElement,
    XUIElementFactory,
)
from com.sun.star.ui.UIElementType import TOOLPANEL as UET_TOOLPANEL

from coffice import contract, doc_commands

IMPL_FACTORY = "org.coffice.sidebar.PanelFactory"
IMPL_PROTOCOL_HANDLER = "org.coffice.sidebar.ProtocolHandler"
RESOURCE_URL = "private:resource/toolpanel/CofficeFactory/CofficePanel"
DECK_ID = "org.coffice.deck"
PROTOCOL_PREFIX = "org.coffice:"

_FILTERS = {
    ".docx": "MS Word 2007 XML",
    ".doc": "MS Word 97",
    ".odt": "writer8",
    ".ott": "writer8_template",
    ".rtf": "Rich Text Format",
    ".txt": "Text",
}


# ---------------------------------------------------------------------------
# small UNO helpers
# ---------------------------------------------------------------------------


def _create(ctx, service):
    return ctx.getServiceManager().createInstanceWithContext(service, ctx)


def _path_to_url(path: str) -> str:
    return Path(path).resolve().as_uri()


def _filter_for(path: str) -> str:
    return _FILTERS.get(Path(path).suffix.lower(), "writer8")


def _make_props(**kwargs):
    from com.sun.star.beans import PropertyValue

    props = []
    for name, value in kwargs.items():
        prop = PropertyValue()
        prop.Name = name
        prop.Value = value
        props.append(prop)
    return tuple(props)


def _dispatch_uno(ctx, frame, command: str) -> None:
    """Dispatch a ``.uno:`` command on ``frame`` (e.g. ``.uno:Open``, ``.uno:Save``)."""
    try:
        helper = _create(ctx, "com.sun.star.frame.DispatchHelper")
        if helper is not None:
            helper.executeDispatch(frame, command, "", 0, ())
    except Exception:
        traceback.print_exc()


def _current_doc(ctx):
    try:
        desktop = _create(ctx, "com.sun.star.frame.Desktop")
        return desktop.getCurrentComponent()
    except Exception:
        return None


def _frame_of(doc):
    try:
        if doc is None:
            return None
        controller = doc.getCurrentController()
        if controller is None:
            return None
        return controller.getFrame()
    except Exception:
        return None


def _launch_browser(ctx, url: str) -> None:
    """Open ``url`` in the system default browser (manual-verification path)."""
    try:
        shell = _create(ctx, "com.sun.star.system.SystemShellExecute")
        shell.execute(url, "", 0)
    except Exception:
        traceback.print_exc()


def _toggle_sidebar(ctx) -> None:
    """Toggle the Coffice deck: open the sidebar on our deck, or close it.

    Uses the public ``com.sun.star.ui.XSidebarProvider`` (available on the
    document controller since LO 5.1); falls back to the built-in
    ``.uno:Sidebar`` toggle when the provider is unavailable.
    """
    doc = _current_doc(ctx)
    frame = _frame_of(doc)
    if doc is not None:
        controller = doc.getCurrentController()
        if controller is not None:
            provider = None
            try:
                provider = controller.queryInterface(
                    uno.getTypeByName("com.sun.star.ui.XSidebarProvider")
                )
            except Exception:
                provider = None
            if provider is not None:
                try:
                    decks = provider.getDecks()
                    deck = None
                    if decks is not None and decks.hasByName(DECK_ID):
                        deck = decks.getByName(DECK_ID)
                    visible = bool(provider.isVisible())
                    deck_active = deck is not None and bool(deck.isActive())
                    if visible and deck_active:
                        provider.setVisible(False)
                    else:
                        provider.setVisible(True)
                        if deck is not None and not deck.isActive():
                            deck.activate(True)
                    return
                except Exception:
                    traceback.print_exc()
    if frame is not None:
        _dispatch_uno(ctx, frame, ".uno:Sidebar")


# ---------------------------------------------------------------------------
# document bridge (executes the hosting-contract document commands via UNO)
# ---------------------------------------------------------------------------


class _UnoDocBridge:
    """Adapts the UNO context to the bridge interface used by
    :func:`coffice.sidebar.doc_commands.execute`."""

    def __init__(self, ctx):
        self.ctx = ctx

    def _desktop(self):
        return _create(self.ctx, "com.sun.star.frame.Desktop")

    def _doc(self):
        try:
            return self._desktop().getCurrentComponent()
        except Exception:
            return None

    def _frame(self):
        return _frame_of(self._doc())

    def open_path(self, path: str):
        try:
            doc = self._desktop().loadComponentFromURL(_path_to_url(path), "_blank", 0, ())
            return self.current_doc_id() if doc is None else self._doc_id(doc)
        except Exception as exc:
            raise doc_commands.CommandError(f"could not open {path!r}: {exc}") from exc

    def prompt_open(self):
        frame = self._frame()
        if frame is not None:
            _dispatch_uno(self.ctx, frame, ".uno:Open")
        return None

    def focus(self):
        try:
            frame = self._frame()
            if frame is not None:
                window = frame.getContainerWindow()
                if window is not None:
                    window.setFocus()
        except Exception as exc:
            raise doc_commands.CommandError(f"could not focus the document: {exc}") from exc

    def save(self, path=None):
        doc = self._doc()
        if doc is None:
            raise doc_commands.CommandError("no document to save")
        try:
            if path:
                doc.storeToURL(_path_to_url(path), _make_props(FilterName=_filter_for(path)))
                return path
            frame = self._frame()
            if frame is not None:
                _dispatch_uno(self.ctx, frame, ".uno:Save")
            return None
        except Exception as exc:
            raise doc_commands.CommandError(f"could not save the document: {exc}") from exc

    def current_doc_id(self):
        return self._doc_id(self._doc())

    @staticmethod
    def _doc_id(doc):
        if doc is None:
            return None
        try:
            location = doc.getLocation()
            return str(location) if location else None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# the panel (native AWT launcher/status UI)
# ---------------------------------------------------------------------------


class CofficePanel(unohelper.Base, XActionListener):
    """The sidebar panel content.

    Shows the configured Agent Deck URL and offers the document commands of the
    hosting contract as buttons. Every button funnels through
    :func:`coffice.sidebar.doc_commands.execute` -- the same code path the JS
    postMessage bridge will use once a WebView host exists.
    """

    def __init__(self, ctx, parent_window):
        self.ctx = ctx
        self.parent = parent_window
        self.url = contract.ui_url()
        self.container = None
        self._controls = {}
        self._resize_listener = None
        try:
            self._build_ui()
        except Exception:
            traceback.print_exc()
            self._build_error_ui(traceback.format_exc())
            return
        self._append_status(
            "Coffice Agent Deck\n"
            f"UI URL: {self.url}\n"
            "MVP host: no UNO WebView exists, so the panel launches the URL in "
            "the browser and runs document commands (open/focus/save) natively."
        )

    # -- UI construction ---------------------------------------------------

    def _build_ui(self):
        self._toolkit = _create(self.ctx, "com.sun.star.awt.Toolkit")
        container = _create(self.ctx, "com.sun.star.awt.UnoControlContainer")
        container.setModel(_create(self.ctx, "com.sun.star.awt.UnoControlContainerModel"))
        container.createPeer(self._toolkit, self.parent)
        self.container = container

        self._add_control(
            "title",
            "com.sun.star.awt.UnoControlFixedText",
            "com.sun.star.awt.UnoControlFixedTextModel",
            {"Label": "Coffice Agent"},
        )
        self._add_control(
            "url",
            "com.sun.star.awt.UnoControlEdit",
            "com.sun.star.awt.UnoControlEditModel",
            {"Text": self.url, "ReadOnly": True},
        )
        self._add_control(
            "status",
            "com.sun.star.awt.UnoControlEdit",
            "com.sun.star.awt.UnoControlEditModel",
            {"MultiLine": True, "ReadOnly": True, "VScroll": True, "AutoVScroll": True},
        )
        self._add_button("open_deck", "Open Agent Deck")
        self._add_button("open_doc", "Open Document")
        self._add_button("focus", "Focus")
        self._add_button("save", "Save")

        size = self.parent.getPosSize()
        container.setPosSize(0, 0, size.Width, size.Height, 15)
        self._resize_listener = _ResizeListener(self)
        self.parent.addWindowListener(self._resize_listener)
        self._relayout()
        container.setVisible(True)

    def _build_error_ui(self, message):
        try:
            self._toolkit = _create(self.ctx, "com.sun.star.awt.Toolkit")
            container = _create(self.ctx, "com.sun.star.awt.UnoControlContainer")
            container.setModel(_create(self.ctx, "com.sun.star.awt.UnoControlContainerModel"))
            container.createPeer(self._toolkit, self.parent)
            self.container = container
            err = self._add_control(
                "err",
                "com.sun.star.awt.UnoControlEdit",
                "com.sun.star.awt.UnoControlEditModel",
                {"MultiLine": True, "ReadOnly": True},
            )
            err.getModel().setPropertyValue(
                "Text", "Coffice panel failed to load:\n\n" + message
            )
            size = self.parent.getPosSize()
            container.setPosSize(0, 0, size.Width, size.Height, 15)
            err.setPosSize(4, 4, max(40, size.Width - 8), max(40, size.Height - 8), 15)
            container.setVisible(True)
        except Exception:
            traceback.print_exc()

    def _add_control(self, name, control_service, model_service, props):
        model = _create(self.ctx, model_service)
        for key, value in props.items():
            model.setPropertyValue(key, value)
        control = _create(self.ctx, control_service)
        control.setModel(model)
        self.container.addControl(name, control)
        control.createPeer(self._toolkit, self.container.getPeer())
        return control

    def _add_button(self, name, label):
        control = self._add_control(
            name,
            "com.sun.star.awt.UnoControlButton",
            "com.sun.star.awt.UnoControlButtonModel",
            {"Label": label},
        )
        control.setActionCommand(name)
        control.addActionListener(self)
        return control

    def _relayout(self):
        if self.container is None or "status" not in self._controls:
            return
        size = self.container.getPosSize()
        w, h = size.Width, size.Height
        if w <= 0 or h <= 0:
            size = self.parent.getPosSize()
            w, h = size.Width, size.Height
        pad = 6
        x = pad
        cw = max(40, w - 2 * pad)
        title_h = 18
        url_h = 22
        button_h = 26
        status_h = max(40, h - (title_h + url_h + 2 * button_h + 5 * pad))
        half = (cw - pad) // 2

        y = pad
        self._controls["title"].setPosSize(x, y, cw, title_h, 15)
        y += title_h + pad
        self._controls["url"].setPosSize(x, y, cw, url_h, 15)
        y += url_h + pad
        self._controls["status"].setPosSize(x, y, cw, status_h, 15)
        y += status_h + pad
        self._controls["open_deck"].setPosSize(x, y, half, button_h, 15)
        self._controls["open_doc"].setPosSize(x + half + pad, y, half, button_h, 15)
        y += button_h + pad
        self._controls["focus"].setPosSize(x, y, half, button_h, 15)
        self._controls["save"].setPosSize(x + half + pad, y, half, button_h, 15)

    # -- actions -----------------------------------------------------------

    def actionPerformed(self, event):
        command = event.ActionCommand
        if command == "open_deck":
            self._open_deck()
        elif command == "open_doc":
            self._run_command("openDoc", {})
        elif command == "focus":
            self._run_command("focus", {})
        elif command == "save":
            self._run_command("save", {})

    def _open_deck(self):
        _launch_browser(self.ctx, self.url)
        self._append_status(f"Opened {self.url} in the system browser.")

    def _run_command(self, command, args):
        result = doc_commands.execute(command, args, _UnoDocBridge(self.ctx))
        line = f"{command} -> ok={result['ok']}"
        if result["error"]:
            line += f" error={result['error']}"
        if result["result"]:
            line += f" result={result['result']}"
        self._append_status(line)

    def _append_status(self, text):
        if "status" not in self._controls:
            return
        existing = self._controls["status"].getText()
        self._controls["status"].setText(f"{existing}\n\n{text}" if existing else text)

    # -- lifecycle ---------------------------------------------------------

    def dispose(self):
        self._controls = {}
        self.container = None

    def get_window(self):
        return self.container


class _ResizeListener(unohelper.Base, XWindowListener):
    def __init__(self, panel):
        self._panel = panel

    def windowResized(self, event):
        try:
            self._panel.container.setPosSize(0, 0, event.Width, event.Height, 15)
            self._panel._relayout()
        except Exception:
            traceback.print_exc()

    def windowMoved(self, event):
        pass

    def windowShown(self, event):
        pass

    def windowHidden(self, event):
        pass

    def disposing(self, event):
        pass


# ---------------------------------------------------------------------------
# UI element + factory
# ---------------------------------------------------------------------------


class CofficePanelUIElement(unohelper.Base, XUIElement, XToolPanel, XSidebarPanel, XComponent):
    def __init__(self, ctx, frame, panel):
        self.ctx = ctx
        self._frame = frame
        self._panel = panel

    # XUIElement
    def getFrame(self):
        return self._frame

    def getResourceURL(self):
        return RESOURCE_URL

    def getType(self):
        return UET_TOOLPANEL

    def getRealInterface(self):
        return self

    # XToolPanel
    def createAccessible(self, parent):
        return self._panel.get_window().getAccessibleContext()

    def getWindow(self):
        return self._panel.get_window()

    # XSidebarPanel
    def getHeightForWidth(self, width):
        return LayoutSize(0, -1, 0)  # flexible height

    def getMinimalWidth(self):
        return 260

    # XComponent
    def dispose(self):
        try:
            if self._panel is not None:
                self._panel.dispose()
                self._panel = None
        except Exception:
            pass

    def addEventListener(self, listener):
        pass

    def removeEventListener(self, listener):
        pass


class CofficePanelFactory(unohelper.Base, XUIElementFactory):
    """Creates the sidebar panel for ``private:resource/toolpanel/CofficeFactory/...``."""

    def __init__(self, ctx):
        self.ctx = ctx

    def createUIElement(self, resource_url, args):
        frame = None
        parent = None
        for arg in args:
            if arg.Name == "Frame":
                frame = arg.Value
            elif arg.Name == "ParentWindow":
                parent = arg.Value
        panel = CofficePanel(self.ctx, parent)
        return CofficePanelUIElement(self.ctx, frame, panel)


# ---------------------------------------------------------------------------
# protocol handler (org.coffice:ToggleSidebar)
# ---------------------------------------------------------------------------


class CofficeProtocolHandler(unohelper.Base, XDispatchProvider, XDispatch, XInitialization):
    def __init__(self, ctx):
        self.ctx = ctx
        self._frame = None

    # XInitialization: the dispatch framework hands us the target frame.
    def initialize(self, args):
        for arg in args:
            if getattr(arg, "Name", None) == "Frame":
                self._frame = arg.Value

    # XDispatchProvider
    def queryDispatch(self, url, target_frame_name, search_flags):
        protocol = getattr(url, "Protocol", "") or ""
        if protocol.startswith(PROTOCOL_PREFIX):
            return self
        return None

    def queryDispatches(self, requests):
        return [
            self.queryDispatch(r.FeatureURL, r.FrameName, r.SearchFlags) for r in requests
        ]

    # XDispatch
    def dispatch(self, url, args):
        path = getattr(url, "Path", "") or ""
        if path == "ToggleSidebar":
            _toggle_sidebar(self.ctx)

    def addStatusListener(self, listener, url):
        pass

    def removeStatusListener(self, listener, url):
        pass


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    CofficePanelFactory,
    IMPL_FACTORY,
    ("com.sun.star.ui.UIElementFactory", IMPL_FACTORY),
)
g_ImplementationHelper.addImplementation(
    CofficeProtocolHandler,
    IMPL_PROTOCOL_HANDLER,
    ("com.sun.star.frame.ProtocolHandler", IMPL_PROTOCOL_HANDLER),
)
