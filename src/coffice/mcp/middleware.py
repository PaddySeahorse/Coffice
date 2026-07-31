"""Per-round middleware hooks for the MCP server (planning doc 4.1).

Snapshots happen once per *conversation round*, NOT per tool call, so the
version history does not get spammed with micro-commits. :func:`start_round`
is the boundary primitive the write-tools and agent beads call at the start of
every user turn; the snapshot middleware they build will hook into the round
it returns. This bead (read tools) only logs round starts and tracks the
active round, as specified for the MVP.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger("coffice.mcp.middleware")


@dataclass(frozen=True)
class Round:
    """Metadata for one conversation round."""

    round_id: str
    agent_id: str
    human_operator: str | None
    started_at: str  #: ISO-8601 UTC timestamp


class RoundTracker:
    """Tracks started rounds; logs each round boundary."""

    def __init__(self) -> None:
        self._active: dict[str, Round] = {}

    def start_round(
        self, agent_id: str, human_operator: str | None = None
    ) -> Round:
        """Open a new round and log the boundary.

        The returned :class:`Round` carries the ``round_id`` that the
        write-tools snapshot middleware will use as the pre-round commit label.
        """
        round_ = Round(
            round_id=uuid.uuid4().hex[:12],
            agent_id=agent_id,
            human_operator=human_operator,
            started_at=datetime.now(UTC).isoformat(),
        )
        self._active[round_.round_id] = round_
        logger.info(
            "round started: round_id=%s agent_id=%s human_operator=%s",
            round_.round_id,
            agent_id,
            human_operator,
        )
        return round_

    def end_round(self, round_id: str) -> None:
        """Close a round (its snapshot boundary is already committed)."""
        if round_id in self._active:
            logger.info("round ended: round_id=%s", round_id)
            self._active.pop(round_id, None)

    def active(self) -> dict[str, Round]:
        """Return the currently open rounds keyed by ``round_id``."""
        return dict(self._active)


_tracker = RoundTracker()


def start_round(agent_id: str, human_operator: str | None = None) -> Round:
    """Start a new conversation round; logs the round start (snapshot hook).

    This is the primitive the write-tools/agent beads consume: call it once at
    the start of a user turn, and the snapshot middleware commits a single
    ``pre: <round>`` snapshot before the round's first edit.
    """
    return _tracker.start_round(agent_id, human_operator)


def get_tracker() -> RoundTracker:
    """Return the process-wide :class:`RoundTracker` (tests may reset it)."""
    return _tracker


__all__ = ["Round", "RoundTracker", "get_tracker", "start_round"]
