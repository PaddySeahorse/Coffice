"""Unit tests for the document registry (docId -> opened Document)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.mcp.conftest import FakeDocument

from coffice.core.document import Document
from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry


@pytest.fixture()
def fake_doc() -> FakeDocument:
    return FakeDocument()


def test_get_opens_via_factory_on_first_use(fake_doc: FakeDocument) -> None:
    opened: list[str] = []
    registry = DocumentRegistry(factory=lambda doc_id, path: (opened.append(doc_id), fake_doc)[1])
    doc = registry.get("doc-1")
    assert doc is fake_doc
    assert opened == ["doc-1"]


def test_get_caches_after_first_open(fake_doc: FakeDocument) -> None:
    calls: list[str] = []
    factory = lambda doc_id, path: (calls.append(doc_id), fake_doc)[1]  # noqa: E731
    registry = DocumentRegistry(factory=factory)
    registry.get("a")
    registry.get("a")
    assert calls == ["a"]


def test_open_with_explicit_path_registers_path(tmp_path: Path, fake_doc: FakeDocument) -> None:
    registry = DocumentRegistry(factory=lambda doc_id, path: fake_doc)
    target = tmp_path / "report.docx"
    registry.open("doc-1", path=str(target))
    assert registry.path("doc-1") == str(target)


def test_path_falls_back_to_configured_path(fake_doc: FakeDocument) -> None:
    registry = DocumentRegistry(
        doc_path="/cfg/report.docx", factory=lambda doc_id, path: fake_doc
    )
    registry.get()
    assert registry.path(DEFAULT_DOC_ID) == "/cfg/report.docx"


def test_path_none_when_created_in_memory(fake_doc: FakeDocument) -> None:
    registry = DocumentRegistry(factory=lambda doc_id, path: fake_doc)
    registry.get()
    assert registry.path(DEFAULT_DOC_ID) is None


def test_path_reads_document_path_attribute(fake_doc: FakeDocument) -> None:
    """Registry honors a path stored on the Document object itself."""
    opened = Document(None, path="/from-doc.docx")  # type: ignore[arg-type]
    registry = DocumentRegistry(factory=lambda doc_id, path: opened)
    registry.get()
    assert registry.path(DEFAULT_DOC_ID) == "/from-doc.docx"


def test_registered_and_contains(tmp_path: Path, fake_doc: FakeDocument) -> None:
    registry = DocumentRegistry(factory=lambda doc_id, path: fake_doc)
    assert "a" not in registry
    registry.get("a")
    assert "a" in registry
    assert registry.registered() == {"a": "<memory>"}


def test_to_dict_self_describes(tmp_path: Path, fake_doc: FakeDocument) -> None:
    registry = DocumentRegistry(
        doc_path=str(tmp_path / "report.docx"),
        lo_port=2111,
        factory=lambda doc_id, path: fake_doc,
    )
    info = registry.to_dict()
    assert info["default_doc_id"] == DEFAULT_DOC_ID
    assert info["lo_port"] == 2111
    assert info["opened"] == {}
    registry.get()
    assert "default" in registry.to_dict()["opened"]
