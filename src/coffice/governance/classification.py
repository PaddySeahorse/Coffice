"""doc 8.3 operation-risk classification table (planning doc ch. 8).

The governance module OWNS this table; the write-tools bead re-exports these
functions unchanged (``coffice.mcp.tools_write.classify`` etc.) so its
middleware and the MCP server share one source of truth.

Risk classes (doc 8.3):

- LOW      -- insert text, apply style, set page layout, add a table, small
              replace (no confirmation; round snapshot only)
- MEDIUM   -- replaceRange deleting > 50 chars, replace table (confirm once)
- HIGH     -- delete a whole section (refactorSection), clear document
              (double confirm)
- FORBIDDEN -- encryption/signing/macro: blocked before any UNO call, the
               tool returns an error

The confirmation thresholds (0/1/2) are derived here too, so the agent-loop
and write-tools beads share one ``requires_confirmation`` implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

#: doc 8.3 forbidden row: encryption/signing/macro are blocked before any edit.
FORBIDDEN_OPS = frozenset({"encryptDocument", "signDocument", "runMacro"})

#: doc 8.3 medium row: a replaceRange that deletes more than this many
#: characters requires confirmation.
DELETE_MEDIUM_THRESHOLD = 50


class RiskLevel(StrEnum):
    """doc 8.3 risk classes for write operations."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    FORBIDDEN = "forbidden"


#: confirmation steps required per risk level (0 = none, 1 = single confirm,
#: 2 = double confirm). Forbidden operations are never confirmed; they error.
_CONFIRMS_REQUIRED: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.FORBIDDEN: 0,
}

#: static risk of each known tool; replaceRange is refined by :func:`classify`
#: against the actual deletion size.
_BASE_RISK: dict[str, RiskLevel] = {
    "insertText": RiskLevel.LOW,
    "applyStyle": RiskLevel.LOW,
    "setPageLayout": RiskLevel.LOW,
    "generateTable": RiskLevel.LOW,
    "replaceRange": RiskLevel.LOW,
    "refactorSection": RiskLevel.HIGH,
    "clearDocument": RiskLevel.HIGH,
}


def classify(
    op: str,
    params: Mapping[str, Any] | None = None,
    text: str = "",
) -> RiskLevel:
    """Return the doc 8.3 risk level of ``op`` given ``params`` and doc text.

    ``replaceRange`` is contextual: an empty range is an insert (LOW), a
    deletion of > 50 chars is MEDIUM, and a range covering the whole document
    ("clear document") is HIGH. Unknown operation names default to LOW so the
    agent is never hard-blocked by a classification gap.
    """
    if op in FORBIDDEN_OPS:
        return RiskLevel.FORBIDDEN
    risk = _BASE_RISK.get(op, RiskLevel.LOW)
    if op == "replaceRange":
        rng = (params or {}).get("range") or {}
        try:
            start = max(0, int(rng.get("start", 0) or 0))
            end = int(rng.get("end", start) or start)
        except (TypeError, ValueError):
            start = end = 0
        if end < start:
            start, end = end, start
        length = len(text or "")
        if start <= 0 and end >= length and length > 0:
            return RiskLevel.HIGH
        if end - start > DELETE_MEDIUM_THRESHOLD:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW
    return risk


def requires_confirmation(risk: RiskLevel, op: str = "") -> bool:
    """True when ``risk`` needs at least one explicit caller confirmation."""
    return confirms_required(risk) > 0


def confirms_required(risk: RiskLevel) -> int:
    """Number of confirmations needed for ``risk`` (0, 1, or 2)."""
    return _CONFIRMS_REQUIRED.get(risk, 0)


def blocked_op_error(op: str) -> dict[str, Any]:
    """Structured error for a forbidden (doc 8.3) operation."""
    return {
        "status": "blocked",
        "tool": op,
        "ok": False,
        "error": (
            f"operation {op!r} is forbidden (encryption/signing/macro, doc 8.3) "
            "and was blocked before any document change"
        ),
    }


__all__ = [
    "DELETE_MEDIUM_THRESHOLD",
    "FORBIDDEN_OPS",
    "RiskLevel",
    "blocked_op_error",
    "classify",
    "confirms_required",
    "requires_confirmation",
]
