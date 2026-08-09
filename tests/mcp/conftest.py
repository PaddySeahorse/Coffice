"""Shared fixtures for the MCP server unit tests (no LibreOffice required).

The registry's document factory is swapped for one that returns
:class:`FakeDocument`, so tool-result shaping can be tested in memory; the
co client is swapped for :class:`FakeCoClient` so getHistory/getDiff (and the
write-tools snapshot/rollback calls) run without the real ``co`` binary.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from coffice.core.lo_manager import LoManager
from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry
from coffice.versioning.co_client import CheckoutResult, CommitInfo, DiffEntry


@pytest.fixture
def free_port() -> int:
    """Return a currently-free TCP port on 127.0.0.1 (for LO instances)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def lo_port() -> int:
    """Session-scoped free TCP port for the shared LibreOffice instance."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def lo_manager(lo_port: int) -> LoManager:
    """A running headless LibreOffice instance, torn down after the session."""
    manager = LoManager(port=lo_port, startup_timeout=60.0)
    manager.ensure_running()
    try:
        yield manager
    finally:
        manager.stop()


@pytest.fixture(scope="session")
def desktop(lo_manager: LoManager):
    """The ``com.sun.star.frame.Desktop`` of the running LO instance."""
    return lo_manager.get_desktop()


class FakeDocument:
    """In-memory stand-in for coffice.core.document.Document."""

    def __init__(
        self,
        outline: list[dict[str, Any]] | None = None,
        styles: dict[str, list[str]] | None = None,
        tables: list[dict[str, Any]] | None = None,
        redlines: list[dict[str, Any]] | None = None,
        text: str = "Report",
    ) -> None:
        self._outline = outline or [
            {"start": 0, "end": 11, "level": 1, "style": "Heading 1", "text": "Report"}
        ]
        self._styles = styles or {
            "paragraph": ["Heading 1", "Text Body"],
            "character": ["Emphasis"],
        }
        self._tables = tables or [
            {"name": "Table1", "rows": 2, "columns": 2, "cells": [["a", "b"], ["c", "d"]]}
        ]
        self._redlines = redlines or [
            {
                "id": "h0_insert_aabbcc",
                "type": "insert",
                "author": "AI-Writer",
                "baseline_range": {"start": 0, "end": 0},
                "current_range": {"start": 0, "end": 3},
                "baseline_text": "",
                "current_text": "new",
            }
        ]
        self._text = text
        self.opened_at = DEFAULT_DOC_ID

    def get_outline(self) -> list[dict[str, Any]]:
        return self._outline

    def get_text(self) -> str:
        return self._text

    def replace_all_text(self, new_text: str) -> int:
        self._text = str(new_text)
        return len(self._text)

    def save(self, path: str | None = None) -> str:
        return path or "/tmp/fake-report.docx"

    def get_selection(self, range: dict[str, int] | None = None) -> dict[str, Any]:
        if range is None:
            return {
                "text": "Report",
                "start": 0,
                "end": 11,
                "paragraph_style": "Heading 1",
                "character_style": "",
            }
        return {
            "text": "Rep",
            "start": range["start"],
            "end": range["end"],
            "paragraph_style": "Heading 1",
            "character_style": "",
        }

    def get_styles(self) -> dict[str, list[str]]:
        return self._styles

    def get_tables(self) -> list[dict[str, Any]]:
        return self._tables

    def get_redlines(self) -> list[dict[str, Any]]:
        return self._redlines


class FakeCoClient:
    """In-memory stand-in for coffice.versioning.co_client.CoClient."""

    def __init__(
        self,
        commits: list[CommitInfo] | None = None,
        diffs: list[DiffEntry] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._commits = commits or [
            CommitInfo(
                hash="abc123",
                author="AI-Writer",
                email="ai@coffice",
                message="pre: round 1",
                timestamp="2026-07-31T10:00:00+00:00",
            ),
            CommitInfo(
                hash="def456",
                author="human:张三",
                email="h@coffice",
                message="Initial",
                timestamp="2026-07-31T09:00:00+00:00",
            ),
        ]
        self._diffs = diffs or [DiffEntry(status="M", path="word/document.xml")]
        self._raise_error = raise_error
        self.calls: list[tuple[str, Any]] = []

    def log(self, path: str, limit: int | None = None) -> list[CommitInfo]:
        self.calls.append(("log", (path, limit)))
        if self._raise_error:
            raise RuntimeError("co binary not available")
        commits = self._commits
        if limit is not None:
            commits = commits[:limit]
        return commits

    def diff(self, path: str, h1: str, h2: str) -> list[DiffEntry]:
        self.calls.append(("diff", (path, h1, h2)))
        if self._raise_error:
            raise RuntimeError("co binary not available")
        return self._diffs

    def commit(
        self,
        path: str,
        message: str,
        author: str | None = None,
        human_operator: str | None = None,
        author_email: str | None = None,
        bundle: str | None = None,
    ) -> str:
        self.calls.append(("commit", (path, message)))
        if self._raise_error:
            raise RuntimeError("co binary not available")
        hash_ = f"c{len(self._commits) + 1:06x}"
        self._commits.append(
            CommitInfo(
                hash=hash_,
                author=author or "AI-Writer",
                message=message,
                timestamp="2026-08-01T00:00:00+00:00",
            )
        )
        return hash_

    def checkout(
        self, path: str, hash: str, bundle: str | None = None
    ) -> CheckoutResult:
        self.calls.append(("checkout", (path, hash)))
        if self._raise_error:
            raise RuntimeError("co binary not available")
        return CheckoutResult(hash=hash)


@pytest.fixture()
def fake_document() -> FakeDocument:
    return FakeDocument()


@pytest.fixture()
def fake_projector() -> FakeProjector:
    return FakeProjector()


@pytest.fixture()
def fake_registry(tmp_path: Path, fake_document: FakeDocument) -> DocumentRegistry:
    """Registry wired to return the shared FakeDocument from its factory."""
    return DocumentRegistry(
        doc_path=str(tmp_path / "report.docx"),
        factory=lambda doc_id, path: fake_document,
    )


@pytest.fixture()
def fake_co_client() -> FakeCoClient:
    return FakeCoClient()


class FakeProjector:
    """In-memory stand-in for coffice.core.diff_projector.DiffProjector.

    Tests inject this via ``create_server(projector=...)`` so the redline
    tools stay deterministic without LibreOffice or a real ``co`` checkout.
    """

    def __init__(
        self,
        redlines: list[dict[str, Any]] | None = None,
        rebuild_result: str | None = None,
        raise_on_rebuild: bool = False,
    ) -> None:
        self._redlines = redlines if redlines is not None else [
            {
                "id": "h0_insert_aabbcc",
                "type": "insert",
                "author": "AI-Writer",
                "baseline_range": {"start": 0, "end": 0},
                "current_range": {"start": 0, "end": 3},
                "baseline_text": "",
                "current_text": "new",
            }
        ]
        self._rebuild_result = rebuild_result
        self._raise_on_rebuild = raise_on_rebuild
        self.calls: list[tuple[str, Any]] = []

    def redlines(
        self,
        path: str,
        current_text: str,
        baseline_hash: str | None = None,
        author: str = "AI-Writer",
    ) -> list[dict[str, Any]]:
        self.calls.append(("redlines", (path, current_text)))
        return [dict(r) for r in self._redlines]

    def rebuild_after_reject(
        self,
        path: str,
        current_text: str,
        rejected_ids: set[str],
        baseline_hash: str | None = None,
    ) -> str:
        self.calls.append(("rebuild_after_reject", (path, current_text, set(rejected_ids))))
        if self._raise_on_rebuild:
            raise RuntimeError("projector rebuild unavailable")
        if self._rebuild_result is not None:
            return self._rebuild_result
        # Default: drop the rejected redline's current_text span from the text.
        result = current_text
        for r in self._redlines:
            if r["id"] in rejected_ids and r["current_text"]:
                result = result.replace(r["current_text"], "", 1)
        return result
