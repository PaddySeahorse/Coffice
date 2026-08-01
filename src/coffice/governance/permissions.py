"""Agent permission lists (planning doc 4.2 governance table).

``get_permissions(agent)`` renders an agent's :class:`AgentScope` as the
machine-readable table the ``getPermissions`` MCP tool returns: one row per
known operation with its allow/deny decision, doc 8.3 risk level, and the
confirmation threshold the write-tools middleware will enforce.

Decision rules (in order, first match wins):

1. read-only scope -> every write operation is denied;
2. operation in the agent's ``forbidden_operations`` (e.g. ``clearDocument``)
   or in the global doc 8.3 forbidden set -> denied;
3. otherwise allowed, with the doc 8.3 risk + confirmation requirement.
"""

from __future__ import annotations

from typing import Any

from coffice.governance.agents import AgentIdentity
from coffice.governance.classification import (
    FORBIDDEN_OPS,
    classify,
    confirms_required,
)

#: read tools every agent may call (they never mutate the document).
READ_OPS = (
    "getOutline",
    "getSelection",
    "getStyles",
    "getTables",
    "getRedlines",
    "getHistory",
    "getDiff",
    "getPermissions",
)

#: write tools (planning doc 4.2 write table) subject to scope + guardrails.
WRITE_OPS = (
    "insertText",
    "replaceRange",
    "applyStyle",
    "generateTable",
    "refactorSection",
    "setPageLayout",
)

#: confirmation-flow tools; confirmOp/rejectOp resolve pending ops only.
CONFIRM_OPS = ("confirmOp", "rejectOp")

#: doc 8.3 destructive operations surfaced in the table. None of these are
#: registered tools in the MVP; they are listed so the agent can see (via
#: getPermissions) that e.g. ``clearDocument`` is blocked for it.
DESTRUCTIVE_OPS = ("clearDocument", "encryptDocument", "signDocument", "runMacro")

#: tools that are intentionally unavailable in the Route 1 MVP (doc 10.2).
UNAVAILABLE_OPS = ("renderTile",)

#: the full operation surface the governance table reports on.
ALL_OPS = (
    *READ_OPS,
    *WRITE_OPS,
    *CONFIRM_OPS,
    *DESTRUCTIVE_OPS,
    *UNAVAILABLE_OPS,
)


def _decision(op: str, agent: AgentIdentity) -> dict[str, Any]:
    """One governance-table row for ``op`` under ``agent``'s scope."""
    scope = agent.scope
    is_write = op in WRITE_OPS

    if scope.read_only and is_write:
        return {
            "op": op,
            "allow": False,
            "reason": "agent is read-only (doc 8.1)",
        }

    if scope.forbids(op) or op in FORBIDDEN_OPS:
        return {
            "op": op,
            "allow": False,
            "reason": f"forbidden operation for agent {agent.agent_id!r} (doc 8.3)",
        }

    if op in UNAVAILABLE_OPS:
        return {
            "op": op,
            "allow": False,
            "reason": "not available in the Route 1 MVP (doc 10.2)",
        }

    risk = classify(op)
    return {
        "op": op,
        "allow": True,
        "risk": risk.value,
        "requires_confirmation": confirms_required(risk) > 0,
        "confirms_required": confirms_required(risk),
    }


def get_permissions(agent: AgentIdentity) -> dict[str, Any]:
    """Render ``agent``'s permission list (doc 4.2 governance table).

    Returns a JSON-ready dict with the agent header plus one ``permissions``
    row per known operation. Unknown operations are not listed; the
    classification table treats them as LOW risk so the agent is never
    hard-blocked by a classification gap.
    """
    return {
        "agentId": agent.agent_id,
        "displayName": agent.display_name,
        "readOnly": agent.scope.read_only,
        "allowedSections": list(agent.scope.allowed_sections),
        "permissions": [_decision(op, agent) for op in ALL_OPS],
    }


def get_permissions_tool(
    agents: Any, agent_id: str
) -> dict[str, Any]:
    """MCP tool handler for ``getPermissions {agentId}`` (doc 4.2).

    Returns the governance table for a registered agent, or a structured
    error for an unknown agent id -- the agent loop should resolve this
    before calling any write tool.
    """
    from coffice.governance.agents import UnknownAgentError

    try:
        agent = agents.require(agent_id)
    except UnknownAgentError as exc:
        return {"agentId": agent_id, "ok": False, "error": str(exc)}
    return {"ok": True, **get_permissions(agent)}


__all__ = [
    "ALL_OPS",
    "CONFIRM_OPS",
    "DESTRUCTIVE_OPS",
    "READ_OPS",
    "UNAVAILABLE_OPS",
    "WRITE_OPS",
    "get_permissions",
    "get_permissions_tool",
]
