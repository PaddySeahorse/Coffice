"""Write-side MCP tool implementations + snapshot middleware (planning doc 4.2).

The six write tools let the agent edit the document through the same
high-level :class:`coffice.core.document.Document` API the human flow uses
(Symmetric Access, doc ch. 3/5). Every edit runs with LO track changes on, so
a round shows up as a small number of reviewable redlines (doc 7.1) instead of
thousands of micro-edits.

Version safety (doc 4.1)
------------------------
Version history is *not* committed per tool call (that would spam ``co log``).
Instead:

- one ``co commit -m "pre: <round_id>"`` snapshot per conversation round,
  taken by the ``start_round`` hook wired in ``coffice.mcp.server``
  (exactly one pre-round commit per round);
- medium/high-risk operations (doc 8.3) take an immediate
  ``co commit -m "pre: <toolName>"`` snapshot and return
  ``{status: "needs_confirmation", summary, changes, snapshot_hash}``; the
  edit executes only after the caller confirms with ``confirmOp {token}``.
  Rejecting with ``rejectOp {token}`` checks the snapshot back out to roll
  back.

Risk classification (doc 8.3)
----------------------------
The classification table (``classify()``, ``RiskLevel``, confirmation
thresholds) is owned by ``coffice.governance.classification`` and re-exported
here unchanged, so the write-tools middleware and the MCP server share one
source of truth. The governance module also layers agent scoping on top (a
forbidden op is blocked before this middleware ever runs -- see
``coffice.governance.guardrails.WriteGuard``).

Hooks
-----
``WriteContext.audit`` is an optional callback the governance bead wires to
its JSONL audit writer; it is kept deliberately simple (a plain
``Callable[[dict], None]``).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from coffice.governance.classification import (
    DELETE_MEDIUM_THRESHOLD,
    FORBIDDEN_OPS,
    RiskLevel,
    blocked_op_error,
    classify,
    confirms_required,
    requires_confirmation,
)
from coffice.mcp.middleware import Round
from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry
from coffice.versioning.co_client import CoClient, CoError

logger = logging.getLogger(__name__)


@dataclass
class PendingOp:
    """A medium/high-risk operation awaiting caller confirmation."""

    token: str
    tool: str
    params: dict[str, Any]
    doc_id: str
    risk: RiskLevel
    snapshot_hash: str
    summary: str
    changes: dict[str, Any]
    confirms_required: int
    confirms_received: int = 0
    execute: Callable[[], dict[str, Any]] | None = None


@dataclass
class WriteContext:
    """State shared by the write tools of one MCP server."""

    registry: DocumentRegistry
    co_client: CoClient
    pending: dict[str, PendingOp] = field(default_factory=dict)
    author: str = "AI-Writer"
    human_operator: str | None = None
    audit: Callable[[dict[str, Any]], None] | None = None

    def snapshot(self, doc_id: str, message: str) -> str | None:
        """Commit the current on-disk doc; returns the hash (None if unsaved)."""
        path = self.registry.path(doc_id)
        if not path:
            return None
        try:
            return self.co_client.commit(
                path,
                message,
                author=self.author,
                human_operator=self.human_operator,
            )
        except CoError as exc:
            logger.warning("snapshot failed for docId=%r: %s", doc_id, exc)
            return None

    def record_audit(self, event: str, **fields: Any) -> None:
        """Fire the audit callback (governance bead) with a JSON-ready dict."""
        if self.audit is None:
            return
        try:
            self.audit(
                {
                    "event": event,
                    "timestamp": datetime.now(UTC).isoformat(),
                    **fields,
                }
            )
        except Exception:  # noqa: BLE001 - auditing must never break a tool
            logger.exception("audit callback failed for event=%s", event)


# ============================================================================
# Round-boundary snapshot (doc 4.1) -- wired as the start_round hook
# ============================================================================


def pre_round_snapshot(
    round_: Round, ctx: WriteContext
) -> dict[str, dict[str, Any]]:
    """Commit one ``pre: <round_id>`` snapshot per opened doc.

    Returns ``{doc_id: {"snapshot_hash": ...}}`` (hash ``None`` + ``error``
    when a doc has no saved path or co is unavailable). Called once per round
    by the ``start_round`` hook, so each conversation round produces exactly
    one pre-round commit -- never one per tool call.
    """
    message = f"pre: {round_.round_id}"
    doc_ids = list(ctx.registry.registered()) or [DEFAULT_DOC_ID]
    results: dict[str, dict[str, Any]] = {}
    for doc_id in doc_ids:
        path = ctx.registry.path(doc_id)
        if not path:
            results[doc_id] = {
                "snapshot_hash": None,
                "error": "no on-disk path for this docId",
            }
            continue
        try:
            hash_ = ctx.co_client.commit(
                path,
                message,
                author=ctx.author,
                human_operator=ctx.human_operator,
            )
            results[doc_id] = {"snapshot_hash": hash_}
            ctx.record_audit(
                "round_snapshot",
                round_id=round_.round_id,
                doc_id=doc_id,
                hash=hash_,
                agent_id=round_.agent_id,
                human_operator=round_.human_operator,
            )
        except CoError as exc:
            logger.warning(
                "pre-round snapshot failed for docId=%r: %s", doc_id, exc
            )
            results[doc_id] = {"snapshot_hash": None, "error": str(exc)}
    return results


# ============================================================================
# Shared middleware helpers
# ============================================================================


def _ensure_track_changes(doc: Any) -> None:
    """Turn on LO track changes so the edit records a reviewable redline."""
    if not getattr(doc, "track_changes_enabled", True):
        doc.enable_track_changes()


def _save(doc: Any) -> str | None:
    """Save the doc so co can see the edit; returns the path (None on failure)."""
    try:
        return doc.save()
    except Exception:  # noqa: BLE001 - unsaved in-memory docs are allowed
        logger.debug("document save failed; edit kept in memory", exc_info=True)
        return None


def _newest_redline_id(doc: Any) -> int | None:
    """Return the id of the most recently recorded redline (if any)."""
    try:
        redlines = doc.get_redlines()
    except Exception:
        return None
    if not redlines:
        return None
    return int(redlines[-1]["id"])


def _section_range(doc: Any, section_id: int) -> tuple[int, int]:
    """Return ``(start, end)`` covering heading ``section_id`` + its body.

    A section runs from its heading's start offset to the next heading's
    start (or the end of the document), as reported by ``getOutline``.
    """
    outline = doc.get_outline()
    index = int(section_id)
    if not outline or index < 0 or index >= len(outline):
        raise ValueError(
            f"section {section_id!r} not found: document has "
            f"{len(outline)} section(s)"
        )
    start = int(outline[index]["start"])
    if index + 1 < len(outline):
        end = int(outline[index + 1]["start"])
    else:
        end = len(doc.get_text())
    return start, max(start, end)


def _describe_op(
    op: str, params: Mapping[str, Any], text: str
) -> tuple[str, dict[str, Any]]:
    """Human summary + machine ``changes`` for the confirmation dialog."""
    if op == "insertText":
        inserted = len(str(params.get("text") or ""))
        return (
            f"Insert {inserted} character(s) at position {params.get('pos')!r}",
            {"tool": op, "pos": params.get("pos"), "inserted_chars": inserted},
        )
    if op == "replaceRange":
        rng = (params or {}).get("range") or {}
        start = max(0, int(rng.get("start", 0) or 0))
        end = max(start, int(rng.get("end", start) or start))
        new_text = str(params.get("text") or "")
        deleted = max(0, min(end, len(text)) - min(start, len(text)))
        return (
            f"Replace {deleted} character(s) at offset {start}..{end} with "
            f"{len(new_text)} character(s)",
            {
                "tool": op,
                "range": {"start": start, "end": end},
                "deleted_chars": deleted,
                "inserted_chars": len(new_text),
            },
        )
    if op == "refactorSection":
        return (
            f"Replace entire section {params.get('sectionId')!r} with the "
            "provided instruction output",
            {"tool": op, "sectionId": params.get("sectionId")},
        )
    return (f"Execute {op}", {"tool": op})


def _dispatch(
    ctx: WriteContext,
    doc_id: str,
    op: str,
    params: Mapping[str, Any],
    execute: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Run the doc 8.3 middleware around ``execute``.

    Forbidden ops are blocked; low-risk ops run immediately; medium/high-risk
    ops snapshot first and return ``needs_confirmation``.
    """
    doc = ctx.registry.get(doc_id)
    text = doc.get_text()
    risk = classify(op, params, text)
    if risk is RiskLevel.FORBIDDEN:
        return blocked_op_error(op)
    required = confirms_required(risk)
    if required == 0:
        try:
            return execute()
        except Exception as exc:  # noqa: BLE001 - surface the edit failure
            logger.warning("write tool %s failed for docId=%r: %s", op, doc_id, exc)
            return {
                "ok": False,
                "tool": op,
                "docId": doc_id,
                "error": f"{type(exc).__name__}: {exc}",
            }
    snapshot_hash = ctx.snapshot(doc_id, f"pre: {op}")
    if not snapshot_hash:
        return {
            "ok": False,
            "tool": op,
            "docId": doc_id,
            "status": "error",
            "error": (
                "cannot take pre-op snapshot: the document has no saved path "
                "or the co CLI is unavailable; refusing to run an "
                "unrecoverable operation"
            ),
        }
    summary, changes = _describe_op(op, params, text)
    token = uuid.uuid4().hex[:12]
    ctx.pending[token] = PendingOp(
        token=token,
        tool=op,
        params=dict(params),
        doc_id=doc_id,
        risk=risk,
        snapshot_hash=snapshot_hash,
        summary=summary,
        changes=changes,
        confirms_required=required,
        execute=execute,
    )
    return {
        "status": "needs_confirmation",
        "token": token,
        "tool": op,
        "docId": doc_id,
        "risk": risk.value,
        "summary": summary,
        "changes": changes,
        "snapshot_hash": snapshot_hash,
        "confirms_required": required,
    }


