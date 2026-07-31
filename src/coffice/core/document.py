"""Document wrapper around a loaded LibreOffice Writer document.

Everything here talks to the document exclusively through the UNO API
(ADR-003) -- no direct manipulation of the ODF/OOXML packages. The wrapper is
the shared foundation both the human flow and the agent flow build on
(Symmetric Access, planning doc ch. 3/5), and its method signatures are the
stable contract the MCP server beads consume.

Ranges
------
``range`` arguments are mappings with integer ``start``/``end`` keys giving
character offsets into the full document text (as returned by
:meth:`Document.get_text`). ``None`` means "the whole document". Offsets are
recomputed against the current text on every call, so they are only valid for
the state at the moment they are produced -- callers that edit should re-read
the text afterwards.

Installation note
-----------------
This module requires an interpreter with PyUNO available (``import uno``);
see ``coffice.core.lo_manager`` for the supported LibreOffice install
options (``apt install libreoffice python3-uno`` or the bundled interpreter).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from coffice.core.exceptions import (
    DocumentError,
    DocumentNotFoundError,
)

_FILTERS: dict[str, str] = {
    ".docx": "MS Word 2007 XML",
    ".doc": "MS Word 97",
    ".odt": "writer8",
    ".ott": "writer8_template",
    ".rtf": "Rich Text Format",
    ".txt": "Text",
}

_HEADING_LEVELS = (1, 2, 3, 4)
_MM_TO_1_100TH = 100


def _filter_for(path: str) -> str:
    return _FILTERS.get(Path(path).suffix.lower(), "writer8")


def _path_to_url(path: str) -> str:
    return Path(path).resolve().as_uri()


def _supports_service(obj: Any, service: str) -> bool:
    try:
        return bool(obj.supportsService(service))
    except AttributeError:
        return False


def _make_props(**kwargs: Any) -> tuple[Any, ...]:
    """Build a tuple of ``com.sun.star.beans.PropertyValue`` for UNO calls."""
    from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]

    values = []
    for name, value in kwargs.items():
        prop = PropertyValue()
        prop.Name = name
        prop.Value = value
        values.append(prop)
    return tuple(values)


def _iso_timestamp(dt: Any) -> str:
    year = getattr(dt, "Year", 0) or 0
    month = getattr(dt, "Month", 0) or 0
    day = getattr(dt, "Day", 0) or 0
    hour = getattr(dt, "Hours", 0) or 0
    minute = getattr(dt, "Minutes", 0) or 0
    second = getattr(dt, "Seconds", 0) or 0
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"


class Document:
    """Wrap a loaded ``com.sun.star.text.TextDocument`` with high-level ops.

    Instances are created with :meth:`Document.open`, :meth:`Document.create`,
    or directly from an already-loaded UNO document.
    """

    def __init__(self, doc: Any, path: str | None = None) -> None:
        self._doc = doc
        self._path = path
        self._track_changes_enabled = self._read_track_changes()

    # -- lifecycle ------------------------------------------------------------

    @classmethod
    def open(cls, desktop: Any, path: str, password: str | None = None) -> Document:
        """Open a .docx/.odt document from disk (or an arbitrary URL)."""
        url = _path_to_url(path)
        props: dict[str, Any] = {"Hidden": False}
        if password:
            props["Password"] = password
        doc = desktop.loadComponentFromURL(url, "_blank", 0, _make_props(**props))
        if doc is None:
            raise DocumentNotFoundError(f"LibreOffice could not open {path!r}")
        return cls(doc, path=path)

    @classmethod
    def create(cls, desktop: Any) -> Document:
        """Create a new, empty Writer document in memory."""
        doc = desktop.loadComponentFromURL(
            "private:factory/swriter", "_blank", 0, ()
        )
        return cls(doc)

    def close(self) -> None:
        """Close the underlying document without saving."""
        self._doc.close(False)

    def save(self, path: str | None = None) -> str:
        """Save the document.

        ``path`` defaults to the path it was opened from (the format is
        inferred from the extension, so a .docx stays a .docx). The track
        changes state is preserved across the save.
        """
        target = path or self._path
        if target is None:
            raise DocumentError("no save path: open/create the document with a path")
        self._restore_track_changes()
        self._doc.storeToURL(
            _path_to_url(target), _make_props(FilterName=_filter_for(target))
        )
        self._path = target
        return target

    # -- text and ranges ------------------------------------------------------

    def get_text(self) -> str:
        """Return the full text of the document."""
        return str(self._doc.Text.getString())

    def _resolve_range(self, range_: Mapping[str, int] | None) -> tuple[int, int]:
        length = len(self.get_text())
        if range_ is None:
            return 0, length
        start = int(range_["start"])
        end = int(range_.get("end", start))
        if end < start:
            start, end = end, start
        return max(0, start), min(length, end)

    def _text_cursor_for(self, start: int, end: int) -> Any:
        cursor = self._doc.Text.createTextCursor()
        cursor.gotoStart(False)
        cursor.goRight(start, False)
        cursor.goRight(end - start, True)
        return cursor

    # -- outline / selection / styles / tables --------------------------------

    def get_outline(self) -> list[dict[str, Any]]:
        """List of headings: ``{start, end, level, style, text}`` (Heading 1-4).

        ``start``/``end`` are character offsets into :meth:`get_text` output
        covering the heading's paragraph text, so outline entries can be
        passed directly to the range-based methods (:meth:`get_selection`,
        :meth:`replace_range`, :meth:`apply_style`).
        """
        full = self.get_text()
        enum = self._doc.Text.createEnumeration()
        outline: list[dict[str, Any]] = []
        search_from = 0
        while enum.hasMoreElements():
            element = enum.nextElement()
            if not _supports_service(element, "com.sun.star.text.Paragraph"):
                continue
            paragraph_text = str(element.String)
            start = full.find(paragraph_text, search_from)
            if start < 0:
                start = search_from
            level = self._heading_level(element)
            if level is not None:
                outline.append(
                    {
                        "start": start,
                        "end": start + len(paragraph_text),
                        "level": level,
                        "style": str(element.ParagraphStyleName),
                        "text": paragraph_text,
                    }
                )
            search_from = start + len(paragraph_text)
        return outline

    def _heading_level(self, paragraph: Any) -> int | None:
        style = str(getattr(paragraph, "ParagraphStyleName", "") or "")
        for candidate in (style, style.lower()):
            for prefix in ("Heading", "heading"):
                if candidate.startswith(prefix):
                    suffix = candidate[len(prefix) :].strip()
                    if suffix.isdigit() and int(suffix) in _HEADING_LEVELS:
                        return int(suffix)
        try:
            level = int(paragraph.OutlineLevel)
        except (AttributeError, TypeError, ValueError):
            level = 0
        if level in _HEADING_LEVELS:
            return level
        return None

    def get_selection(self, range: Mapping[str, int] | None = None) -> dict[str, Any]:
        """Return text + style info for ``range`` (defaults to the whole doc)."""
        start, end = self._resolve_range(range)
        cursor = self._text_cursor_for(start, end)
        return {
            "text": str(cursor.String),
            "start": start,
            "end": end,
            "paragraph_style": str(getattr(cursor, "ParaStyleName", "") or ""),
            "character_style": str(getattr(cursor, "CharStyleName", "") or ""),
        }

    def get_styles(self) -> dict[str, list[str]]:
        """Return ``{"paragraph": [...], "character": [...]}`` style names."""
        families = self._doc.getStyleFamilies()
        return {
            "paragraph": list(families.getByName("ParagraphStyles").ElementNames),
            "character": list(families.getByName("CharacterStyles").ElementNames),
        }

    def get_tables(self) -> list[dict[str, Any]]:
        """Return all tables as ``{name, rows, columns, cells}`` (cells[row][col])."""
        tables = self._doc.getTextTables()
        result: list[dict[str, Any]] = []
        for name in tables.ElementNames:
            table = tables.getByName(name)
            rows = int(table.getRows().getCount())
            columns = int(table.getColumns().getCount())
            cells: list[list[str]] = []
            for row in range(rows):
                cell_row: list[str] = []
                for column in range(columns):
                    try:
                        cell_row.append(str(table.getCellByPosition(column, row).getString()))
                    except Exception:
                        cell_row.append("")
                cells.append(cell_row)
            result.append(
                {"name": str(name), "rows": rows, "columns": columns, "cells": cells}
            )
        return result

    # -- editing --------------------------------------------------------------

    def insert_text(
        self, pos: int | str, text: str, style: str | None = None
    ) -> int:
        """Insert ``text`` at character offset ``pos`` (or ``"end"``).

        ``style`` is a paragraph style name applied to the inserted text.
        Returns the character offset just after the inserted text.
        """
        if pos == "end" or pos is None:
            offset = len(self.get_text())
        else:
            offset = int(pos)
        cursor = self._doc.Text.createTextCursor()
        cursor.gotoStart(False)
        cursor.goRight(offset, False)
        if style:
            cursor.setPropertyValue("ParaStyleName", style)
        self._doc.Text.insertString(cursor, text, False)
        return offset + len(text)

    def replace_range(
        self, range: Mapping[str, int], text: str
    ) -> dict[str, int]:
        """Replace the text in ``range`` with ``text``."""
        start, end = self._resolve_range(range)
        cursor = self._text_cursor_for(start, end)
        cursor.setString(text)
        return {"start": start, "end": start + len(text)}

    def apply_style(self, range: Mapping[str, int], style_id: str) -> None:
        """Apply paragraph style ``style_id`` to all paragraphs in ``range``."""
        start, end = self._resolve_range(range)
        cursor = self._text_cursor_for(start, end)
        cursor.setPropertyValue("ParaStyleName", style_id)

    def generate_table(
        self, rows: int, cols: int, data: list[list[Any]] | None = None
    ) -> dict[str, Any]:
        """Append a new table with ``rows`` x ``cols`` (optionally filled from ``data``)."""
        table = self._doc.createInstance("com.sun.star.text.TextTable")
        table.initialize(int(rows), int(cols))
        cursor = self._doc.Text.createTextCursor()
        cursor.gotoEnd(False)
        self._doc.Text.insertTextContent(cursor, table, False)
        if data:
            for row_index, row in enumerate(data):
                for col_index, value in enumerate(row):
                    if row_index < int(rows) and col_index < int(cols):
                        table.getCellByPosition(col_index, row_index).setString(str(value))
        return {"name": str(table.getName()), "rows": int(rows), "columns": int(cols)}

    # -- page layout ----------------------------------------------------------

    def set_page_layout(self, opts: Mapping[str, Any]) -> None:
        """Set page layout options (margins/page size in mm, column count).

        ``opts`` may contain ``margins`` (``top``/``bottom``/``left``/``right``
        in millimetres), ``page_size`` (``width``/``height`` in millimetres)
        and ``columns`` (int).
        """
        page = self._get_page_style()
        margins = opts.get("margins") or {}
        for side in ("top", "bottom", "left", "right"):
            if side in margins:
                page.setPropertyValue(
                    f"{side.title()}Margin", int(margins[side] * _MM_TO_1_100TH)
                )
        size = opts.get("page_size") or {}
        if "width" in size:
            page.setPropertyValue("Width", int(size["width"] * _MM_TO_1_100TH))
        if "height" in size:
            page.setPropertyValue("Height", int(size["height"] * _MM_TO_1_100TH))
        if "width" in size and "height" in size:
            page.setPropertyValue(
                "IsLandscape", size["width"] > size["height"]
            )
        if "columns" in opts:
            page.setPropertyValue("Columns", self._create_text_columns(opts["columns"]))

    def _get_page_style(self) -> Any:
        families = self._doc.getStyleFamilies()
        pages = families.getByName("PageStyles")
        names = list(pages.ElementNames)
        preferred = ("Default Style", "Standard", "Standard Page Style")
        for name in preferred:
            if name in names:
                return pages.getByName(name)
        if names:
            return pages.getByName(names[0])
        raise DocumentError("no page style available in the document")

    def _create_text_columns(self, count: int) -> Any:
        columns = self._doc.createInstance("com.sun.star.text.TextColumns")
        columns.Count = int(count)
        return columns

    # -- track changes (redlines) ----------------------------------------------

    def _read_track_changes(self) -> bool:
        try:
            return bool(self._doc.getPropertyValue("RecordChanges"))
        except Exception:
            return False

    def _restore_track_changes(self) -> None:
        try:
            self._doc.setPropertyValue(
                "RecordChanges", bool(self._track_changes_enabled)
            )
        except Exception:
            pass

    @property
    def track_changes_enabled(self) -> bool:
        """Whether record-changes is currently on for this document."""
        return self._track_changes_enabled

    def enable_track_changes(self) -> None:
        """Turn on LO track changes (redlines) for subsequent edits."""
        self._doc.setPropertyValue("RecordChanges", True)
        self._doc.setPropertyValue("RedlineProtection", False)
        self._track_changes_enabled = True

    def disable_track_changes(self) -> None:
        """Turn off LO track changes."""
        self._doc.setPropertyValue("RecordChanges", False)
        self._track_changes_enabled = False

    def get_redlines(self) -> list[dict[str, Any]]:
        """Return ``[{id, type, author, timestamp, text, comment}]`` redlines.

        ``id`` is the redline's position in the document's redline list and is
        what :meth:`accept_redline` / :meth:`reject_redline` take.
        """
        redlines = self._doc.getRedlines()
        result: list[dict[str, Any]] = []
        for index in range(redlines.getCount()):
            redline = redlines.getByIndex(index)
            result.append(
                {
                    "id": index,
                    "type": str(getattr(redline, "RedlineType", "") or ""),
                    "author": str(getattr(redline, "Author", "") or ""),
                    "timestamp": _iso_timestamp(getattr(redline, "TimeStamp", None)),
                    "text": self._redline_text(redline),
                    "comment": str(getattr(redline, "RedlineComment", "") or ""),
                }
            )
        return result

    @staticmethod
    def _redline_text(redline: Any) -> str:
        try:
            return str(redline.getString())
        except Exception:
            return ""

    def _redline_by_id(self, redline_id: int) -> Any:
        try:
            redlines = self._doc.getRedlines()
            return redlines.getByIndex(int(redline_id))
        except Exception as exc:
            raise DocumentNotFoundError(f"redline {redline_id!r} not found") from exc

    def accept_redline(self, redline_id: int) -> None:
        """Accept (apply) the redline with the given ``id``."""
        self._redline_by_id(redline_id).acceptChanges()

    def reject_redline(self, redline_id: int) -> None:
        """Reject (roll back) the redline with the given ``id``."""
        self._redline_by_id(redline_id).rejectChanges()
