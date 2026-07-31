"""Pure-logic unit tests for Document helpers (no LibreOffice required)."""

from __future__ import annotations

import sys
import types

from coffice.core.document import (
    Document,
    _filter_for,
    _iso_timestamp,
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


def test_iso_timestamp_formats_struct_fields() -> None:
    class FakeDateTime:
        Year = 2026
        Month = 7
        Day = 31
        Hours = 12
        Minutes = 5
        Seconds = 9

    assert _iso_timestamp(FakeDateTime()) == "2026-07-31T12:05:09"


def test_iso_timestamp_missing_fields() -> None:
    class Empty:
        pass

    assert _iso_timestamp(Empty()) == "0000-00-00T00:00:00"


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