# ============================================================================
# Write tools (planning doc 4.2 write table)
# ============================================================================


def insert_text(
    ctx: WriteContext,
    doc_id: str = DEFAULT_DOC_ID,
    pos: int | str = 0,
    text: str = "",
    style: str | None = None,
) -> dict[str, Any]:
    """Insert ``text`` at character offset ``pos`` (``"end"`` appends)."""

    def execute() -> dict[str, Any]:
        doc = ctx.registry.get(doc_id)
        _ensure_track_changes(doc)
        end = doc.insert_text(pos, text, style)
        saved = _save(doc)
        ctx.record_audit("op_executed", tool="insertText", doc_id=doc_id, ok=True)
        return {
            "ok": True,
            "tool": "insertText",
            "docId": doc_id,
            "end": end,
            "redline_id": _newest_redline_id(doc),
            "saved": saved is not None,
        }

    return _dispatch(
        ctx, doc_id, "insertText", {"pos": pos, "text": text, "style": style}, execute
    )


def replace_range(
    ctx: WriteContext,
    doc_id: str,
    range: Mapping[str, int],
    text: str,
) -> dict[str, Any]:
    """Replace the text in ``range`` ({start, end} offsets) with ``text``."""

    def execute() -> dict[str, Any]:
        doc = ctx.registry.get(doc_id)
        _ensure_track_changes(doc)
        result = doc.replace_range(range, text)
        saved = _save(doc)
        ctx.record_audit("op_executed", tool="replaceRange", doc_id=doc_id, ok=True)
        return {
            "ok": True,
            "tool": "replaceRange",
            "docId": doc_id,
            **result,
            "redline_id": _newest_redline_id(doc),
            "saved": saved is not None,
        }

    return _dispatch(
        ctx, doc_id, "replaceRange", {"range": range, "text": text}, execute
    )


