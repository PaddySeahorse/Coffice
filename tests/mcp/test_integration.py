"""Integration tests against a real LibreOffice instance (skipped when absent).

The MCP server is built with a registry that opens a real document through
PyUNO (via the LO core bead), and ``getOutline``/``getStyles`` are invoked
through the FastMCP layer against the live document. Skipped cleanly when
LibreOffice + PyUNO are not installed (``LO_UNAVAILABLE``).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from tests.mcp.conftest import FakeCoClient

from coffice.core.document import Document
from coffice.core.lo_manager import LO_UNAVAILABLE
from coffice.mcp import create_server
from coffice.mcp.middleware import RoundTracker
from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry
from coffice.versioning.co_client import CoClient

FAKE_CO = Path(__file__).resolve().parent.parent / "versioning" / "fake_co.py"

pytestmark = pytest.mark.skipif(
    LO_UNAVAILABLE,
    reason="LibreOffice/PyUNO not installed (apt install libreoffice python3-uno)",
)


def call_tool(server: FastMCP, name: str, arguments: dict[str, Any]) -> Any:
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    return result


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

    outline = call_tool(server, "getOutline", {"docId": DEFAULT_DOC_ID})
    assert outline["docId"] == DEFAULT_DOC_ID
    assert isinstance(outline["outline"], list)

    styles = call_tool(server, "getStyles", {"docId": DEFAULT_DOC_ID})
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

    tables = call_tool(server, "getTables", {"docId": DEFAULT_DOC_ID})
    assert isinstance(tables["tables"], list)

    redlines = call_tool(server, "getRedlines", {"docId": DEFAULT_DOC_ID})
    assert isinstance(redlines["redlines"], list)


def _live_write_server(desktop, tmp_path: Path) -> tuple[FastMCP, DocumentRegistry]:
    """Build a server on a saved live document, with the fake co binary."""
    doc_path = tmp_path / "write.odt"
    doc = Document.create(desktop)
    doc.insert_text(
        0,
        "## Report\nBody of the report goes here. "
        "This sentence makes the body longer.\n## Notes\nA note.",
    )
    doc.save(str(doc_path))
    registry = DocumentRegistry(
        doc_path=str(doc_path),
        factory=lambda doc_id, path: Document.open(desktop, path or str(doc_path)),
    )
    co = CoClient(
        bin_path=str(FAKE_CO),
        env={
            "FAKE_CO_STATE": str(tmp_path / "state.json"),
            "FAKE_CO_RECORD": str(tmp_path / "invocations.jsonl"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        },
    )
    server = create_server(
        registry=registry, co_client=co, round_tracker=RoundTracker()
    )
    return server, registry


def test_write_tools_edit_live_document(
    desktop, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    server, _ = _live_write_server(desktop, tmp_path)

    inserted = call_tool(
        server, "insertText", {"docId": DEFAULT_DOC_ID, "pos": "end", "text": "\nAdded."}
    )
    assert inserted["ok"] is True
    assert inserted["saved"] is True
    # ADR diff_projector: write tools never turn on RecordChanges, so there is
    # no per-op LO redline index to report; the field stays None for back-compat.
    assert inserted["redline_id"] is None

    styled = call_tool(
        server,
        "applyStyle",
        {"docId": DEFAULT_DOC_ID, "range": {"start": 0, "end": 8}, "styleId": "Heading 1"},
    )
    assert styled["ok"] is True

    table = call_tool(
        server,
        "generateTable",
        {"docId": DEFAULT_DOC_ID, "rows": 2, "cols": 2, "data": [["a", "b"], ["c", "d"]]},
    )
    assert table["ok"] is True
    assert table["rows"] == 2

    layout = call_tool(
        server,
        "setPageLayout",
        {"docId": DEFAULT_DOC_ID, "opts": {"margins": {"top": 15}}},
    )
    assert layout["ok"] is True

    redlines = call_tool(server, "getRedlines", {"docId": DEFAULT_DOC_ID})
    assert isinstance(redlines["redlines"], list)


def test_medium_risk_confirm_flow_against_live_document(
    desktop, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    server, _ = _live_write_server(desktop, tmp_path)

    big = call_tool(
        server,
        "replaceRange",
        {
            "docId": DEFAULT_DOC_ID,
            "range": {"start": 0, "end": 60},
            "text": "Replaced.",
        },
    )
    assert big["status"] == "needs_confirmation"
    assert big["risk"] == "medium"
    assert big["snapshot_hash"]

    confirmed = call_tool(server, "confirmOp", {"docId": DEFAULT_DOC_ID, "token": big["token"]})
    assert confirmed["status"] == "confirmed"
    assert confirmed["ok"] is True

    selection = call_tool(server, "getSelection", {"docId": DEFAULT_DOC_ID})
    assert selection["selection"]["text"].startswith("Replaced.")
