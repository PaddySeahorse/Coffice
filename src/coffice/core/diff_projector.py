"""Project co-commit diffs into virtual redlines (ADR: Track Changes is display only).

The original design said LibreOffice's native Track Changes should only act as a
*display* of the diff, while the source of truth for every edit lives in the
``co`` history. The agent's write tools therefore never turn on
``RecordChanges``. Instead this module reads the baseline commit's text via
:class:`~coffice.versioning.co_client.CoClient`, diffs it against the current
working text with :mod:`difflib`, and emits a list of *virtual* redlines. Each
redline carries a content-stable ``id`` (a sha256 fingerprint of type + baseline
span + current span + the affected text) so accept/reject keep working even when
other hunks are accepted first.

Accepting a redline is a no-op on the working document (the working text already
contains the change); rejecting one reverts that hunk back to the baseline text
and commits the result through ``co``. The decision log itself is captured by
the audit bead -- there is no separate sidecar file.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Redline id is the first 16 hex chars of the sha256 fingerprint. 16 hex chars =
# 64 bits -- plenty of room for a per-document decision queue and short enough
# to fit comfortably in MCP tool args / audit records.
ID_HEX_LEN = 16


class _CoLike(Protocol):
    """Minimal slice of :class:`CoClient` used by :class:`DiffProjector`."""

    def log(self, path: str, limit: int | None = None, bundle: str | None = None) -> list[Any]: ...

    def checkout(self, path: str, hash: str, bundle: str | None = None) -> Any: ...


class _DocLoader(Protocol):
    """Loads a saved Office file into a transient :class:`Document` for reading."""

    def load_text(self, path: str) -> str: ...


@dataclass(frozen=True)
class Hunk:
    """One ``baseline <-> current`` change segment.

    ``baseline_start``/``baseline_end`` index into the baseline text,
    ``current_start``/``current_end`` into the current text. ``type`` is the
    difflib opcode mapped to ``insert`` / ``delete`` / ``replace``.
    """

    type: str
    baseline_start: int
    baseline_end: int
    current_start: int
    current_end: int
    baseline_text: str
    current_text: str
    author: str

    @property
    def id(self) -> str:
        return _hunk_id(
            self.type,
            self.baseline_start,
            self.baseline_end,
            self.current_start,
            self.current_end,
            self.baseline_text,
            self.current_text,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "author": self.author,
            "baseline_range": {"start": self.baseline_start, "end": self.baseline_end},
            "current_range": {"start": self.current_start, "end": self.current_end},
            "baseline_text": self.baseline_text,
            "current_text": self.current_text,
        }


def _hunk_id(
    type_: str,
    baseline_start: int,
    baseline_end: int,
    current_start: int,
    current_end: int,
    baseline_text: str,
    current_text: str,
) -> str:
    payload = "|".join(
        (
            type_,
            f"{baseline_start}:{baseline_end}",
            f"{current_start}:{current_end}",
            baseline_text,
            current_text,
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:ID_HEX_LEN]


def _opcode_type(tag: str) -> str:
    if tag == "insert":
        return "insert"
    if tag == "delete":
        return "delete"
    if tag == "replace":
        return "replace"
    # ``equal`` segments are not redlines.
    return ""


def diff_to_hunks(
    baseline_text: str,
    current_text: str,
    author: str = "AI-Writer",
) -> list[Hunk]:
    """Split ``baseline_text -> current_text`` into redline :class:`Hunk` items.

    Uses :class:`difflib.SequenceMatcher` over the two strings character by
    character. ``equal`` segments are skipped; every other opcode becomes a
    single hunk. Ids are content-stable: accepting one hunk does not change the
    id of any sibling hunk (the id is derived from the baseline+current spans of
    *that* hunk, not from list position).
    """
    matcher = difflib.SequenceMatcher(a=baseline_text, b=current_text, autojunk=False)
    hunks: list[Hunk] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        mapped = _opcode_type(tag)
        if not mapped:
            continue
        hunks.append(
            Hunk(
                type=mapped,
                baseline_start=i1,
                baseline_end=i2,
                current_start=j1,
                current_end=j2,
                baseline_text=baseline_text[i1:i2],
                current_text=current_text[j1:j2],
                author=author,
            )
        )
    return hunks


def reject_hunk(
    baseline_text: str,
    current_text: str,
    rejected_ids: set[str],
) -> str:
    """Rebuild the working text with ``rejected_ids`` hunks reverted to baseline.

    Hunks whose id is in ``rejected_ids`` fall back to their baseline span; all
    other non-equal hunks keep the current text. ``equal`` segments always come
    from the baseline (they are identical on both sides).
    """
    matcher = difflib.SequenceMatcher(a=baseline_text, b=current_text, autojunk=False)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.append(baseline_text[i1:i2])
            continue
        hunk_id = _hunk_id(
            _opcode_type(tag),
            i1,
            i2,
            j1,
            j2,
            baseline_text[i1:i2],
            current_text[j1:j2],
        )
        if hunk_id in rejected_ids:
            out.append(baseline_text[i1:i2])
        else:
            out.append(current_text[j1:j2])
    return "".join(out)


def _reject_from_hunks(
    baseline_text: str,
    hunks: list[Hunk],
    rejected_ids: set[str],
) -> str:
    """Rebuild working text from already-computed ``hunks`` (no difflib rerun).

    Walks the baseline text, filling equal gaps with the baseline content and
    each hunk span with either the baseline (rejected) or current (kept) text.
    Equivalent to :func:`reject_hunk` but reuses a prior :func:`diff_to_hunks`
    pass so callers that already fetched the redlines list (e.g. to validate a
    redline id) do not run the SequenceMatcher a second time.
    """
    out: list[str] = []
    pos = 0
    for hunk in sorted(hunks, key=lambda h: h.baseline_start):
        out.append(baseline_text[pos:hunk.baseline_start])  # equal gap before hunk
        out.append(hunk.baseline_text if hunk.id in rejected_ids else hunk.current_text)
        pos = hunk.baseline_end
    out.append(baseline_text[pos:])  # trailing equal segment
    return "".join(out)


def find_baseline_commit(log: list[Any], prefix: str = "pre: ") -> str | None:
    """Pick the most recent ``pre:`` commit from ``co log`` as the diff baseline.

    The MCP server takes one ``pre: <round_id>`` snapshot per round, so the
    newest ``pre:`` commit is the natural "what the document looked like at the
    start of this round" baseline. If no ``pre:`` commit exists yet (very first
    round before any snapshot has run) we fall back to the second-newest commit
    (i.e. ``HEAD~``) when available -- the newest commit is usually the in-round
    write the agent just made, which is exactly what we are diffing *against* the
    baseline.
    """
    pre_match = next(
        (c for c in log if str(getattr(c, "message", "")).startswith(prefix)), None
    )
    if pre_match is not None:
        pre_hash = getattr(pre_match, "hash", "")
        if isinstance(pre_hash, str) and pre_hash:
            return pre_hash
    if len(log) >= 2:
        head_hash = getattr(log[1], "hash", "")
        return str(head_hash) if head_hash else None
    if log:
        head_hash = getattr(log[0], "hash", "")
        return str(head_hash) if head_hash else None
    return None


class DiffProjector:
    """Per-document virtual-redline projector.

    Construction is cheap; the heavy work (loading the baseline text) happens on
    :meth:`redlines` and is cached on the instance so multiple calls within one
    request do not reload the baseline.
    """

    def __init__(
        self,
        co_client: _CoLike,
        doc_loader: _DocLoader,
        temp_dir: str | None = None,
    ) -> None:
        self._co = co_client
        self._loader = doc_loader
        self._temp_dir = temp_dir or tempfile.gettempdir()
        self._baseline_cache: dict[str, tuple[str, str]] = {}
        # Hunks cache keyed by ``(doc_path, current_text, baseline_hash,
        # author)``. A single-entry cache is enough because reject_redline
        # always validates the id (computing redlines) immediately before
        # rebuilding -- reusing the SequenceMatcher pass avoids the redundant
        # diff the reviewer flagged.
        self._hunks_cache: tuple[tuple[Any, ...], str, list[Hunk]] | None = None

    def _compute_hunks(
        self,
        doc_path: str,
        current_text: str,
        baseline_hash: str | None,
        author: str,
    ) -> list[Hunk]:
        """Resolve the baseline and diff it against ``current_text`` (cached).

        Returns the hunks; the resolved baseline hash is held in
        :attr:`_hunks_cache` so a follow-up :meth:`rebuild_after_reject` with
        ``baseline_hash=None`` can fetch the baseline text without re-running
        ``co log``.
        """
        key = (doc_path, current_text, baseline_hash, author)
        if self._hunks_cache is not None and self._hunks_cache[0] == key:
            return self._hunks_cache[2]
        if baseline_hash is None:
            log = self._co.log(doc_path)
            baseline_hash = find_baseline_commit(log)
        resolved_hash = baseline_hash or ""
        if not resolved_hash:
            return []
        try:
            baseline_text = self.baseline_text(doc_path, resolved_hash)
        except Exception as exc:  # noqa: BLE001 - baseline unavailable is non-fatal
            logger.warning(
                "diff_projector: cannot load baseline %s for %s: %s",
                resolved_hash,
                doc_path,
                exc,
            )
            return []
        hunks = diff_to_hunks(baseline_text, current_text, author=author)
        self._hunks_cache = (key, resolved_hash, hunks)
        return hunks

    def baseline_text(self, doc_path: str, baseline_hash: str) -> str:
        """Return the working text of ``baseline_hash`` without touching ``doc_path``.

        ``co checkout`` overwrites the file on disk, so we copy the live document
        to a temp file (preserving its ``.co/`` history), check the temp file
        out to ``baseline_hash``, load its text, and discard the temp file.
        """
        cached = self._baseline_cache.get(f"{doc_path}:{baseline_hash}")
        if cached is not None:
            return cached[1]
        tmp_path = self._temp_copy(doc_path)
        try:
            self._co.checkout(tmp_path, baseline_hash)
            text = self._loader.load_text(tmp_path)
        finally:
            self._discard(tmp_path)
        self._baseline_cache[f"{doc_path}:{baseline_hash}"] = (baseline_hash, text)
        return text

    def redlines(
        self,
        doc_path: str,
        current_text: str,
        baseline_hash: str | None = None,
        author: str = "AI-Writer",
    ) -> list[dict[str, Any]]:
        """Return virtual redlines for ``current_text`` vs the chosen baseline.

        When ``baseline_hash`` is ``None`` the projector picks one via
        :func:`find_baseline_commit` from ``co log``. If no co history exists
        yet (fresh document, never snapshotted) the returned list is empty --
        there is nothing to diff against, and that is fine: the first round has
        no reviewable redlines by construction. The hunks pass is cached so a
        follow-up ``rebuild_after_reject`` for the same text reuses it instead of
        running the diff a second time.
        """
        hunks = self._compute_hunks(doc_path, current_text, baseline_hash, author)
        return [h.to_dict() for h in hunks]

    def rebuild_after_reject(
        self,
        doc_path: str,
        current_text: str,
        rejected_ids: set[str],
        baseline_hash: str | None = None,
        author: str = "AI-Writer",
    ) -> str:
        """Return the working text after ``rejected_ids`` hunks are rolled back.

        Prefers the cached hunks produced by a preceding :meth:`redlines` call
        (same doc, text, baseline, author) so the reject path does not run the
        SequenceMatcher twice; falls back to :func:`reject_hunk` on cache miss.
        """
        hunks = self._compute_hunks(doc_path, current_text, baseline_hash, author)
        if not hunks:
            if baseline_hash is None:
                log = self._co.log(doc_path)
                baseline_hash = find_baseline_commit(log)
            if not baseline_hash:
                return current_text
            baseline_text = self.baseline_text(doc_path, baseline_hash)
            return reject_hunk(baseline_text, current_text, rejected_ids)
        # Cache holds the resolved baseline hash alongside the hunks.
        resolved_hash = self._hunks_cache[1] if self._hunks_cache else ""
        baseline_text = self.baseline_text(doc_path, resolved_hash) if resolved_hash else ""
        return _reject_from_hunks(baseline_text, hunks, rejected_ids)

    # -- internals ------------------------------------------------------------

    def _temp_copy(self, src_path: str) -> str:
        root, ext = os.path.splitext(os.path.basename(src_path))
        fd, tmp_path = tempfile.mkstemp(
            prefix=f"coffice-baseline-{root}-",
            suffix=ext or ".odt",
            dir=self._temp_dir,
        )
        os.close(fd)
        # ``.co`` history lives *inside* the Office ZIP container (ADR-001), so a
        # plain file copy carries the whole history with it -- no sibling ``.co``
        # directory to mirror. ``co checkout(tmp_path, hash)`` then rewrites the
        # temp file in place, leaving the live working document untouched.
        shutil.copy2(src_path, tmp_path)
        return tmp_path

    def _discard(self, tmp_path: str) -> None:
        try:
            os.remove(tmp_path)
        except OSError:
            logger.debug("diff_projector: could not remove temp file %s", tmp_path, exc_info=True)


def make_doc_loader(open_callable: Callable[[str], Any]) -> _DocLoader:
    """Build a :class:`_DocLoader` from a ``Document.open``-shaped callable.

    The callable must return an object exposing ``get_text()`` and ``close()``;
    both are part of the existing :class:`Document` API.
    """

    class _Loader:
        def load_text(self, path: str) -> str:
            doc = open_callable(path)
            try:
                return str(doc.get_text())
            finally:
                try:
                    doc.close()
                except Exception:  # noqa: BLE001 - cleanup must never raise
                    logger.debug("doc_loader: close failed for %s", path, exc_info=True)

    return _Loader()