def apply_style(
    ctx: WriteContext,
    doc_id: str,
    range: Mapping[str, int],
    style_id: str,
) -> dict[str, Any]:
    """Apply paragraph style ``styleId`` to the paragraphs in ``range``."""

    def execute() -> dict[str, Any]:
        doc = ctx.registry.get(doc_id)
        _ensure_track_changes(doc)
        doc.apply_style(range, style_id)
        saved = _save(doc)
        ctx.record_audit("op_executed", tool="applyStyle", doc_id=doc_id, ok=True)
        return {
            "ok": True,
            "tool": "applyStyle",
            "docId": doc_id,
            "styleId": style_id,
            "redline_id": _newest_redline_id(doc),
            "saved": saved is not None,
        }

    return _dispatch(
        ctx, doc_id, "applyStyle", {"range": range, "styleId": style_id}, execute
    )


def generate_table(
    ctx: WriteContext,
    doc_id: str,
    rows: int,
    cols: int,
    data: list[list[Any]] | None = None,
    style: str | None = None,
) -> dict[str, Any]:
    """Append a new ``rows`` x ``cols`` table, optionally filled from ``data``.

    ``style`` is reserved for Phase 2 table styles and is not applied in the
    MVP (the document API exposes paragraph/character styles only).
    """

    def execute() -> dict[str, Any]:
        doc = ctx.registry.get(doc_id)
        _ensure_track_changes(doc)
        result = doc.generate_table(rows, cols, data)
        saved = _save(doc)
        ctx.record_audit("op_executed", tool="generateTable", doc_id=doc_id, ok=True)
        return {
            "ok": True,
            "tool": "generateTable",
            "docId": doc_id,
            **result,
            "redline_id": _newest_redline_id(doc),
            "saved": saved is not None,
        }

    return _dispatch(
        ctx,
        doc_id,
        "generateTable",
        {"rows": rows, "cols": cols, "data": data, "style": style},
        execute,
    )


