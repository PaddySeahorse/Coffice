"""Tests for the agent session tool-calling loop (planning doc 3.3 + ch. 4).

Acceptance criteria covered here: with a scripted (mock) LLM, a full round
(outline -> insertText -> snapshot) produces exactly one pre-round commit,
one redline group, one audit entry, and a session change summary; the
confirmation flow pauses, resumes on confirm, and rolls back on reject.
"""

from __future__ import annotations

from tests.agent.conftest import commits_of, read_invocations, tool_call
from tests.mcp.test_server import call_tool

from coffice.agent import HUMAN_ONLY_TOOLS, ChatMessage


def test_system_prompt_includes_identity_tools_and_rules(session_factory) -> None:
    session, llm = session_factory([ChatMessage(content="hi")])
    result = session.chat("hello")
    assert result.status == "complete"
    system = llm.calls[0][0][0]
    assert system["role"] == "system"
    assert "AI-Writer" in system["content"]
    assert "getOutline" in system["content"]
    assert "one logical change per tool call" in system["content"]
    assert "needs_confirmation" in system["content"]
    assert "insertText" in system["content"]


def test_confirm_and_reject_tools_not_offered_to_model(session_factory) -> None:
    session, llm = session_factory([ChatMessage(content="done")])
    session.chat("no edits")
    names = llm.tool_names_seen()
    assert "insertText" in names
    for human_only in HUMAN_ONLY_TOOLS:
        assert human_only not in names


def test_full_round_acceptance(
    session_factory,
    agent_server,
    audit_log,
    fake_co_env,
    doc,
) -> None:
    """outline -> insertText -> snapshot: one commit, one redline group, one
    audit entry, and a change summary."""
    llm_steps = [
        tool_call("c1", "getOutline"),
        tool_call(
            "c2",
            "insertText",
            pos="end",
            text="\n\n## Pricing\nWe charge per seat, billed yearly.",
        ),
        ChatMessage(content="Added a Pricing section."),
    ]
    session, llm = session_factory(llm_steps)
    result = session.chat("Add a section about pricing")

    # final assistant message is returned
    assert result.status == "complete"
    assert result.reply == "Added a Pricing section."
    assert result.round_id

    # change summary lists the applied tool calls, in order
    assert [c["tool"] for c in result.change_summary] == [
        "getOutline",
        "insertText",
    ]
    assert result.change_summary[1]["result"]["ok"] is True

    # exactly one pre-round commit, taken BEFORE the first tool call
    invocations = read_invocations(fake_co_env)
    assert invocations[0][1] == "commit"
    commits = commits_of(invocations)
    assert len(commits) == 1
    assert commits[0][3] == f"pre: {result.round_id}"

    # one redline group (the insert recorded a tracked change)
    redlines = call_tool(agent_server, "getRedlines", {})
    assert redlines["redlines"]
    assert redlines["redlines"][0]["type"] == "insert"

    # exactly one audit record, carrying the round commit
    entries = audit_log.read()
    assert len(entries) == 1
    assert entries[0]["message"] == f"pre: {result.round_id}"
    assert entries[0]["author"] == "AI-Writer"
    assert entries[0]["human_operator"] == "alice"
    assert entries[0]["hash"]

    # the document was edited (whole-paragraph insert, non-streaming MVP)
    assert "## Pricing" in doc._text  # noqa: SLF001


def test_conversation_history_persists_across_chats(session_factory) -> None:
    session, llm = session_factory(
        [ChatMessage(content="first reply"), ChatMessage(content="second reply")]
    )
    first = session.chat("message one")
    assert first.reply == "first reply"
    second = session.chat("message two")
    assert second.reply == "second reply"
    # both user messages are part of the model's conversation history
    roles = [m["role"] for m in llm.calls[1][0]]
    assert roles.count("user") == 2


def test_loop_stops_when_model_stops_calling_tools(session_factory) -> None:
    session, llm = session_factory(
        [
            tool_call("c1", "getOutline"),
            tool_call("c2", "insertText", pos="end", text="Paragraph."),
            ChatMessage(content="Finished."),
        ]
    )
    result = session.chat("write something")
    assert result.status == "complete"
    assert result.reply == "Finished."
    assert len(result.change_summary) == 2
    assert len(llm.calls) == 3


def test_llm_error_returns_error_result(session_factory) -> None:
    from coffice.agent import LLMRequestError

    class BrokenLLM:
        def chat(self, messages, tools=None):
            raise LLMRequestError("connection refused")

    session, _ = session_factory()
    session._llm = BrokenLLM()  # noqa: SLF001
    result = session.chat("hello")
    assert result.status == "error"
    assert "connection refused" in result.error


