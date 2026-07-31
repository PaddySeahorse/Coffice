"""Unit tests for the per-round middleware primitive (planning doc 4.1)."""

from __future__ import annotations

from coffice.mcp.middleware import RoundTracker, get_tracker, start_round


def test_start_round_returns_round_metadata() -> None:
    round_ = start_round("AI-Writer", "alice")
    assert round_.round_id
    assert round_.agent_id == "AI-Writer"
    assert round_.human_operator == "alice"
    assert round_.started_at.endswith("Z") or "+" in round_.started_at


def test_round_ids_are_unique() -> None:
    first = start_round("AI-Writer")
    second = start_round("AI-Writer")
    assert first.round_id != second.round_id


def test_tracker_tracks_and_ends_rounds() -> None:
    tracker = RoundTracker()
    round_ = tracker.start_round("AI-Writer", "alice")
    assert tracker.active() == {round_.round_id: round_}
    tracker.end_round(round_.round_id)
    assert tracker.active() == {}


def test_tracker_reports_active_rounds() -> None:
    tracker = RoundTracker()
    a = tracker.start_round("AI-Writer")
    b = tracker.start_round("Human")
    assert set(tracker.active()) == {a.round_id, b.round_id}


def test_module_tracker_is_singleton() -> None:
    assert get_tracker() is get_tracker()
