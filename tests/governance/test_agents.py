"""Unit tests for the agent identity registry and doc 4.2 permission table."""

from __future__ import annotations

import pytest

from coffice.governance import (
    AI_WRITER_ID,
    AgentRegistry,
    AgentScope,
    UnknownAgentError,
    default_agent,
    default_registry,
)
from coffice.governance.permissions import get_permissions


def test_default_registry_ships_the_ai_writer_singleton() -> None:
    agents = default_registry()
    assert AI_WRITER_ID in agents
    agent = agents.require(AI_WRITER_ID)
    assert agent.display_name == "AI-Writer"
    assert agent.scope.read_only is False
    assert "*" in agent.scope.allowed_sections


def test_unknown_agent_raises() -> None:
    agents = AgentRegistry()
    with pytest.raises(UnknownAgentError):
        agents.require("ghost")
    assert agents.get("ghost") is None


def test_register_scoped_agent_for_future_multi_agent() -> None:
    agents = AgentRegistry()
    agents.register(
        "editor-zh",
        display_name="Editor 张三",
        allowed_sections=("0", "1"),
        read_only=True,
        forbidden_operations=frozenset({"clearDocument"}),
    )
    identity = agents.require("editor-zh")
    assert identity.display_name == "Editor 张三"
    assert identity.scope.allows_section(0)
    assert not identity.scope.allows_section(2)
    assert identity.scope.forbids("clearDocument")
    assert not identity.scope.forbids("insertText")
    assert list(agents.list()) == [identity]


def test_get_permissions_returns_full_governance_table() -> None:
    table = get_permissions(default_agent())
    assert table["agentId"] == "AI-Writer"
    assert table["readOnly"] is False
    ops = {row["op"]: row for row in table["permissions"]}

    # read tools allowed
    assert ops["getOutline"]["allow"] is True
    assert ops["getOutline"]["risk"] == "low"
    # write tools allowed with confirmation thresholds
    assert ops["insertText"]["allow"] is True
    assert ops["insertText"]["requires_confirmation"] is False
    assert ops["refactorSection"]["allow"] is True
    assert ops["refactorSection"]["confirms_required"] == 2
    # destructive ops blocked for the agent (acceptance: getPermissions works)
    assert ops["clearDocument"]["allow"] is False
    assert "forbidden" in ops["clearDocument"]["reason"]
    assert ops["runMacro"]["allow"] is False
    assert ops["encryptDocument"]["allow"] is False
    assert ops["signDocument"]["allow"] is False
    # MVP-unavailable tools blocked
    assert ops["renderTile"]["allow"] is False
    # confirmation helpers allowed
    assert ops["confirmOp"]["allow"] is True


def test_read_only_agent_denies_writes_but_keeps_reads() -> None:
    agent = default_agent()
    agent = agent.__class__(
        agent_id=agent.agent_id,
        display_name=agent.display_name,
        scope=AgentScope(allowed_sections=("*",), read_only=True),
    )
    table = get_permissions(agent)
    ops = {row["op"]: row for row in table["permissions"]}
    assert ops["getOutline"]["allow"] is True
    assert ops["insertText"]["allow"] is False
    assert "read-only" in ops["insertText"]["reason"]
    assert ops["replaceRange"]["allow"] is False
