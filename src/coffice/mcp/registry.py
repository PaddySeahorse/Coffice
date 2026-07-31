"""Document registry mapping ``docId`` -> opened :class:`Document` (planning doc ch. 4).

The MCP server is the only bridge an LLM has to the document, so it owns the
mapping from the opaque ``docId`` identifiers agents pass around to live
:class:`coffice.core.document.Document` instances. The MVP is single-document:
every tool defaults to the ``"default"`` doc, which is opened on demand either
from the configured path (``COFFICE_DOC_PATH``) or created empty in memory on
the first tool call.

The registry never imports PyUNO at module scope; documents are created lazily
through a factory so unit tests can inject an in-memory fake without
LibreOffice (see ``tests/mcp/``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from coffice.core.document import Document

DEFAULT_DOC_ID = "default"

#: ``factory(doc_id, path) -> Document``; ``path`` is an explicit override that
#: wins over the registry's configured path.
DocumentFactory = Callable[[str, str | None], Document]


class DocumentRegistry:
    """Open, cache, and resolve ``Document`` objects by ``docId``.

    Args:
        doc_path: path of the document to open for the ``"default"`` doc (from
            ``COFFICE_DOC_PATH``). ``None`` means "create an empty in-memory
            document on first use".
        lo_port: UNO socket port for the LibreOffice instance.
        factory: overridable document opener (tests inject fakes here).
    """

    def __init__(
        self,
        doc_path: str | None = None,
        lo_port: int | None = None,
        factory: DocumentFactory | None = None,
    ) -> None:
        self._doc_path = doc_path
        self._lo_port = lo_port
        self._factory = factory or self._default_open
        self._documents: dict[str, Document] = {}
        self._paths: dict[str, str] = {}

    def _default_open(self, doc_id: str, path: str | None) -> Document:
        from coffice.core.lo_manager import get_manager

        manager = get_manager(port=self._lo_port) if self._lo_port else get_manager()
        manager.ensure_running()
        desktop = manager.get_desktop()
        target = path or self._doc_path
        if target:
            return Document.open(desktop, target)
        return Document.create(desktop)

    def open(self, doc_id: str = DEFAULT_DOC_ID, path: str | None = None) -> Document:
        """Open ``doc_id`` (cached), optionally pinning it to ``path``."""
        if doc_id not in self._documents:
            self._documents[doc_id] = self._factory(doc_id, path)
            if path:
                self._paths[doc_id] = path
            elif self._doc_path:
                self._paths.setdefault(doc_id, self._doc_path)
        return self._documents[doc_id]

    def get(self, doc_id: str = DEFAULT_DOC_ID) -> Document:
        """Return the document for ``doc_id``, opening it on first use."""
        return self.open(doc_id)

    def path(self, doc_id: str = DEFAULT_DOC_ID) -> str | None:
        """Return the on-disk path of ``doc_id``, or ``None`` if in-memory.

        ``getHistory``/``getDiff`` (via the co client) operate on the saved
        file, so they need the path; a created-but-unsaved document has none.
        """
        if doc_id in self._paths:
            return self._paths[doc_id]
        doc = self._documents.get(doc_id)
        if doc is not None:
            return getattr(doc, "_path", None) or self._doc_path
        return self._doc_path

    def registered(self) -> dict[str, str]:
        """Return ``{doc_id: path-or-<memory>}`` for the opened documents."""
        return {doc_id: self.path(doc_id) or "<memory>" for doc_id in self._documents}

    def __contains__(self, doc_id: str) -> bool:
        return doc_id in self._documents

    def __getitem__(self, doc_id: str) -> Document:
        return self.get(doc_id)

    def to_dict(self) -> dict[str, Any]:
        """Self-describing registry snapshot for the server info block."""
        return {
            "default_doc_id": DEFAULT_DOC_ID,
            "configured_path": self._doc_path,
            "lo_port": self._lo_port,
            "opened": self.registered(),
        }