def test_medium_risk_confirmation_flow(session_factory, doc) -> None:
    doc._text = "A" * 100  # noqa: SLF001
    llm_steps = [
        tool_call(
            "c1",
            "replaceRange",
            range={"start": 0, "end": 60},
            text="B",
        ),
        ChatMessage(content="Replaced."),
    ]
    session, llm = session_factory(llm_steps)
    result = session.chat("Replace a big chunk")

    # loop paused; no second LLM call happened yet
    assert result.status == "needs_confirmation"
    assert result.needs_confirmation is True
    assert result.confirmation["status"] == "needs_confirmation"
    assert result.confirmation["risk"] == "medium"
    token = result.confirmation["token"]
    assert len(llm.calls) == 1
    assert doc._text == "A" * 100  # noqa: SLF001 - edit not applied yet

    # confirming executes the edit and resumes the loop
    confirmed = session.confirm(token)
    assert confirmed.status == "complete"
    assert confirmed.reply == "Replaced."
    assert doc._text == "B" + "A" * 40  # noqa: SLF001
    # the confirmation outcome is part of the change summary
    assert confirmed.change_summary[-1]["tool"] == "replaceRange"
    assert confirmed.change_summary[-1]["result"]["status"] == "confirmed"


def test_new_message_blocked_while_confirmation_pending(session_factory, doc) -> None:
    doc._text = "A" * 100  # noqa: SLF001
    llm_steps = [
        tool_call("c1", "replaceRange", range={"start": 0, "end": 60}, text="B"),
        ChatMessage(content="done"),
    ]
    session, _ = session_factory(llm_steps)
    result = session.chat("edit")
    assert result.status == "needs_confirmation"
    blocked = session.chat("another message")
    assert blocked.status == "error"
    assert "awaiting confirmation" in blocked.error


def test_reject_rolls_back_via_snapshot_hash(session_factory, doc, fake_co_env) -> None:
    doc._text = "A" * 100  # noqa: SLF001
    llm_steps = [
        tool_call("c1", "replaceRange", range={"start": 0, "end": 60}, text="B"),
        ChatMessage(content="Rolled back."),
    ]
    session, _ = session_factory(llm_steps)
    result = session.chat("edit")
    assert result.status == "needs_confirmation"
    snapshot_hash = result.confirmation["snapshot_hash"]
    assert snapshot_hash

    rejected = session.reject(result.confirmation["token"])
    assert rejected.status == "complete"
    assert rejected.reply == "Rolled back."
    # the fake co binary recorded a checkout of the pre-op snapshot
    invocations = read_invocations(fake_co_env)
    checkouts = [line for line in invocations if line[1] == "checkout"]
    assert len(checkouts) == 1
    assert checkouts[0][2] == snapshot_hash
    assert rejected.change_summary[-1]["result"]["status"] == "rejected"


def test_high_risk_requires_double_confirmation(session_factory, doc) -> None:
    doc._text = "## Intro\nSome body text.\n## Details\nMore details here."  # noqa: SLF001
    llm_steps = [
        tool_call(
            "c1",
            "refactorSection",
            sectionId=0,
            instruction="## Intro\nNew body.",
        ),
        ChatMessage(content="Refactored."),
    ]
    session, _ = session_factory(llm_steps)
    result = session.chat("refactor the intro")
    assert result.status == "needs_confirmation"
    assert result.confirmation["risk"] == "high"
    assert result.confirmation["confirms_required"] == 2
    token = result.confirmation["token"]

    first = session.confirm(token)
    assert first.status == "needs_confirmation"
    assert first.confirmation["confirms_received"] == 1
    assert "Some body text." in doc._text  # noqa: SLF001 - still not executed

    second = session.confirm(token)
    assert second.status == "complete"
    assert "Some body text." not in doc._text  # noqa: SLF001
    assert doc._text.startswith("## Intro\nNew body.")  # noqa: SLF001


def test_confirm_unknown_token_errors(session_factory) -> None:
    session, _ = session_factory([ChatMessage(content="ok")])
    result = session.confirm("no-such-token")
    assert result.status == "error"
    assert "no pending confirmation" in result.error


def test_audit_record_written_for_confirmed_round(
    session_factory, doc, audit_log, fake_co_env
) -> None:
    """A confirmed medium-risk round still lands exactly one audit record."""
    doc._text = "A" * 100  # noqa: SLF001
    llm_steps = [
        tool_call("c1", "replaceRange", range={"start": 0, "end": 60}, text="B"),
        ChatMessage(content="done"),
    ]
    session, _ = session_factory(llm_steps)
    result = session.chat("edit")
    session.confirm(result.confirmation["token"])
    entries = audit_log.read()
    assert len(entries) == 1
    assert entries[0]["message"] == f"pre: {result.round_id}"
