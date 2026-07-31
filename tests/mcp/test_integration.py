"""Integration tests against a real LibreOffice instance (skipped when absent).

The MCP server is built with a registry that opens a real document through
PyUNO (via the LO core bead), and ``getOutline``/``getStyles`` are invoked
through the FastMCP layer against the live document. Skipped cleanly when
LibreOffice + PyUNO are not installed (``LO_UNAVAILABLE``).
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP
from tests.mcp.conftest import FakeCoClient

from coffice.core.document import Document
from coffice.core.lo_manager import LO_UNAVAILABLE
from coffice.mcp import create_server
from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry

pytestmark = pytest.mark.skipif(
    LO_UNAVAILABLE,
    reason="LibreOffice/PyUNO not installed (apt install libreoffice python3-uno)",
)


def test_get_outline_and_styles_against_live_document(
    desktop, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    doc_path = tmp_path / "live.odt"
    doc = Document.create(desktop)
    doc.insert_text(0, "Hello live document")
    doc.save(str(doc_path))

    registry = DocumentRegistry(
        doc_path=str(doc_path),
        factory=lambda doc_id, path: Document.open(desktop, path or str(doc_path)),
    )
    server: FastMCP = create_server(registry=registry, co_client=FakeCoClient())

    outline = asyncio.run(server.call_tool("getOutline", {"docId": DEFAULT_DOC_ID}))
    assert outline["docId"] == DEFAULT_DOC_ID
    assert isinstance(outline["outline"], list)

    styles = asyncio.run(server.call_tool("getStyles", {"docId": DEFAULT_DOC_ID}))
    assert styles["docId"] == DEFAULT_DOC_ID
    assert "paragraph" in styles["styles"]
    assert isinstance(styles["styles"]["paragraph"], list)


def test_get_redlines_and_tables_against_live_document(
    desktop, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    doc_path = tmp_path / "redlines.odt"
    doc = Document.create(desktop)
    doc.insert_text(0, "First paragraph")
    doc.generate_table(2, 2, [["a", "b"], ["c", "d"]])
    doc.save(str(doc_path))

    registry = DocumentRegistry(
        doc_path=str(doc_path),
        factory=lambda doc_id, path: Document.open(desktop, path or str(doc_path)),
    )
    server: FastMCP = create_server(registry=registry, co_client=FakeCoClient())

    tables = asyncio.run(server.call_tool("getTables", {"docId": DEFAULT_DOC_ID}))
    assert isinstance(tables["tables"], list)

    redlines = asyncio.run(server.call_tool("getRedlines", {"docId": DEFAULT_DOC_ID}))
    assert isinstance(redlines["redlines"], list)
