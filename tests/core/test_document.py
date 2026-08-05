"""Integration tests for the Document wrapper (require LibreOffice + PyUNO)."""

from __future__ import annotations

import pytest

from coffice.core.document import Document
from coffice.core.lo_manager import LO_UNAVAILABLE

pytestmark = pytest.mark.skipif(LO_UNAVAILABLE, reason="LibreOffice/PyUNO not available")


def test_insert_get_text_selection(desktop) -> None:
    doc = Document.create(desktop)
    doc.insert_text(0, "Hello world")
    assert doc.get_text() == "Hello world"

    selection = doc.get_selection({"start": 0, "end": 5})
    assert selection["text"] == "Hello"
    assert selection["start"] == 0
    assert selection["end"] == 5
    assert selection["paragraph_style"]
    assert isinstance(selection["character_style"], str)

    whole = doc.get_selection()
    assert whole["text"] == "Hello world"


def test_replace_range(desktop) -> None:
    doc = Document.create(desktop)
    doc.insert_text(0, "Hello world")
    doc.replace_range({"start": 6, "end": 11}, "there")
    assert doc.get_text() == "Hello there"


def test_apply_style_and_outline(desktop) -> None:
    doc = Document.create(desktop)
    doc.insert_text(0, "Intro paragraph\n")
    doc.insert_text("end", "Chapter One\n", style="Heading 1")
    doc.insert_text("end", "Body text\n")
    doc.insert_text("end", "Chapter Two\n", style="Heading 2")
    doc.insert_text("end", "More body\n")

    outline = doc.get_outline()
    assert [heading["text"].rstrip("\n") for heading in outline] == [
        "Chapter One",
        "Chapter Two",
    ]
    assert [heading["level"] for heading in outline] == [1, 2]
    assert [heading["style"] for heading in outline] == ["Heading 1", "Heading 2"]

    first = outline[0]
    doc.apply_style({"start": first["start"], "end": first["end"]}, "Heading 3")
    levels = [heading["level"] for heading in doc.get_outline()]
    assert levels == [3, 2]


def test_get_styles(desktop) -> None:
    doc = Document.create(desktop)
    styles = doc.get_styles()
    assert "Heading 1" in styles["paragraph"]
    assert styles["character"]


def test_generate_and_read_tables(desktop) -> None:
    doc = Document.create(desktop)
    doc.insert_text(0, "Before table\n")
    result = doc.generate_table(2, 2, data=[["a", "b"], ["c", "d"]])
    assert result["rows"] == 2
    assert result["columns"] == 2
    assert result["name"]

    tables = doc.get_tables()
    assert len(tables) == 1
    assert tables[0]["cells"] == [["a", "b"], ["c", "d"]]


def test_set_page_layout(desktop) -> None:
    doc = Document.create(desktop)
    doc.set_page_layout(
        {
            "margins": {"top": 20, "bottom": 20, "left": 25, "right": 25},
            "page_size": {"width": 210, "height": 297},
            "columns": 1,
        }
    )
    assert doc.get_text() == ""


def test_save_reopen_docx(desktop, tmp_path) -> None:
    doc = Document.create(desktop)
    doc.insert_text(0, "Persisted content")
    doc.insert_text("end", "Heading\n", style="Heading 1")
    doc.enable_track_changes()
    doc.insert_text("end", " Tracked")
    assert doc.track_changes_enabled

    path = str(tmp_path / "sample.docx")
    doc.save(path)
    assert doc.track_changes_enabled  # track-changes state kept across save
    doc.close()

    reopened = Document.open(desktop, path)
    assert "Persisted content" in reopened.get_text()
    outline = reopened.get_outline()
    assert outline and outline[0]["text"].rstrip("\n") == "Heading"
    reopened.close()


def test_save_odt(desktop, tmp_path) -> None:
    doc = Document.create(desktop)
    doc.insert_text(0, "ODT content")
    path = str(tmp_path / "sample.odt")
    doc.save(path)
    doc.close()

    reopened = Document.open(desktop, path)
    assert reopened.get_text() == "ODT content"
    reopened.close()
