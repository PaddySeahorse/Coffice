"""Unit tests for the write-side MCP tools and snapshot middleware.

Covers the acceptance criteria:

- all 6 write tools (+ confirmOp/rejectOp) are registered and callable;
- a medium-risk op (replaceRange deleting > 50 chars) returns
  ``needs_confirmation`` and rolls back on reject (verified via the fake co
  binary's recorded commit/checkout invocations);
- round snapshots produce exactly one pre-round commit.

The document is an in-memory :class:`FakeWriteDocument`; the co side uses the
real :class:`CoClient` against ``tests/versioning/fake_co.py`` so subprocess
invocations (commit/checkout) are observable through ``FAKE_CO_RECORD``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from tests.mcp.test_server import call_tool, list_tools

from coffice.mcp import WRITE_TOOL_NAMES, create_server
from coffice.mcp.middleware import RoundTracker
from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry
from coffice.mcp.tools_write import (
    RiskLevel,
    WriteContext,
    _dispatch,  # noqa: PLC2701 - middleware guard
    blocked_op_error,
    classify,
    confirms_required,
    requires_confirmation,
)
from coffice.versioning.co_client import CoClient

FAKE_CO = Path(__file__).resolve().parent.parent / "versioning" / "fake_co.py"


class FakeWriteDocument:
    """In-memory stand-in for Document with a mutable text buffer."""

    def __init__(self, text: str = "", path: str | None = None) -> None:
        self._text = text
        self._path = path
        self._track_enabled = False
        self._redlines: list[dict[str, Any]] = []
        self.calls: list[tuple[str, Any]] = []

    # -- read side ---------------------------------------------------------

    @property
    def track_changes_enabled(self) -> bool:
        return self._track_enabled

    def get_text(self) -> str:
        return self._text

    def get_outline(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        for line in self._text.splitlines(keepends=True):
            if line.startswith("## "):
                heading = line.rstrip("\n")
                out.append(
                    {
                        "start": offset,
                        "end": offset + len(heading),
                        "level": 1,
                        "style": "Heading 1",
                        "text": heading,
                    }
                )
            offset += len(line)
        return out

    def get_redlines(self) -> list[dict[str, Any]]:
        return self._redlines

    # -- write side --------------------------------------------------------

    def enable_track_changes(self) -> None:
        self._track_enabled = True

    def insert_text(self, pos: int | str, text: str, style: str | None = None) -> int:
        self.calls.append(("insert_text", (pos, text, style)))
        offset = len(self._text) if pos in ("end", None) else int(pos)
        self._text = self._text[:offset] + text + self._text[offset:]
        self._add_redline("Insert", text)
        return offset + len(text)

    def replace_range(self, range: dict[str, int], text: str) -> dict[str, int]:
        self.calls.append(("replace_range", (dict(range), text)))
        start, end = int(range["start"]), int(range.get("end", range["start"]))
        self._text = self._text[:start] + text + self._text[end:]
        self._add_redline("Replace", text)
        return {"start": start, "end": start + len(text)}

    def apply_style(self, range: dict[str, int], style_id: str) -> None:
        self.calls.append(("apply_style", (dict(range), style_id)))

    def generate_table(
        self, rows: int, cols: int, data: list[list[Any]] | None = None
    ) -> dict[str, Any]:
        self.calls.append(("generate_table", (rows, cols, data)))
        self._add_redline("InsertTable", "")
        return {"name": "Table1", "rows": rows, "columns": cols}

    def set_page_layout(self, opts: dict[str, Any]) -> None:
        self.calls.append(("set_page_layout", (dict(opts),)))

    def save(self, path: str | None = None) -> str:
        self.calls.append(("save", (path,)))
        if path:
            self._path = str(path)
        if not self._path:
            raise RuntimeError("no save path")
        Path(self._path).write_text(self._text, encoding="utf-8")
        return self._path

    def _add_redline(self, redline_type: str, text: str) -> None:
        self._redlines.append(
            {
                "id": len(self._redlines),
                "type": redline_type,
                "author": "AI-Writer",
                "timestamp": "2026-08-01T00:00:00",
                "text": text,
                "comment": "",
            }
        )


def read_invocations(fake_co_env: dict[str, str]) -> list[list[str]]:
    """Read the fake co binary's recorded argv lines."""
    record = Path(fake_co_env["FAKE_CO_RECORD"])
    if not record.exists():
        return []
    return [
        json.loads(line)
        for line in record.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def commits_of(invocations: list[list[str]]) -> list[list[str]]:
    return [line for line in invocations if line[1] == "commit"]


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def fake_co_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Point the fake co binary at an isolated state + invocation record."""
    env = {
        "FAKE_CO_STATE": str(tmp_path / "state.json"),
        "FAKE_CO_RECORD": str(tmp_path / "invocations.jsonl"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    monkeypatch.setenv("FAKE_CO_STATE", env["FAKE_CO_STATE"])
    return env


@pytest.fixture()
def co_client(fake_co_env: dict[str, str]) -> CoClient:
    return CoClient(bin_path=str(FAKE_CO), env=fake_co_env)


@pytest.fixture()
def write_doc(tmp_path: Path) -> FakeWriteDocument:
    path = tmp_path / "report.docx"
    path.write_text("", encoding="utf-8")
    return FakeWriteDocument(text="", path=str(path))


@pytest.fixture()
def write_registry(write_doc: FakeWriteDocument) -> DocumentRegistry:
    return DocumentRegistry(
        doc_path=write_doc._path,  # noqa: SLF001
        factory=lambda doc_id, path: write_doc,
    )


@pytest.fixture()
def round_tracker() -> RoundTracker:
    return RoundTracker()


@pytest.fixture()
def write_server(
    write_registry: DocumentRegistry,
    co_client: CoClient,
    round_tracker: RoundTracker,
) -> FastMCP:
    return create_server(
        registry=write_registry, co_client=co_client, round_tracker=round_tracker
    )


# --------------------------------------------------------------------------
# registration (acceptance: all 6 write tools registered and callable)
# --------------------------------------------------------------------------


def test_all_write_tools_registered(write_server: FastMCP) -> None:
    tools = {t["name"]: t for t in list_tools(write_server)}
    for name in WRITE_TOOL_NAMES:
        assert name in tools, f"{name} not registered"
    for name in (
        "insertText",
        "replaceRange",
        "applyStyle",
        "generateTable",
        "refactorSection",
        "setPageLayout",
    ):
        tool = tools[name]
        assert tool["description"], f"{name} missing description"
        assert "properties" in tool["inputSchema"]


def test_write_tool_schemas_require_key_fields(write_server: FastMCP) -> None:
    tools = {t["name"]: t for t in list_tools(write_server)}
    assert "text" in tools["insertText"]["inputSchema"]["properties"]
    assert "range" in tools["replaceRange"]["inputSchema"]["properties"]
    assert "text" in tools["replaceRange"]["inputSchema"]["properties"]
    assert "styleId" in tools["applyStyle"]["inputSchema"]["properties"]
    assert "token" in tools["confirmOp"]["inputSchema"]["required"]
    assert "token" in tools["rejectOp"]["inputSchema"]["required"]


# --------------------------------------------------------------------------
# risk classification (doc 8.3)
# --------------------------------------------------------------------------


def test_classify_low_risk_ops() -> None:
    assert classify("insertText") is RiskLevel.LOW
    assert classify("applyStyle") is RiskLevel.LOW
    assert classify("setPageLayout") is RiskLevel.LOW
    assert classify("generateTable") is RiskLevel.LOW
    assert classify("totallyUnknownOp") is RiskLevel.LOW


def test_classify_replace_range_thresholds() -> None:
    text = "x" * 100
    assert classify("replaceRange", {"range": {"start": 0, "end": 10}}, text) is RiskLevel.LOW
    assert classify("replaceRange", {"range": {"start": 0, "end": 50}}, text) is RiskLevel.LOW
    assert classify("replaceRange", {"range": {"start": 0, "end": 51}}, text) is RiskLevel.MEDIUM
    assert classify("replaceRange", {"range": {"start": 0, "end": 0}}, text) is RiskLevel.LOW
    assert (
        classify("replaceRange", {"range": {"start": 0, "end": 100}}, text)
        is RiskLevel.HIGH
    )


def test_classify_high_and_forbidden() -> None:
    assert classify("refactorSection") is RiskLevel.HIGH
    assert classify("clearDocument") is RiskLevel.HIGH
    assert classify("runMacro") is RiskLevel.FORBIDDEN
    assert classify("signDocument") is RiskLevel.FORBIDDEN
    assert classify("encryptDocument") is RiskLevel.FORBIDDEN


def test_confirmation_thresholds() -> None:
    assert confirms_required(RiskLevel.LOW) == 0
    assert requires_confirmation(RiskLevel.LOW) is False
    assert confirms_required(RiskLevel.MEDIUM) == 1
    assert requires_confirmation(RiskLevel.MEDIUM) is True
    assert confirms_required(RiskLevel.HIGH) == 2
    assert requires_confirmation(RiskLevel.HIGH) is True
    assert confirms_required(RiskLevel.FORBIDDEN) == 0


# --------------------------------------------------------------------------
# tool behavior
# --------------------------------------------------------------------------


def test_insert_text_runs_immediately_without_track_changes(
    write_server: FastMCP, write_doc: FakeWriteDocument
) -> None:
    write_doc._text = "Hello world"  # noqa: SLF001
    result = call_tool(write_server, "insertText", {"pos": 5, "text": " there"})
    assert result["ok"] is True
    assert result["tool"] == "insertText"
    assert "redline_id" in result
    assert result["redline_id"] is None  # Track Changes is display only
    assert result["saved"] is True
    assert write_doc._text == "Hello there world"  # noqa: SLF001
    # ADR diff_projector: write tools must NOT turn on RecordChanges -- redlines
    # come from co history via diff_projector, not from LO Track Changes.
    assert write_doc._track_enabled is False  # noqa: SLF001


def test_small_replace_range_runs_immediately(
    write_server: FastMCP, write_doc: FakeWriteDocument
) -> None:
    write_doc._text = "x" * 20  # noqa: SLF001
    result = call_tool(
        write_server, "replaceRange", {"range": {"start": 0, "end": 5}, "text": "y"}
    )
    assert result["ok"] is True
    assert write_doc._text == "y" + "x" * 15  # noqa: SLF001


def test_medium_risk_returns_needs_confirmation(
    write_server: FastMCP, write_doc: FakeWriteDocument, fake_co_env: dict[str, str]
) -> None:
    write_doc._text = "A" * 100  # noqa: SLF001
    result = call_tool(
        write_server,
        "replaceRange",
        {"range": {"start": 0, "end": 60}, "text": "B"},
    )
    assert result["status"] == "needs_confirmation"
    assert result["risk"] == "medium"
    assert result["token"]
    assert result["snapshot_hash"]
    assert "60" in result["summary"]
    assert result["changes"]["deleted_chars"] == 60
    assert result["confirms_required"] == 1
    # the edit did NOT run yet
    assert write_doc._text == "A" * 100  # noqa: SLF001
    # exactly one pre-op commit: `co commit -m "pre: replaceRange" <path>`
    commits = commits_of(read_invocations(fake_co_env))
    assert len(commits) == 1
    assert commits[0][:3] == ["co", "commit", "-m"]
    assert commits[0][3] == "pre: replaceRange"


def test_confirm_op_executes_edit(
    write_server: FastMCP, write_doc: FakeWriteDocument
) -> None:
    write_doc._text = "A" * 100  # noqa: SLF001
    result = call_tool(
        write_server,
        "replaceRange",
        {"range": {"start": 0, "end": 60}, "text": "B"},
    )
    token = result["token"]
    confirmed = call_tool(write_server, "confirmOp", {"token": token})
    assert confirmed["status"] == "confirmed"
    assert confirmed["ok"] is True
    assert write_doc._text == "B" + "A" * 40  # noqa: SLF001
    # the token is consumed
    again = call_tool(write_server, "confirmOp", {"token": token})
    assert again["status"] == "error"


def test_reject_rolls_back_via_co_checkout(
    write_server: FastMCP,
    write_doc: FakeWriteDocument,
    fake_co_env: dict[str, str],
) -> None:
    write_doc._text = "A" * 100  # noqa: SLF001
    result = call_tool(
        write_server,
        "replaceRange",
        {"range": {"start": 0, "end": 60}, "text": "B"},
    )
    snapshot = result["snapshot_hash"]
    rejected = call_tool(write_server, "rejectOp", {"token": result["token"]})
    assert rejected["status"] == "rejected"
    assert rejected["rolled_back"] is True
    assert rejected["snapshot_hash"] == snapshot
    # the fake co binary recorded a checkout of the pre-op snapshot
    invocations = read_invocations(fake_co_env)
    checkouts = [line for line in invocations if line[1] == "checkout"]
    assert len(checkouts) == 1
    assert checkouts[0][:2] == ["co", "checkout"]
    assert checkouts[0][2] == snapshot
    assert checkouts[0][3] == write_doc._path  # noqa: SLF001
    # the edit never ran
    assert write_doc._text == "A" * 100  # noqa: SLF001


def test_high_risk_requires_double_confirmation(
    write_server: FastMCP, write_doc: FakeWriteDocument
) -> None:
    write_doc._text = (  # noqa: SLF001
        "## Intro\nSome body text.\n## Details\nMore details here."
    )
    result = call_tool(
        write_server,
        "refactorSection",
        {"sectionId": 0, "instruction": "## Intro\nNew body."},
    )
    assert result["status"] == "needs_confirmation"
    assert result["risk"] == "high"
    assert result["confirms_required"] == 2

    first = call_tool(write_server, "confirmOp", {"token": result["token"]})
    assert first["status"] == "needs_confirmation"
    assert first["confirms_received"] == 1
    assert first["confirms_required"] == 2
    # still not executed after the first confirm
    assert "Some body text." in write_doc._text  # noqa: SLF001

    second = call_tool(write_server, "confirmOp", {"token": result["token"]})
    assert second["status"] == "confirmed"
    assert second["ok"] is True
    assert "Some body text." not in write_doc._text  # noqa: SLF001
    assert write_doc._text.startswith("## Intro\nNew body.")  # noqa: SLF001
    assert "## Details\nMore details here." in write_doc._text  # noqa: SLF001


def test_low_risk_tools_leave_no_pending_or_commits(
    write_server: FastMCP, write_doc: FakeWriteDocument, fake_co_env: dict[str, str]
) -> None:
    write_doc._text = "Text"  # noqa: SLF001
    for name, args in (
        ("applyStyle", {"range": {"start": 0, "end": 4}, "styleId": "Heading 1"}),
        ("generateTable", {"rows": 2, "cols": 2}),
        ("setPageLayout", {"opts": {"margins": {"top": 15}}}),
    ):
        result = call_tool(write_server, name, args)
        assert result["ok"] is True, f"{name} failed: {result}"
    assert commits_of(read_invocations(fake_co_env)) == []


def test_forbidden_op_blocked_before_any_change(
    write_registry: DocumentRegistry,
    co_client: CoClient,
    write_doc: FakeWriteDocument,
) -> None:
    ctx = WriteContext(registry=write_registry, co_client=co_client)

    def execute() -> dict[str, Any]:
        raise AssertionError("forbidden op must never execute")

    result = _dispatch(ctx, DEFAULT_DOC_ID, "runMacro", {}, execute)
    assert result["status"] == "blocked"
    assert "forbidden" in result["error"]
    assert write_doc.calls == []
    blocked = blocked_op_error("signDocument")
    assert blocked["status"] == "blocked"
    assert "signDocument" in blocked["error"]


def test_confirm_unknown_token_errors(write_server: FastMCP) -> None:
    result = call_tool(write_server, "confirmOp", {"token": "does-not-exist"})
    assert result["status"] == "error"


def test_medium_op_without_saved_path_errors(tmp_path: Path) -> None:
    doc = FakeWriteDocument(text="A" * 100, path=None)
    registry = DocumentRegistry(factory=lambda doc_id, path: doc)
    env = {
        "FAKE_CO_STATE": str(tmp_path / "state.json"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    server = create_server(
        registry=registry,
        co_client=CoClient(bin_path=str(FAKE_CO), env=env),
        round_tracker=RoundTracker(),
    )
    result = call_tool(
        server, "replaceRange", {"range": {"start": 0, "end": 60}, "text": "B"}
    )
    assert result["status"] == "error"
    assert "snapshot" in result["error"]


# --------------------------------------------------------------------------
# round-boundary snapshots (acceptance: exactly one pre-round commit)
# --------------------------------------------------------------------------


def test_round_snapshot_produces_exactly_one_pre_round_commit(
    write_server: FastMCP,
    round_tracker: RoundTracker,
    write_doc: FakeWriteDocument,
    co_client: CoClient,
    fake_co_env: dict[str, str],
) -> None:
    write_doc._text = "Hello"  # noqa: SLF001
    first = round_tracker.start_round("AI-Writer", "alice")
    commits = commits_of(read_invocations(fake_co_env))
    assert len(commits) == 1
    assert commits[0][3] == f"pre: {first.round_id}"

    # a low-risk tool call does NOT create an extra commit
    call_tool(write_server, "insertText", {"pos": "end", "text": " world"})
    commits = commits_of(read_invocations(fake_co_env))
    assert len(commits) == 1

    # a second round creates exactly one more pre-round commit
    second = round_tracker.start_round("AI-Writer", "bob")
    commits = commits_of(read_invocations(fake_co_env))
    assert len(commits) == 2
    assert commits[1][3] == f"pre: {second.round_id}"

    history = co_client.log(write_doc._path)  # noqa: SLF001
    assert [c.message for c in history] == [
        f"pre: {second.round_id}",
        f"pre: {first.round_id}",
    ]


def test_medium_op_adds_pre_op_snapshot_after_round_snapshot(
    write_server: FastMCP,
    round_tracker: RoundTracker,
    write_doc: FakeWriteDocument,
    fake_co_env: dict[str, str],
) -> None:
    write_doc._text = "A" * 100  # noqa: SLF001
    round_ = round_tracker.start_round("AI-Writer")
    result = call_tool(
        write_server,
        "replaceRange",
        {"range": {"start": 0, "end": 60}, "text": "B"},
    )
    assert result["status"] == "needs_confirmation"
    messages = [line[3] for line in commits_of(read_invocations(fake_co_env))]
    assert messages == [f"pre: {round_.round_id}", "pre: replaceRange"]