def refactor_section(
    ctx: WriteContext,
    doc_id: str,
    section_id: int,
    instruction: str,
) -> dict[str, Any]:
    """Replace section ``sectionId`` (outline index) with ``instruction``.

    MVP semantics: the agent pre-computes the replacement text and passes it
    as ``instruction``; the whole section (heading + body up to the next
    heading) is replaced with that text.
    """

    def execute() -> dict[str, Any]:
        doc = ctx.registry.get(doc_id)
        _ensure_track_changes(doc)
        start, end = _section_range(doc, section_id)
        doc.replace_range({"start": start, "end": end}, instruction)
        saved = _save(doc)
        ctx.record_audit("op_executed", tool="refactorSection", doc_id=doc_id, ok=True)
        return {
            "ok": True,
            "tool": "refactorSection",
            "docId": doc_id,
            "sectionId": section_id,
            "replaced_range": {"start": start, "end": end},
            "redline_id": _newest_redline_id(doc),
            "saved": saved is not None,
        }

    return _dispatch(
        ctx,
        doc_id,
        "refactorSection",
        {"sectionId": section_id, "instruction": instruction},
        execute,
    )


def set_page_layout(
    ctx: WriteContext,
    doc_id: str,
    opts: Mapping[str, Any],
) -> dict[str, Any]:
    """Set page layout options (margins/page size in mm, column count)."""

    def execute() -> dict[str, Any]:
        doc = ctx.registry.get(doc_id)
        doc.set_page_layout(opts)
        saved = _save(doc)
        ctx.record_audit("op_executed", tool="setPageLayout", doc_id=doc_id, ok=True)
        return {
            "ok": True,
            "tool": "setPageLayout",
            "docId": doc_id,
            "redline_id": _newest_redline_id(doc),
            "saved": saved is not None,
        }

    return _dispatch(ctx, doc_id, "setPageLayout", {"opts": opts}, execute)


