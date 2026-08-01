"""Unit tests for the doc 8.3 operation-classification table.

The governance module OWNS the table; ``coffice.mcp.tools_write`` re-exports
the same objects, so the write-tools middleware and the MCP server share one
source of truth. These tests pin the table behavior and the ownership link.
"""

from __future__ import annotations

import coffice.governance.classification as gov_classification
import coffice.mcp.tools_write as tools_write
from coffice.governance.classification import (
    FORBIDDEN_OPS,
    RiskLevel,
    blocked_op_error,
    classify,
    confirms_required,
    requires_confirmation,
)


def test_governance_owns_the_classification_table() -> None:
    # the write-tools middleware re-exports the very same objects
    assert tools_write.classify is gov_classification.classify
    assert tools_write.RiskLevel is gov_classification.RiskLevel
    assert tools_write.requires_confirmation is gov_classification.requires_confirmation
    assert tools_write.confirms_required is gov_classification.confirms_required
    assert tools_write.FORBIDDEN_OPS == FORBIDDEN_OPS


def test_classify_low_risk_ops() -> None:
    assert classify("insertText") is RiskLevel.LOW
    assert classify("applyStyle") is RiskLevel.LOW
    assert classify("setPageLayout") is RiskLevel.LOW
    assert classify("generateTable") is RiskLevel.LOW
    assert classify("totallyUnknownOp") is RiskLevel.LOW


def test_classify_replace_range_thresholds() -> None:
    text = "x" * 100
    assert (
        classify("replaceRange", {"range": {"start": 0, "end": 10}}, text)
        is RiskLevel.LOW
    )
    assert (
        classify("replaceRange", {"range": {"start": 0, "end": 50}}, text)
        is RiskLevel.LOW
    )
    assert (
        classify("replaceRange", {"range": {"start": 0, "end": 51}}, text)
        is RiskLevel.MEDIUM
    )
    assert (
        classify("replaceRange", {"range": {"start": 0, "end": 0}}, text)
        is RiskLevel.LOW
    )
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


def test_blocked_op_error_shape() -> None:
    err = blocked_op_error("runMacro")
    assert err["status"] == "blocked"
    assert err["ok"] is False
    assert err["tool"] == "runMacro"
    assert "forbidden" in err["error"]
