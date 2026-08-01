"""Governance: agent identity, permissions, guardrails, audit log (planning doc ch. 8).

The control plane that makes symmetric access safe: every agent is a
registered identity with a scope, every write is filtered through guardrails
(forbidden ops, rate limits, protected regions), and every round commit lands
in an append-only JSONL audit log.

Public API::

    from coffice.governance import (
        AgentIdentity, AgentRegistry, AgentScope, WriteGuard, AuditLog,
        default_registry, get_permissions,
    )

    agents = default_registry()                 # singleton AI-Writer
    permissions = get_permissions(agents.require("AI-Writer"))
    guard = WriteGuard(agents=agents, rate_limiter=RateLimiter(limit=30))
    blocked = guard.check("AI-Writer", "clearDocument", {}, doc, "default")

The classification table (doc 8.3) is owned here (``classification``) and
re-exported by the write-tools middleware.
"""

from coffice.governance.agents import (
    AI_WRITER_ID,
    DEFAULT_FORBIDDEN_OPERATIONS,
    AgentIdentity,
    AgentRegistry,
    AgentScope,
    UnknownAgentError,
    default_agent,
    default_registry,
)
from coffice.governance.audit import (
    AUDIT_FIELDS,
    DEFAULT_BRANCH,
    AuditLog,
    round_audit_callback,
)
from coffice.governance.classification import (
    DELETE_MEDIUM_THRESHOLD,
    FORBIDDEN_OPS,
    RiskLevel,
    blocked_op_error,
    classify,
    confirms_required,
    requires_confirmation,
)
from coffice.governance.guardrails import (
    GuardrailError,
    ProtectedRegion,
    ProtectedRegionError,
    ProtectedRegionGuard,
    RateLimiter,
    RateLimitError,
    ReadOnlyAgentError,
    WriteGuard,
    parse_protected_regions,
)
from coffice.governance.permissions import get_permissions

__all__ = [
    "AI_WRITER_ID",
    "AUDIT_FIELDS",
    "DEFAULT_BRANCH",
    "DEFAULT_FORBIDDEN_OPERATIONS",
    "DELETE_MEDIUM_THRESHOLD",
    "FORBIDDEN_OPS",
    "AgentIdentity",
    "AgentRegistry",
    "AgentScope",
    "AuditLog",
    "GuardrailError",
    "ProtectedRegion",
    "ProtectedRegionError",
    "ProtectedRegionGuard",
    "RateLimitError",
    "RateLimiter",
    "ReadOnlyAgentError",
    "RiskLevel",
    "UnknownAgentError",
    "WriteGuard",
    "blocked_op_error",
    "classify",
    "confirms_required",
    "default_agent",
    "default_registry",
    "get_permissions",
    "parse_protected_regions",
    "requires_confirmation",
    "round_audit_callback",
]
