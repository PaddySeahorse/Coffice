"""Unit tests for the doc 8.2 guardrails: rate limiting, protected regions,
destructive-operation blocking, and read-only agents."""

from __future__ import annotations

from typing import Any

import pytest

from coffice.governance import (
    AgentRegistry,
    ProtectedRegion,
    ProtectedRegionGuard,
    RateLimiter,
    WriteGuard,
    default_registry,
    parse_protected_regions,
)


class FakeDoc:
    """Minimal stand-in exposing what the protected-region check reads."""

    def __init__(self, text: str, outline: list[dict[str, Any]] | None = None) -> None:
        self._text = text
        self._outline = outline or []

    def get_text(self) -> str:
        return self._text

    def get_outline(self) -> list[dict[str, Any]]:
        return self._outline


# --------------------------------------------------------------------------
# rate limiter (acceptance: rate limit triggers after threshold)
# --------------------------------------------------------------------------


def test_rate_limiter_allows_up_to_limit_then_triggers() -> None:
    limiter = RateLimiter(limit=3, window_seconds=60.0)
    assert limiter.allow("insertText") is True
    assert limiter.allow("insertText") is True
    assert limiter.allow("replaceRange") is True
    # 4th write within the window is over the threshold
    assert limiter.allow("insertText") is False
    assert limiter.remaining("insertText") == 0


def test_rate_limiter_window_slides_with_time() -> None:
    clock = iter([0.0, 1.0, 2.0, 61.0, 62.0])
    limiter = RateLimiter(limit=2, window_seconds=60.0, now=lambda: next(clock))
    assert limiter.allow("insertText") is True  # t=0
    assert limiter.allow("insertText") is True  # t=1
    assert limiter.allow("insertText") is False  # t=2 (over limit)
    assert limiter.allow("insertText") is True  # t=61 (window slid; t=0 expired)
    assert limiter.allow("insertText") is True  # t=62


def test_rate_limiter_per_operation_buckets() -> None:
    limiter = RateLimiter(limit=1, per_operation=True)
    assert limiter.allow("insertText") is True
    assert limiter.allow("insertText") is False
    assert limiter.allow("replaceRange") is True  # separate bucket


def test_rate_limiter_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError):
        RateLimiter(limit=0)


# --------------------------------------------------------------------------
# protected regions (acceptance: protected-region denial)
# --------------------------------------------------------------------------


def test_protected_region_overlap_detection() -> None:
    region = ProtectedRegion(label="Signature", start=100, end=120)
    assert region.contains(90, 100) is False  # touches the boundary only
    assert region.contains(90, 101) is True
    assert region.contains(110, 115) is True  # fully inside
    assert region.contains(119, 130) is True
    assert region.contains(120, 130) is False  # right at the edge


def test_protected_region_rejects_bad_ranges() -> None:
    with pytest.raises(ValueError):
        ProtectedRegion(label="bad", start=10, end=5)


def test_parse_protected_regions_spec() -> None:
    regions = parse_protected_regions("Signature:100:120, Header:0:20")
    assert [r.label for r in regions] == ["Signature", "Header"]
    assert regions[0].start == 100 and regions[0].end == 120


def test_protected_region_guard_scopes_by_doc_id() -> None:
    guard = ProtectedRegionGuard(
        [ProtectedRegion(label="sig", start=100, end=120, doc_id="contract")]
    )
    assert guard.find("contract", 110, 115) is not None
    assert guard.find("other", 110, 115) is None


# --------------------------------------------------------------------------
# WriteGuard composition (acceptance: forbidden op blocked before UNO)
# --------------------------------------------------------------------------


@pytest.fixture()
def agents() -> AgentRegistry:
    return default_registry()


def test_forbidden_destructive_op_blocked_before_any_doc_touch(
    agents: AgentRegistry,
) -> None:
    guard = WriteGuard(agents=agents)
    doc = FakeDoc("x" * 200)
    result = guard.check("AI-Writer", "clearDocument", {}, doc, "default")
    assert result is not None
    assert result["status"] == "blocked"
    assert result["ok"] is False
    assert "clearDocument" in result["error"]
    # the doc was never touched (guard is a pure check)
    assert doc._text == "x" * 200  # noqa: SLF001


def test_doc83_forbidden_ops_blocked_before_any_doc_touch(
    agents: AgentRegistry,
) -> None:
    guard = WriteGuard(agents=agents)
    for op in ("encryptDocument", "signDocument", "runMacro"):
        assert guard.check("AI-Writer", op, {}, FakeDoc("x"), "default") is not None


def test_allowed_low_risk_op_passes_guard(agents: AgentRegistry) -> None:
    guard = WriteGuard(agents=agents)
    assert guard.check("AI-Writer", "insertText", {"pos": 0}, FakeDoc("x"), "default") is None


def test_unknown_agent_blocked(agents: AgentRegistry) -> None:
    guard = WriteGuard(agents=agents)
    result = guard.check("ghost", "insertText", {}, FakeDoc("x"), "default")
    assert result is not None
    assert "unknown agent" in result["error"]


def test_read_only_agent_blocked(agents: AgentRegistry) -> None:
    agents.register("viewer", read_only=True)
    guard = WriteGuard(agents=agents)
    result = guard.check("viewer", "insertText", {}, FakeDoc("x"), "default")
    assert result is not None
    assert "read-only" in result["error"]


def test_rate_limit_blocks_after_threshold(agents: AgentRegistry) -> None:
    guard = WriteGuard(agents=agents, rate_limiter=RateLimiter(limit=2))
    doc = FakeDoc("x")
    assert guard.check("AI-Writer", "insertText", {}, doc, "default") is None
    assert guard.check("AI-Writer", "insertText", {}, doc, "default") is None
    result = guard.check("AI-Writer", "insertText", {}, doc, "default")
    assert result is not None
    assert result["status"] == "rate_limited"


def test_forbidden_op_is_not_rate_limited(agents: AgentRegistry) -> None:
    """Forbidden ops must fail before the rate limiter records them."""
    guard = WriteGuard(agents=agents, rate_limiter=RateLimiter(limit=1))
    assert guard.check("AI-Writer", "clearDocument", {}, FakeDoc("x"), "default") is not None
    # the budget is untouched
    assert guard.rate_limiter.remaining("clearDocument") == 1


def test_protected_region_denies_overlapping_edit(agents: AgentRegistry) -> None:
    regions = ProtectedRegionGuard([ProtectedRegion(label="Signature", start=90, end=110)])
    guard = WriteGuard(agents=agents, protected_regions=regions)
    doc = FakeDoc("x" * 200)

    # range fully inside the protected region -> denied
    result = guard.check(
        "AI-Writer", "replaceRange", {"range": {"start": 95, "end": 105}, "text": "x"},
        doc, "default",
    )
    assert result is not None
    assert result["status"] == "blocked"
    assert "Signature" in result["error"]
    assert result["protectedRegion"]["label"] == "Signature"

    # range outside the protected region -> allowed
    ok = guard.check(
        "AI-Writer", "replaceRange", {"range": {"start": 5, "end": 15}, "text": "x"},
        doc, "default",
    )
    assert ok is None


def test_agent_scope_forbidden_op_blocks_for_that_agent_only(
    agents: AgentRegistry,
) -> None:
    agents.register("edgy", forbidden_operations=frozenset({"setPageLayout"}))
    guard = WriteGuard(agents=agents)
    assert guard.check("edgy", "setPageLayout", {}, FakeDoc("x"), "default") is not None
    # the default agent is unaffected
    assert guard.check("AI-Writer", "setPageLayout", {}, FakeDoc("x"), "default") is None
