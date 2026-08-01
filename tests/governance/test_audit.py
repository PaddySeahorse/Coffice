"""Unit tests for the doc 8.4 append-only JSONL audit log."""

from __future__ import annotations

import json
from pathlib import Path

from coffice.governance import AUDIT_FIELDS
from coffice.governance.audit import AuditLog, round_audit_callback
from coffice.mcp.registry import DocumentRegistry
from coffice.versioning.co_client import CommitInfo, DiffEntry


class FakeAuditCo:
    """Minimal co stand-in: log (parent lookup) + diff (files_changed)."""

    def __init__(self) -> None:
        self._commits = [
            CommitInfo(hash="c002", author="AI-Writer", message="pre: r2",
                       timestamp="2026-08-01T00:00:00+00:00"),
            CommitInfo(hash="c001", author="AI-Writer", message="pre: r1",
                       timestamp="2026-08-01T00:00:00+00:00"),
        ]

    def log(self, path: str, limit: int | None = None):
        return self._commits[:limit]

    def diff(self, path: str, h1: str, h2: str):
        return [DiffEntry(status="M", path="word/document.xml")]


class FakeCtx:
    """WriteContext stand-in: the callback reads author/human_operator live."""

    def __init__(self, author: str = "AI-Writer", human_operator: str | None = None):
        self.author = author
        self.human_operator = human_operator


def test_record_writes_all_doc84_fields(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(
        hash="c002",
        author="AI-Writer",
        human_operator="alice",
        message="pre: r2",
        timestamp="2026-08-01T00:00:00+00:00",
        files_changed=["word/document.xml"],
        branch="main",
        parent="c001",
    )
    entries = log.read()
    assert len(entries) == 1
    entry = entries[0]
    assert set(entry) == set(AUDIT_FIELDS)
    assert entry == {
        "hash": "c002",
        "author": "AI-Writer",
        "human_operator": "alice",
        "message": "pre: r2",
        "timestamp": "2026-08-01T00:00:00+00:00",
        "files_changed": ["word/document.xml"],
        "branch": "main",
        "parent": "c001",
    }


def test_log_is_append_only_and_never_truncates(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record(hash="c001", message="first")
    log.record(hash="c002", message="second")
    assert len(log.read()) == 2
    # a second AuditLog instance over the same path appends, not truncates
    again = AuditLog(path)
    again.record(hash="c003", message="third")
    messages = [e["message"] for e in log.read()]
    assert messages == ["first", "second", "third"]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert all(json.loads(line) for line in lines)


def test_record_defaults_keep_shape(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(hash="abc")
    (entry,) = log.read()
    assert set(entry) == set(AUDIT_FIELDS)
    assert entry["hash"] == "abc"
    assert entry["files_changed"] == []
    assert entry["human_operator"] is None


def test_round_callback_writes_full_record(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    registry = DocumentRegistry(doc_path=str(tmp_path / "report.docx"))
    registry._paths["default"] = str(tmp_path / "report.docx")  # noqa: SLF001
    callback = round_audit_callback(log, FakeAuditCo(), registry, FakeCtx("AI-Writer", "alice"))
    callback(
        {
            "event": "round_snapshot",
            "round_id": "r2",
            "doc_id": "default",
            "hash": "c002",
            "timestamp": "2026-08-01T00:00:00+00:00",
        }
    )
    (entry,) = log.read()
    assert set(entry) == set(AUDIT_FIELDS)
    assert entry["hash"] == "c002"
    assert entry["author"] == "AI-Writer"
    assert entry["human_operator"] == "alice"
    assert entry["message"] == "pre: r2"
    assert entry["files_changed"] == ["word/document.xml"]
    assert entry["parent"] == "c001"
    assert entry["branch"] == "main"


def test_round_callback_skips_op_executed_events(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    registry = DocumentRegistry(doc_path=str(tmp_path / "report.docx"))
    callback = round_audit_callback(log, FakeAuditCo(), registry, FakeCtx())
    callback({"event": "op_executed", "tool": "insertText", "doc_id": "default"})
    assert log.read() == []


def test_round_callback_first_commit_uses_fallback_files(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    registry = DocumentRegistry(doc_path=str(tmp_path / "report.docx"))

    class LoneCo:
        def log(self, path, limit=None):
            return [CommitInfo(hash="c001", author="AI-Writer", message="pre: r1")]

    callback = round_audit_callback(log, LoneCo(), registry, FakeCtx())
    callback({"event": "round_snapshot", "round_id": "r1", "hash": "c001"})
    (entry,) = log.read()
    assert entry["parent"] is None
    assert entry["files_changed"] == ["word/document.xml"]
