"""Pure-logic unit tests for Document helpers (no LibreOffice required)."""

from __future__ import annotations

import sys
import types

from coffice.core.document import (
    Document,
    _filter_for,
    _make_props,
    _path_to_url,
)
from coffice.core.lo_manager import get_manager


def _install_fake_property_value() -> type:
    """Make ``com.sun.star.beans.PropertyValue`` importable for _make_props."""
    for name in ("com", "com.sun", "com.sun.star", "com.sun.star.beans"):
        sys.modules.setdefault(name, types.ModuleType(name))

    class FakePropertyValue:
        def __init__(self) -> None:
            self.Name: str | None = None
            self.Value: object = None

    sys.modules["com.sun.star.beans"].PropertyValue = FakePropertyValue  # type: ignore[attr-defined]
    return FakePropertyValue


class FakeParagraph:
    ParagraphStyleName = "Heading 3"


class FakeOutlineParagraph:
    ParagraphStyleName = "My Custom Style"
    OutlineLevel = 2


class FakeBodyParagraph:
    ParagraphStyleName = "Text Body"
    OutlineLevel = 0


class FakeText:
    def __init__(self, content: str) -> None:
        self._content = content

    def getString(self) -> str:
        return self._content


class FakeDoc:
    def __init__(self, content: str = "Hello world") -> None:
        self.Text = FakeText(content)

    def getPropertyValue(self, name: str) -> bool:  # pragma: no cover - never reached
        raise NotImplementedError


def test_filter_for_extension() -> None:
    assert _filter_for("a.docx") == "MS Word 2007 XML"
    assert _filter_for("a.odt") == "writer8"
    assert _filter_for("a.unknown") == "writer8"


def test_path_to_url(tmp_path) -> None:
    url = _path_to_url(str(tmp_path / "doc.docx"))
    assert url.startswith("file://")
    assert url.endswith("doc.docx")


def test_make_props_builds_property_values() -> None:
    prop_type = _install_fake_property_value()
    props = _make_props(FilterName="writer8", Hidden=True)
    assert len(props) == 2
    assert isinstance(props[0], prop_type)
    names = {p.Name: p.Value for p in props}
    assert names == {"FilterName": "writer8", "Hidden": True}


def test_heading_level_from_style_name() -> None:
    assert Document(FakeDoc())._heading_level(FakeParagraph()) == 3


def test_heading_level_from_outline_property() -> None:
    assert Document(FakeDoc())._heading_level(FakeOutlineParagraph()) == 2


def test_heading_level_body_returns_none() -> None:
    assert Document(FakeDoc())._heading_level(FakeBodyParagraph()) is None


def test_resolve_range_none_is_whole_document() -> None:
    doc = Document(FakeDoc("Hello world"))
    assert doc._resolve_range(None) == (0, 11)


def test_resolve_range_clamps_to_text_length() -> None:
    doc = Document(FakeDoc("Hello world"))
    assert doc._resolve_range({"start": 3, "end": 99}) == (3, 11)
    assert doc._resolve_range({"start": -5, "end": 4}) == (0, 4)


def test_resolve_range_swaps_inverted_offsets() -> None:
    doc = Document(FakeDoc("Hello world"))
    assert doc._resolve_range({"start": 8, "end": 2}) == (2, 8)


def test_get_manager_is_singleton() -> None:
    assert get_manager() is get_manager()


class FakeEnum:
    def __init__(self, elements: list[object]) -> None:
        self._elements = list(elements)

    def hasMoreElements(self) -> bool:
        return bool(self._elements)

    def nextElement(self) -> object:
        return self._elements.pop(0)


class FakeParagraphElement:
    def __init__(self, text: str, style: str, outline_level: int) -> None:
        self.String = text
        self.ParagraphStyleName = style
        self.OutlineLevel = outline_level

    def supportsService(self, service: str) -> bool:
        return service == "com.sun.star.text.Paragraph"


class FakeTextWithEnum:
    def __init__(self, content: str, paragraphs: list[FakeParagraphElement]) -> None:
        self._content = content
        self._paragraphs = paragraphs

    def getString(self) -> str:
        return self._content

    def createEnumeration(self) -> FakeEnum:
        return FakeEnum(list(self._paragraphs))


class FakeDocWithOutline:
    def __init__(self, content: str, paragraphs: list[FakeParagraphElement]) -> None:
        self.Text = FakeTextWithEnum(content, paragraphs)

    def getPropertyValue(self, name: str) -> bool:  # pragma: no cover - never reached
        raise NotImplementedError


def test_get_outline_reports_char_offsets() -> None:
    content = "Intro paragraph\nChapter One\nBody text\nChapter Two\nMore body\n"
    paragraphs = [
        FakeParagraphElement("Intro paragraph\n", "Text Body", 0),
        FakeParagraphElement("Chapter One\n", "Heading 1", 1),
        FakeParagraphElement("Body text\n", "Text Body", 0),
        FakeParagraphElement("Chapter Two\n", "Heading 2", 2),
        FakeParagraphElement("More body\n", "Text Body", 0),
    ]
    outline = Document(FakeDocWithOutline(content, paragraphs)).get_outline()
    assert [heading["text"] for heading in outline] == [
        "Chapter One\n",
        "Chapter Two\n",
    ]
    assert [heading["level"] for heading in outline] == [1, 2]
    assert [heading["start"] for heading in outline] == [16, 38]
    assert [heading["end"] for heading in outline] == [28, 50]
    assert outline[0]["start"] == content.index("Chapter One\n")