# ============================================================================
# Confirmation flow (doc 4.1)
# ============================================================================


def confirm_op(
    ctx: WriteContext, doc_id: str, token: str
) -> dict[str, Any]:
    """Confirm a pending medium/high-risk operation and execute it.

    High-risk operations need two confirms: the first returns another
    ``needs_confirmation``, the second executes.
    """
    pending = ctx.pending.get(token)
    if pending is None:
        return {
            "ok": False,
            "status": "error",
            "error": f"no pending operation for token {token!r} (expired or already resolved)",
        }
    pending.confirms_received += 1
    remaining = pending.confirms_required - pending.confirms_received
    if remaining > 0:
        return {
            "status": "needs_confirmation",
            "token": token,
            "tool": pending.tool,
            "docId": pending.doc_id,
            "risk": pending.risk.value,
            "summary": pending.summary,
            "snapshot_hash": pending.snapshot_hash,
            "confirms_received": pending.confirms_received,
            "confirms_required": pending.confirms_required,
            "message": (
                f"confirmation {pending.confirms_received} of "
                f"{pending.confirms_required} received; confirm again to proceed"
            ),
        }
    ctx.pending.pop(token, None)
    try:
        result = pending.execute() if pending.execute else {}
    except Exception as exc:  # noqa: BLE001 - surface the confirmed-op failure
        logger.warning(
            "confirmed op %s failed for docId=%r: %s",
            pending.tool,
            pending.doc_id,
            exc,
        )
        # Clean up the pre-op snapshot to avoid orphaned commit
        path = ctx.registry.path(pending.doc_id)
        if path:
            try:
                ctx.co_client.checkout(path, pending.snapshot_hash)
                logger.info(
                    "rolled back pre-op snapshot %s after confirmed edit failure",
                    pending.snapshot_hash,
                )
            except CoError as checkout_exc:
                logger.warning(
                    "failed to roll back pre-op snapshot %s: %s",
                    pending.snapshot_hash,
                    checkout_exc,
                )
        return {
            "ok": False,
            "tool": pending.tool,
            "docId": pending.doc_id,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {"status": "confirmed", "token": token, **result}


def reject_op(ctx: WriteContext, doc_id: str, token: str) -> dict[str, Any]:
    """Reject a pending operation: check out its snapshot to roll back."""
    pending = ctx.pending.get(token)
    if pending is None:
        return {
            "ok": False,
            "status": "error",
            "error": f"no pending operation for token {token!r}",
        }
    ctx.pending.pop(token, None)
    rolled_back = False
    path = ctx.registry.path(pending.doc_id)
    if path:
        try:
            ctx.co_client.checkout(path, pending.snapshot_hash)
            rolled_back = True
        except CoError as exc:
            logger.warning(
                "rollback checkout failed for docId=%r: %s", pending.doc_id, exc
            )
    ctx.record_audit(
        "op_rejected",
        tool=pending.tool,
        doc_id=pending.doc_id,
        snapshot_hash=pending.snapshot_hash,
        rolled_back=rolled_back,
    )
    return {
        "status": "rejected",
        "token": token,
        "tool": pending.tool,
        "docId": pending.doc_id,
        "rolled_back": rolled_back,
        "snapshot_hash": pending.snapshot_hash,
    }


__all__ = [
    "DELETE_MEDIUM_THRESHOLD",
    "FORBIDDEN_OPS",
    "PendingOp",
    "RiskLevel",
    "WriteContext",
    "apply_style",
    "blocked_op_error",
    "classify",
    "confirm_op",
    "confirms_required",
    "generate_table",
    "insert_text",
    "pre_round_snapshot",
    "refactor_section",
    "reject_op",
    "replace_range",
    "requires_confirmation",
    "set_page_layout",
]
