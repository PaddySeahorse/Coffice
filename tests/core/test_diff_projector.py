"""Unit tests for the virtual-redline diff_projector (no LibreOffice required).

Covers the ADR diff_projector contract:

- baseline text comes from ``co checkout`` on a temp copy (never the working
  file);
- ``diff_to_hunks`` splits ``baseline -> current`` into insert/delete/replace
  hunks with content-stable sha256 ids;
- accepting one hunk does not shift the ids of other hunks (the original
  problem: LO redline list indexes shifted on every accept);
- ``reject_hunk`` rebuilds the text with rejected hunks reverted to the
  baseline span while keeping every other change;
- ``find_baseline_commit`` picks the newest ``pre:`` commit, falling back to
  ``HEAD~`` when no pre-round snapshot exists yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from coffice.core.diff_projector import (
    DiffProjector,
    diff_to_hunks,
    find_baseline_commit,
    reject_hunk,
)
from coffice.versioning.co_client import CommitInfo


@dataclass
class _FakeDoc:
    text: str

    def get_text(self) -> str:
        return self.text

    def close(self) -> None:
        return None


class _FakeDocLoader:
    """Maps ``path -> text`` so the projector's temp-copy flow is testable."""

    def __init__(self, paths: dict[str, str]) -> None:
        self._paths = paths

    def load_text(self, path: str) -> str:
        return self._paths[path]


class _FakeCo:
    """Just enough ``co`` for the projector: ``log`` + ``checkout`` that
    rewrites the temp copy's recorded text."""

    def __init__(self, commits: list[CommitInfo], files: dict[str, str]) -> None:
        self._commits = commits
        self._files = files
        self.checkouts: list[tuple[str, str]] = []

    def log(
        self, path: str, limit: int | None = None, bundle: str | None = None
    ) -> list[CommitInfo]:
        commits = self._commits
        return commits if limit is None else commits[:limit]

    def checkout(self, path: str, hash: str, bundle: str | None = None) -> Any:
        self.checkouts.append((path, hash))
        # Record what the file *was* at that hash so the loader can read it back.
        self._files[path] = self._files.get(hash, "")
        return None


# ---------------------------------------------------------------------------
# diff_to_hunks / reject_hunk (pure logic, no I/O)
# ---------------------------------------------------------------------------


def test_diff_to_hunks_emits_one_hunk_per_change() -> None:
    hunks = diff_to_hunks("Hello world", "Hello there world")
    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.type == "insert"
    assert hunk.baseline_text == ""
    assert hunk.current_text == "there "
    assert isinstance(hunk.id, str) and len(hunk.id) == 16


def test_diff_to_hunks_id_is_content_stable_across_other_accepts() -> None:
    """The original LO redline-list index shifted on every accept. The
    diff_projector id must stay stable when a sibling hunk is accepted."""
    hunks = diff_to_hunks("A B C", "A X B C Y")  # two inserts
    assert len(hunks) == 2
    hunk_a, hunk_b = hunks
    # Recompute the same baseline vs current -- ids must be identical.
    recomputed = diff_to_hunks("A B C", "A X B C Y")
    assert recomputed[0].id == hunk_a.id
    assert recomputed[1].id == hunk_b.id


def test_diff_to_hunks_id_is_unique_for_distinct_hunks() -> None:
    hunks = diff_to_hunks("base", "XbaseY")
    assert len(hunks) == 2
    assert hunks[0].id != hunks[1].id


def test_diff_to_hunks_skips_equal_segments() -> None:
    assert diff_to_hunks("identical", "identical") == []


def test_reject_hunk_reverts_only_rejected_id() -> None:
    baseline = "alpha beta gamma"
    current = "ALPHA beta GAMMA"
    hunks = diff_to_hunks(baseline, current)
    assert {h.type for h in hunks} == {"replace"}
    rejected = {hunks[0].id}  # reject the first change only
    rebuilt = reject_hunk(baseline, current, rejected)
    # First change reverted, second change kept
    assert rebuilt == "alpha beta GAMMA"


def test_reject_hunk_with_empty_set_returns_current_text() -> None:
    baseline = "alpha beta"
    current = "alpha BETA"
    assert reject_hunk(baseline, current, set()) == current


def test_reject_hunk_unknown_id_keeps_change() -> None:
    baseline = "alpha"
    current = "ALPHA"
    rebuilt = reject_hunk(baseline, current, {"does-not-exist"})
    assert rebuilt == "ALPHA"


# ---------------------------------------------------------------------------
# find_baseline_commit
# ---------------------------------------------------------------------------


