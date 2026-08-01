"""Shared fixtures for the governance server integration tests.

Mirrors ``tests/mcp/conftest.py`` but with a mutable-text fake document (for
protected-region checks) and a recording fake co client (for the audit-flow
test), so the governance wiring can be exercised without LibreOffice or the
real ``co`` binary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from coffice.governance import AgentRegistry, RateLimiter, default_registry
from coffice.governance.audit import AuditLog
from coffice.mcp.middleware import RoundTracker
from coffice.mcp.registry import DocumentRegistry
from coffice.versioning.co_client import CheckoutResult, CommitInfo, DiffEntry


class FakeGovDocument:
    """In-memory mutable document stand-in."""

    def __init__(self, text: str = "", path: str | None = None) -> None:
        self._text = text
        self._path = path
        self._track_enabled = False
        self.calls: list[tuple[str, Any]] = []

    @property
    def track_changes_enabled(self) -> bool:
        return self._track_enabled

    def get_text(self) -> str:
        return self._text

    def get_outline(self) -> list[dict[str, Any]]:
        return []

    def get_redlines(self) -> list[dict[str, Any]]:
        return []

    def enable_track_changes(self) -> None:
        self._track_enabled = True

    def insert_text(self, pos: int | str, text: str, style: str | None = None) -> int:
        self.calls.append(("insert_text", (pos, text, style)))
        offset = len(self._text) if pos in ("end", None) else int(pos)
        self._text = self._text[:offset] + text + self._text[offset:]
        return offset + len(text)

    def replace_range(self, range: dict[str, int], text: str) -> dict[str, int]:
        self.calls.append(("replace_range", (dict(range), text)))
        start, end = int(range["start"]), int(range.get("end", range["start"]))
        self._text = self._text[:start] + text + self._text[end:]
        return {"start": start, "end": start + len(text)}

    def save(self, path: str | None = None) -> str:
        self.calls.append(("save", (path,)))
        if path:
            self._path = str(path)
        if not self._path:
            raise RuntimeError("no save path")
        Path(self._path).write_text(self._text, encoding="utf-8")
        return self._path


class FakeGovCo:
    """In-memory co stand-in that records commits and serves log/diff."""

    def __init__(self) -> None:
        self._commits: list[CommitInfo] = []
        self.calls: list[tuple[str, Any]] = []

    def commit(self, path, message, author=None, human_operator=None, **kwargs) -> str:
        self.calls.append(("commit", (path, message)))
        hash_ = f"c{len(self._commits) + 1:06x}"
        self._commits.append(
            CommitInfo(hash=hash_, author=author or "AI-Writer", message=message)
        )
        return hash_

    def log(self, path, limit: int | None = None):
        self.calls.append(("log", (path, limit)))
        commits = list(reversed(self._commits))
        return commits[:limit] if limit is not None else commits

    def diff(self, path, h1, h2):
        self.calls.append(("diff", (path, h1, h2)))
        return [DiffEntry(status="M", path="word/document.xml")]

    def checkout(self, path, hash, bundle=None) -> CheckoutResult:
        self.calls.append(("checkout", (path, hash)))
        return CheckoutResult(hash=hash)


@pytest.fixture()
def gov_agents() -> AgentRegistry:
    return default_registry()


@pytest.fixture()
def gov_doc(tmp_path: Path) -> FakeGovDocument:
    path = tmp_path / "report.docx"
    path.write_text("", encoding="utf-8")
    return FakeGovDocument(text="", path=str(path))


@pytest.fixture()
def gov_registry(gov_doc: FakeGovDocument) -> DocumentRegistry:
    return DocumentRegistry(
        doc_path=gov_doc._path,  # noqa: SLF001
        factory=lambda doc_id, path: gov_doc,
    )


@pytest.fixture()
def gov_co() -> FakeGovCo:
    return FakeGovCo()


@pytest.fixture()
def gov_round_tracker() -> RoundTracker:
    return RoundTracker()


@pytest.fixture()
def gov_audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture()
def gov_limiter() -> RateLimiter:
    return RateLimiter(limit=3, window_seconds=60.0)
