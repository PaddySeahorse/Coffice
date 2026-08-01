"""Append-only JSONL audit log (planning doc 8.4).

Every conversation round produces one audit record after its ``co commit``,
with the exact doc 8.4 fields::

    {
      "hash":          "<co commit hash>",
      "author":        "AI-Writer",
      "human_operator": "alice" | null,
      "message":       "pre: a1b2c3d4e5f6",
      "timestamp":     "2026-08-01T00:00:00+00:00",   # ISO-8601 UTC
      "files_changed": ["word/document.xml"],          # zip-internal paths
      "branch":        "main",
      "parent":        "<previous commit hash>" | null
    }

:class:`AuditLog` is deliberately dumb and append-only: it opens the target
path in append mode, never rewrites or truncates, and writes one JSON object
per line so the file stays greppable and tail-able. The write-tools middleware
fires the :class:`WriteContext` audit callback after each round commit;
:func:`round_audit_callback` adapts those events into full 8.4 records, using
the co client to resolve ``parent``/``files_changed``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from coffice.versioning.co_client import CoClient

if TYPE_CHECKING:
    from coffice.mcp.registry import DocumentRegistry

logger = logging.getLogger(__name__)

#: the doc 8.4 field set; every audit record carries all of these keys.
AUDIT_FIELDS = (
    "hash",
    "author",
    "human_operator",
    "message",
    "timestamp",
    "files_changed",
    "branch",
    "parent",
)

#: default branch reported when the co CLI does not expose branch info (the
#: upstream co binary has no branch concept; MVP commits land on HEAD).
DEFAULT_BRANCH = "main"

#: fallback changed-path when co diff is unavailable or the commit has no
#: parent to diff against.
FALLBACK_FILES_CHANGED = ["word/document.xml"]


class AuditLog:
    """Append-only JSONL writer for doc 8.4 operation records.

    Args:
        path: file to append records to. Parent directories are created on
            first write. ``"-"`` writes to stdout (handy for demos/tests).

    The log never truncates an existing file; :meth:`record` always appends.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._stdout = path == "-"

    def record(
        self,
        *,
        hash: str = "",
        author: str = "",
        human_operator: str | None = None,
        message: str = "",
        timestamp: str | None = None,
        files_changed: Sequence[str] | None = None,
        branch: str = DEFAULT_BRANCH,
        parent: str | None = None,
    ) -> dict[str, Any]:
        """Append one doc 8.4 record; returns the JSON-ready dict written."""
        record: dict[str, Any] = {
            "hash": hash,
            "author": author,
            "human_operator": human_operator,
            "message": message,
            "timestamp": timestamp or datetime.now(UTC).isoformat(),
            "files_changed": list(files_changed) if files_changed else [],
            "branch": branch,
            "parent": parent,
        }
        line = json.dumps(record, ensure_ascii=False)
        if self._stdout:
            print(line)
            return record
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return record

    def record_mapping(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        """Record from a mapping; unknown keys are dropped, missing keys get
        defaults, so callers can pass a superset (e.g. raw WriteContext
        events)."""
        return self.record(
            hash=str(fields.get("hash", "")),
            author=str(fields.get("author", "")),
            human_operator=fields.get("human_operator"),
            message=str(fields.get("message", "")),
            timestamp=fields.get("timestamp"),
            files_changed=fields.get("files_changed"),
            branch=str(fields.get("branch", DEFAULT_BRANCH)),
            parent=fields.get("parent"),
        )

    def read(self) -> list[dict[str, Any]]:
        """Read every record back (newest last); used by tests/forensics."""
        if self._stdout or not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("skipping malformed audit line in %s", self.path)
        return entries

    def __len__(self) -> int:
        return len(self.read())


def _commit_context(
    co_client: CoClient | None, path: str | None, commit_hash: str
) -> tuple[str | None, list[str]]:
    """Resolve ``(parent_hash, files_changed)`` for a fresh commit.

    ``parent`` is the previous commit in ``co log`` (newest first); when the
    commit is the first one or co is unavailable, ``parent`` is None and
    ``files_changed`` falls back to ``word/document.xml``.
    """
    if not co_client or not path or not commit_hash:
        return None, list(FALLBACK_FILES_CHANGED)
    try:
        commits = co_client.log(path, limit=2)
    except Exception:  # noqa: BLE001 - auditing must never raise into the tool
        logger.debug("audit: co log unavailable; using fallback files", exc_info=True)
        return None, list(FALLBACK_FILES_CHANGED)
    if not commits:
        return None, list(FALLBACK_FILES_CHANGED)
    parent = commits[1].hash if len(commits) > 1 else None
    if not parent:
        return None, list(FALLBACK_FILES_CHANGED)
    try:
        diff = co_client.diff(path, parent, commit_hash)
        files = [entry.path for entry in diff] or list(FALLBACK_FILES_CHANGED)
        return parent, files
    except Exception:  # noqa: BLE001 - auditing must never raise into the tool
        logger.debug("audit: co diff unavailable; using fallback files", exc_info=True)
        return parent, list(FALLBACK_FILES_CHANGED)


def round_audit_callback(
    audit_log: AuditLog,
    co_client: CoClient,
    registry: DocumentRegistry,
    ctx: Any,
) -> Callable[[dict[str, Any]], None]:
    """Build a ``WriteContext.audit``-compatible callback writing 8.4 records.

    The write-tools middleware fires the callback after each round commit with
    ``{"event": "round_snapshot", "round_id", "doc_id", "hash", "timestamp"}``;
    this callback turns that into a full doc 8.4 record, reading ``author`` /
    ``human_operator`` live from the ``WriteContext`` so per-round operator
    identity is honored. ``registry``/``co_client`` resolve ``parent`` and
    ``files_changed``. Only commit-bearing events are written (round snapshots
    and rejected ops carry a commit hash); plain ``op_executed`` events are
    skipped because the doc 8.4 record is per-commit, and a low-risk edit's
    effects are captured by the round commit that follows.
    """

    def callback(event: dict[str, Any]) -> None:
        try:
            event_name = event.get("event", "")
            if event_name == "round_snapshot":
                commit_hash = event.get("hash")
                if not commit_hash:
                    return
                doc_id = event.get("doc_id") or "default"
                path = registry.path(doc_id) if registry else None
                parent, files_changed = _commit_context(
                    co_client, path, str(commit_hash)
                )
                audit_log.record(
                    hash=str(commit_hash),
                    author=str(event.get("agent_id") or getattr(ctx, "author", "AI-Writer")),
                    human_operator=(
                        event.get("human_operator")
                        if event.get("human_operator") is not None
                        else getattr(ctx, "human_operator", None)
                    ),
                    message=f"pre: {event.get('round_id', '')}",
                    timestamp=event.get("timestamp"),
                    files_changed=files_changed,
                    branch=DEFAULT_BRANCH,
                    parent=parent,
                )
                return
            snapshot_hash = event.get("hash") or event.get("snapshot_hash")
            if event_name == "op_rejected" and snapshot_hash:
                audit_log.record(
                    hash=str(snapshot_hash),
                    author=str(getattr(ctx, "author", "AI-Writer")),
                    human_operator=getattr(ctx, "human_operator", None),
                    message=f"rejected {event.get('tool', 'op')} (rolled back)",
                    timestamp=event.get("timestamp"),
                    files_changed=[],  # no new commit was produced
                    branch=DEFAULT_BRANCH,
                    parent=None,
                )
                return
            # op_executed and other non-commit events are covered by the round
            # commit record; do not spam the 8.4 log with hash-less rows.
            logger.debug(
                "audit: skipping non-commit event %r", event.get("event")
            )
        except Exception:  # noqa: BLE001 - auditing must never break a tool
            logger.exception("audit callback failed for event=%r", event)

    return callback


__all__ = [
    "AUDIT_FIELDS",
    "DEFAULT_BRANCH",
    "FALLBACK_FILES_CHANGED",
    "AuditLog",
    "round_audit_callback",
]
