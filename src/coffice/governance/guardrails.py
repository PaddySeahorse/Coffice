"""Guardrails: rate limiting, protected regions, destructive-op blocking (doc 8.2).

These run *before* any UNO call: the MCP server wraps every write tool with a
:class:`WriteGuard` that (in order) rejects unknown/read-only agents, blocks
operations forbidden by the agent's scope or the doc 8.3 table, enforces the
per-minute write rate limit, and denies edits that touch a configured
protected region (signature/header areas).

- :class:`RateLimiter` -- sliding-window counter of write operations
  (max N per minute, configurable).
- :class:`ProtectedRegion` / :class:`ProtectedRegionGuard` -- configured
  character ranges that the agent may not edit.
- :class:`WriteGuard` -- the composed check the server calls.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from coffice.governance.agents import AgentRegistry
from coffice.governance.classification import FORBIDDEN_OPS, RiskLevel, classify


class GuardrailError(Exception):
    """Base class for governance guardrail violations."""


class RateLimitError(GuardrailError):
    """The agent exceeded the write-operation rate limit."""

    def __init__(self, op: str, limit: int, window_seconds: float) -> None:
        self.op = op
        self.limit = limit
        self.window_seconds = window_seconds
        super().__init__(
            f"rate limit exceeded for {op!r}: at most {limit} write "
            f"operation(s) per {int(window_seconds)}s"
        )


class ProtectedRegionError(GuardrailError):
    """An edit range overlaps a configured protected region."""

    def __init__(self, label: str, start: int, end: int) -> None:
        self.label = label
        self.start = start
        self.end = end
        super().__init__(
            f"edit range overlaps protected region {label!r} "
            f"({start}..{end}); denied (doc 8.2)"
        )


class ReadOnlyAgentError(GuardrailError):
    """A read-only agent attempted a write operation."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"agent {agent_id!r} is read-only; write denied (doc 8.1)")