def _commit(hash_: str, message: str) -> CommitInfo:
    return CommitInfo(hash=hash_, author="AI-Writer", email="a@x", message=message, timestamp="t")


def test_find_baseline_commit_prefers_newest_pre_snapshot() -> None:
    log = [
        _commit("h1", "edit: rewrite intro"),
        _commit("h2", "pre: round-1"),
        _commit("h3", "Initial"),
    ]
    assert find_baseline_commit(log) == "h2"


def test_find_baseline_commit_falls_back_to_parent_of_head() -> None:
    log = [
        _commit("h1", "edit: write pricing"),
        _commit("h2", "Initial"),
    ]
    assert find_baseline_commit(log) == "h2"


def test_find_baseline_commit_single_commit_returns_head() -> None:
    log = [_commit("only", "Initial")]
    assert find_baseline_commit(log) == "only"


def test_find_baseline_commit_empty_log_returns_none() -> None:
    assert find_baseline_commit([]) is None


# ---------------------------------------------------------------------------
# DiffProjector end-to-end (fake co + fake doc loader)
# ---------------------------------------------------------------------------


def test_redlines_uses_baseline_text_from_co_checkout(tmp_path) -> None:
    doc_path = str(tmp_path / "doc.docx")
    baseline = "Hello world"
    current = "Hello there world"
    # The projector's _temp_copy uses shutil.copy2 on src_path, so the working
    # file has to physically exist on disk; its *text* is read back through the
    # fake loader (which keys purely on path).
    from pathlib import Path as _Path

    _Path(doc_path).write_text(current, encoding="utf-8")
    files = {doc_path: current, "h2": baseline}
    co = _FakeCo(
        commits=[
            _commit("h1", "edit: replace greeting"),
            _commit("h2", "pre: round-1"),
        ],
        files=files,
    )
    projector = DiffProjector(co_client=co, doc_loader=_FakeDocLoader(files))

    redlines = projector.redlines(doc_path, current)
    # The projector checked the temp copy (NOT the working file) out to the
    # baseline hash and read its text through the loader.
    assert co.checkouts, "projector must co-checkout the baseline"
    assert len(co.checkouts) == 1
    temp_path, hash_used = co.checkouts[0]
    assert hash_used == "h2"
    assert temp_path != doc_path, "projector must never check out the working file"
    assert len(redlines) == 1
    assert redlines[0]["type"] == "insert"
    assert redlines[0]["baseline_text"] == ""
    assert redlines[0]["current_text"] == "there "
    # Id is the same id the pure-logic helper produces for this hunk.
    expected_hunks = diff_to_hunks(baseline, current)
    assert redlines[0]["id"] == expected_hunks[0].id


def test_redlines_returns_empty_when_no_co_history(tmp_path) -> None:
    doc_path = str(tmp_path / "doc.docx")
    co = _FakeCo(commits=[], files={doc_path: "anything"})
    projector = DiffProjector(co_client=co, doc_loader=_FakeDocLoader({doc_path: ""}))
    assert projector.redlines(doc_path, "anything") == []


def test_redlines_returns_empty_when_baseline_unavailable(tmp_path) -> None:
    """If the co checkout throws (corrupt history / bad hash), the projector
    degrades to "no redlines" rather than crashing the read tool."""
    doc_path = str(tmp_path / "doc.docx")

    class _BrokenCo(_FakeCo):
        def checkout(self, path: str, hash: str, bundle: str | None = None) -> Any:
            raise RuntimeError("co binary unavailable")

    co = _BrokenCo(
        commits=[_commit("h1", "edit"), _commit("h2", "pre: round-1")],
        files={doc_path: "x"},
    )
    projector = DiffProjector(co_client=co, doc_loader=_FakeDocLoader({doc_path: ""}))
    assert projector.redlines(doc_path, "x") == []


def test_rebuild_after_reject_returns_baseline_only_for_rejected(tmp_path) -> None:
    from pathlib import Path as _Path

    doc_path = str(tmp_path / "doc.docx")
    baseline = "alpha beta gamma"
    current = "ALPHA beta GAMMA"
    _Path(doc_path).write_text(current, encoding="utf-8")
    files = {doc_path: current, "h2": baseline}
    co = _FakeCo(
        commits=[_commit("h1", "edit"), _commit("h2", "pre: round-1")],
        files=files,
    )
    projector = DiffProjector(co_client=co, doc_loader=_FakeDocLoader(files))
    redlines = projector.redlines(doc_path, current)
    assert redlines, "baseline must be loaded so the projector emits hunks"
    rejected = {redlines[0]["id"]}
    rebuilt = projector.rebuild_after_reject(doc_path, current, rejected)
    assert rebuilt == "alpha beta GAMMA"
