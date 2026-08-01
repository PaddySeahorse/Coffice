"""Agent identity registry (planning doc 8.1).

Every operation an LLM performs is attributed to a registered *agent* with a
display name and a :class:`AgentScope`: which sections it may touch, whether
it is read-only, and which operations are forbidden for it (doc 8.3
destructive rows, e.g. ``clearDocument``). The MCP server exposes the scope as
the ``getPermissions`` tool (doc 4.2 governance table) and the write guard
enforces it before any UNO call.

MVP ships exactly one agent, ``"AI-Writer"`` (the singleton
:func:`default_agent`). The registry and scoping API are built for future
multi-agent setups (planning doc 14.4 scenario B is explicitly NOT in the
MVP); registering more agents is a one-line call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: id of the singleton agent the Route 1 MVP ships with.
AI_WRITER_ID = "AI-Writer"

#: doc 8.3 destructive operations the MVP agent is never allowed to perform.
#: Encryption/signing/macro are globally forbidden (classification table);
#: ``clearDocument`` is additionally forbidden *for the agent* -- clearing the
#: whole document is a human-only action even though it is technically
#: confirmable (HIGH, double confirm) for interactive users.
DEFAULT_FORBIDDEN_OPERATIONS = frozenset(
    {"clearDocument", "encryptDocument", "signDocument", "runMacro"}
)


class UnknownAgentError(KeyError):
    """Raised when an operation references an agent that is not registered."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"unknown agent {agent_id!r}: register it first")


@dataclass(frozen=True)
class AgentScope:
    """What a single agent may do in a document (doc 8.1 scoping)."""

    #: section ids (getOutline indices) the agent may edit; ``("*",)`` (the
    #: default) means the whole document.
    allowed_sections: tuple[str, ...] = ("*",)
    #: read-only agents may call get* tools but never a write tool.
    read_only: bool = False
    #: operation names blocked for this agent before any UNO call (doc 8.3).
    forbidden_operations: frozenset[str] = field(default_factory=frozenset)

    def allows_section(self, section: int | str) -> bool:
        """True when the agent may edit ``section`` under this scope."""
        if "*" in self.allowed_sections:
            return True
        return str(section) in self.allowed_sections

    def forbids(self, op: str) -> bool:
        """True when ``op`` is in the agent's forbidden-operations list."""
        return op in self.forbidden_operations

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowedSections": list(self.allowed_sections),
            "readOnly": self.read_only,
            "forbiddenOperations": sorted(self.forbidden_operations),
        }


@dataclass(frozen=True)
class AgentIdentity:
    """A registered agent: id, display name, and scope."""

    agent_id: str
    display_name: str
    scope: AgentScope = field(default_factory=AgentScope)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agentId": self.agent_id,
            "displayName": self.display_name,
            "scope": self.scope.to_dict(),
        }


class AgentRegistry:
    """Registered agents keyed by ``agent_id``.

    The registry is deliberately small: register/get/require/list. It is
    thread-unsafe by design (the MCP server is single-threaded); a future
    multi-agent milestone can add persistence.
    """

    def __init__(self, agents: dict[str, AgentIdentity] | None = None) -> None:
        self._agents: dict[str, AgentIdentity] = dict(agents or {})

    def register(
        self,
        agent_id: str,
        display_name: str | None = None,
        *,
        allowed_sections: tuple[str, ...] = ("*",),
        read_only: bool = False,
        forbidden_operations: frozenset[str] = frozenset(),
    ) -> AgentIdentity:
        """Register (or overwrite) an agent; returns its identity.

        ``display_name`` defaults to ``agent_id``.
        """
        identity = AgentIdentity(
            agent_id=agent_id,
            display_name=display_name or agent_id,
            scope=AgentScope(
                allowed_sections=allowed_sections,
                read_only=read_only,
                forbidden_operations=frozenset(forbidden_operations),
            ),
        )
        self._agents[agent_id] = identity
        return identity

    def register_identity(self, identity: AgentIdentity) -> AgentIdentity:
        """Register a pre-built :class:`AgentIdentity` (e.g. the singleton)."""
        self._agents[identity.agent_id] = identity
        return identity

    def get(self, agent_id: str) -> AgentIdentity | None:
        """Return the identity or ``None`` when not registered."""
        return self._agents.get(agent_id)

    def require(self, agent_id: str) -> AgentIdentity:
        """Return the identity, raising :class:`UnknownAgentError` otherwise."""
        identity = self._agents.get(agent_id)
        if identity is None:
            raise UnknownAgentError(agent_id)
        return identity

    def list(self) -> list[AgentIdentity]:
        """Return all registered identities (sorted by id for stability)."""
        return [self._agents[key] for key in sorted(self._agents)]

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._agents

    def __len__(self) -> int:
        return len(self._agents)


def default_agent() -> AgentIdentity:
    """The Route 1 MVP singleton: ``AI-Writer`` with whole-doc, write scope."""
    return AgentIdentity(
        agent_id=AI_WRITER_ID,
        display_name=AI_WRITER_ID,
        scope=AgentScope(
            allowed_sections=("*",),
            read_only=False,
            forbidden_operations=DEFAULT_FORBIDDEN_OPERATIONS,
        ),
    )


def default_registry() -> AgentRegistry:
    """A registry pre-populated with the singleton ``AI-Writer`` agent."""
    registry = AgentRegistry()
    registry.register_identity(default_agent())
    return registry


__all__ = [
    "AI_WRITER_ID",
    "DEFAULT_FORBIDDEN_OPERATIONS",
    "AgentIdentity",
    "AgentRegistry",
    "AgentScope",
    "UnknownAgentError",
    "default_agent",
    "default_registry",
]