class RateLimiter:
    """Sliding-window write-operation rate limiter (doc 8.2).

    Args:
        limit: max number of write operations per ``window_seconds``.
        window_seconds: length of the counting window (default 60s).
        per_operation: when True, count each operation name separately;
            otherwise all write operations share one budget.
        now: injectable clock (``time.monotonic``-compatible) for tests.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        *,
        per_operation: bool = False,
        now: Callable[[], float] | None = None,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self.limit = limit
        self.window_seconds = window_seconds
        self.per_operation = per_operation
        self._now = now or time.monotonic
        #: bucket key -> list of call timestamps (monotonic seconds)
        self._events: dict[str, list[float]] = {}

    def _key(self, op: str) -> str:
        return op if self.per_operation else "_all"

    def _prune(self, key: str, now: float) -> None:
        cutoff = now - self.window_seconds
        events = self._events.get(key)
        if not events:
            return
        kept = [t for t in events if t > cutoff]
        if kept:
            self._events[key] = kept
        else:
            self._events.pop(key, None)

    def allow(self, op: str) -> bool:
        """Record ``op`` and return True when it is under the rate limit."""
        key = self._key(op)
        now = self._now()
        self._prune(key, now)
        events = self._events.setdefault(key, [])
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True

    def check(self, op: str) -> None:
        """Raise :class:`RateLimitError` when ``op`` is over the limit."""
        if not self.allow(op):
            raise RateLimitError(op, self.limit, self.window_seconds)

    def remaining(self, op: str) -> int:
        """Number of further operations allowed right now for the ``op`` key."""
        key = self._key(op)
        self._prune(key, self._now())
        return max(0, self.limit - len(self._events.get(key, [])))

    def reset(self) -> None:
        """Clear all recorded events (used between tests/rounds)."""
        self._events.clear()


@dataclass(frozen=True)
class ProtectedRegion:
    """A configured character range the agent may not edit (doc 8.2).

    Examples: a signature block at the end of a contract, a header/footer
    area, or a fixed prelude. ``doc_id`` None means "applies to every
    document"; otherwise only the named document.
    """

    label: str
    start: int
    end: int
    doc_id: str | None = None

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"protected region {self.label!r}: end ({self.end}) < start "
                f"({self.start})"
            )

    def contains(self, start: int, end: int) -> bool:
        """True when range ``[start, end)`` overlaps this region."""
        return start < self.end and end > self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "docId": self.doc_id,
        }


class ProtectedRegionGuard:
    """Denies edits that overlap any configured :class:`ProtectedRegion`."""

    def __init__(self, regions: Iterable[ProtectedRegion] = ()) -> None:
        self._regions: list[ProtectedRegion] = list(regions)

    def add(self, region: ProtectedRegion) -> None:
        """Register another protected region."""
        self._regions.append(region)

    def regions(self) -> list[ProtectedRegion]:
        return list(self._regions)

    def find(self, doc_id: str, start: int, end: int) -> ProtectedRegion | None:
        """Return the first region ``[start, end)`` overlaps, else None."""
        for region in self._regions:
            if region.doc_id is not None and region.doc_id != doc_id:
                continue
            if region.contains(start, end):
                return region
        return None

    def check_range(self, doc_id: str, start: int, end: int) -> None:
        """Raise :class:`ProtectedRegionError` when the range is protected."""
        region = self.find(doc_id, start, end)
        if region is not None:
            raise ProtectedRegionError(region.label, region.start, region.end)


def parse_protected_regions(
    spec: str, default_doc_id: str | None = None
) -> list[ProtectedRegion]:
    """Parse a compact ``"label:start:end[,label:start:end...]"`` spec.

    Used for the ``COFFICE_PROTECTED_REGIONS`` env var; offsets are character
    offsets into the document text.
    """
    regions: list[ProtectedRegion] = []
    for chunk in (part.strip() for part in (spec or "").split(",")):
        if not chunk:
            continue
        label, start_s, end_s = chunk.split(":")
        regions.append(
            ProtectedRegion(
                label=label.strip(),
                start=int(start_s),
                end=int(end_s),
                doc_id=default_doc_id,
            )
        )
    return regions


#: write-tool names that carry a character range the guard can check.
_RANGE_OPS = ("insertText", "replaceRange", "applyStyle", "refactorSection")


def _edit_range(
    op: str, params: Mapping[str, Any], doc: Any
) -> tuple[int, int] | None:
    """Best-effort ``[start, end)`` the operation would touch, else None."""
    if op == "replaceRange" or op == "applyStyle":
        rng = (params or {}).get("range") or {}
        try:
            start = max(0, int(rng.get("start", 0) or 0))
            end = max(start, int(rng.get("end", start) or start))
        except (TypeError, ValueError):
            return None
        return start, end
    if op == "insertText":
        pos = params.get("pos", 0)
        if pos == "end":
            try:
                return len(doc.get_text()), len(doc.get_text())
            except Exception:  # noqa: BLE001 - guard must not raise on doc access
                return None
        try:
            pos = max(0, int(pos))
        except (TypeError, ValueError):
            return None
        return pos, pos
    if op == "refactorSection":
        try:
            outline = doc.get_outline()
            index = int(params.get("sectionId", 0))
            if not outline or index < 0 or index >= len(outline):
                return None
            start = int(outline[index]["start"])
            if index + 1 < len(outline):
                end = int(outline[index + 1]["start"])
            else:
                end = len(doc.get_text())
            return start, max(start, end)
        except Exception:  # noqa: BLE001 - guard must not raise on doc access
            return None
    return None


class WriteGuard:
    """Composed doc 8.2 guardrail check for write operations.

    Args:
        agents: the :class:`AgentRegistry` to resolve agent scopes from.
        rate_limiter: optional write-op rate limiter (skipped when None).
        protected_regions: optional :class:`ProtectedRegionGuard` (skipped
            when None).

    Call :meth:`check` before executing any write tool; it returns a
    JSON-ready error dict when the operation must be blocked and ``None`` to
    proceed. The order is deliberate: identity -> destructive-op blocking ->
    rate limit -> protected regions, so forbidden operations fail before the
    rate limiter even records them.
    """

    def __init__(
        self,
        agents: AgentRegistry,
        rate_limiter: RateLimiter | None = None,
        protected_regions: ProtectedRegionGuard | None = None,
    ) -> None:
        self.agents = agents
        self.rate_limiter = rate_limiter
        self.protected_regions = protected_regions

    def check(
        self,
        agent_id: str,
        op: str,
        params: Mapping[str, Any] | None = None,
        doc: Any = None,
        doc_id: str = "default",
    ) -> dict[str, Any] | None:
        """Return a blocked-error dict, or None when the op may proceed."""
        agent = self.agents.get(agent_id)
        if agent is None:
            return {
                "status": "blocked",
                "ok": False,
                "tool": op,
                "error": f"unknown agent {agent_id!r}; cannot authorize operation",
            }

        if op in ("confirmOp", "rejectOp"):
            # confirmation flow is not a document write; scope it to the agent
            # but never rate-limit or region-check it.
            if agent.scope.read_only:
                return {
                    "status": "blocked",
                    "ok": False,
                    "tool": op,
                    "error": f"agent {agent_id!r} is read-only (doc 8.1)",
                }
            return None

        if agent.scope.read_only:
            return {
                "status": "blocked",
                "ok": False,
                "tool": op,
                "error": f"agent {agent_id!r} is read-only; write denied (doc 8.1)",
            }

        # destructive-operation blocking (doc 8.3) -- before any rate limiter
        # or document access, so a forbidden op never touches UNO.
        if agent.scope.forbids(op) or op in FORBIDDEN_OPS:
            return {
                "status": "blocked",
                "ok": False,
                "tool": op,
                "error": (
                    f"operation {op!r} is forbidden for agent {agent_id!r} "
                    "(doc 8.3) and was blocked before any document change"
                ),
            }

        # defensive: any operation the classification table marks FORBIDDEN
        # that somehow escaped the scope list is still blocked.
        if classify(op) is RiskLevel.FORBIDDEN:
            return {
                "status": "blocked",
                "ok": False,
                "tool": op,
                "error": f"operation {op!r} is forbidden (doc 8.3)",
            }

        if self.rate_limiter is not None and not self.rate_limiter.allow(op):
            return {
                "status": "rate_limited",
                "ok": False,
                "tool": op,
                "error": (
                    f"rate limit exceeded for {op!r}: at most "
                    f"{self.rate_limiter.limit} write operation(s) per "
                    f"{int(self.rate_limiter.window_seconds)}s (doc 8.2)"
                ),
            }

        if self.protected_regions is not None and op in _RANGE_OPS:
            rng = _edit_range(op, params or {}, doc)
            if rng is not None:
                region = self.protected_regions.find(doc_id, rng[0], rng[1])
                if region is not None:
                    return {
                        "status": "blocked",
                        "ok": False,
                        "tool": op,
                        "error": (
                            f"edit range {rng[0]}..{rng[1]} overlaps protected "
                            f"region {region.label!r} ({region.start}.."
                            f"{region.end}); denied (doc 8.2)"
                        ),
                        "protectedRegion": region.to_dict(),
                    }
        return None


__all__ = [
    "GuardrailError",
    "ProtectedRegion",
    "ProtectedRegionError",
    "ProtectedRegionGuard",
    "RateLimitError",
    "RateLimiter",
    "ReadOnlyAgentError",
    "WriteGuard",
    "parse_protected_regions",
]
