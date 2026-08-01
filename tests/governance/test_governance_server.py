"""Server-level governance tests: getPermissions tool, guardrails on the write
path, and the doc 8.4 audit record after a round commit.

Covers the acceptance criteria end to end through the MCP tool surface:

- ``getPermissions {agentId}`` works and reports the doc 4.2 table;
- a forbidden op (``clearDocument``) is blocked before touching the document;
- the write rate limit triggers after the threshold;
- a protected-region overlap is denied;
- every round commit lands in the audit log with all 8.4 fields.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP
from tests.governance.conftest import FakeGovCo, FakeGovDocument

from coffice.governance import (
    AI_WRITER_ID,
    AUDIT_FIELDS,
    AgentRegistry,
    ProtectedRegion,
    ProtectedRegionGuard,
    RateLimiter,
)
from coffice.governance.audit import AuditLog
from coffice.mcp import GOVERNANCE_TOOL_NAMES, create_server
from coffice.mcp.middleware import RoundTracker
from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry


def list_tools(server: FastMCP) -> list[dict[str, Any]]:
    return [
        {"name": tool.name, "description": tool.description, "inputSchema": tool.inputSchema}
        for tool in asyncio.run(server.list_tools())
    ]


def call_tool(server: FastMCP, name: str, arguments: dict[str, Any]) -> Any:
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    return result


def build_server(
    registry: DocumentRegistry,
    co_client: FakeGovCo,
    tracker: RoundTracker,
    *,
    agents: AgentRegistry | None = None,
    rate_limiter: RateLimiter | None = None,
    protected_regions: ProtectedRegionGuard | None = None,
    audit_log: AuditLog | None = None,
) -> FastMCP:
    return create_server(
        registry=registry,
        co_client=co_client,
        round_tracker=tracker,
        agent_registry=agents,
        rate_limiter=rate_limiter,
        protected_regions=protected_regions,
        audit_log=audit_log,
    )


# --------------------------------------------------------------------------
# getPermissions tool (acceptance: getPermissions works)
# --------------------------------------------------------------------------


def test_get_permissions_tool_registered_and_callable(
    gov_registry: DocumentRegistry,
    gov_co: FakeGovCo,
    gov_round_tracker: RoundTracker,
) -> None:
    server = build_server(gov_registry, gov_co, gov_round_tracker)
    tools = {t["name"]: t for t in list_tools(server)}
    assert GOVERNANCE_TOOL_NAMES == ("getPermissions",)
    assert "getPermissions" in tools
    assert "agentId" in tools["getPermissions"]["inputSchema"]["properties"]

    result = call_tool(server, "getPermissions", {"agentId": AI_WRITER_ID})
    assert result["ok"] is True
    assert result["agentId"] == "AI-Writer"
    ops = {row["op"]: row for row in result["permissions"]}
    assert ops["clearDocument"]["allow"] is False
    assert ops["insertText"]["allow"] is True
    # defaults to the singleton agent when agentId is omitted
    defaulted = call_tool(server, "getPermissions", {})
    assert defaulted["agentId"] == "AI-Writer"


def test_get_permissions_unknown_agent_returns_error(
    gov_registry: DocumentRegistry,
    gov_co: FakeGovCo,
    gov_round_tracker: RoundTracker,
) -> None:
    server = build_server(gov_registry, gov_co, gov_round_tracker)
    result = call_tool(server, "getPermissions", {"agentId": "ghost"})
    assert result["ok"] is False
    assert "unknown agent" in result["error"]


# --------------------------------------------------------------------------
# guardrails on the write path
# --------------------------------------------------------------------------


def test_forbidden_op_blocked_before_any_document_change(
    gov_registry: DocumentRegistry,
    gov_co: FakeGovCo,
    gov_round_tracker: RoundTracker,
    gov_doc: FakeGovDocument,
) -> None:
    """An op forbidden by the agent scope is blocked on the server write path
    before the write-tools middleware (and therefore UNO) is reached.

    ``clearDocument`` is the canonical destructive op (doc 8.3) but is not one
    of the six registered write tools, so we prove the wiring with
    ``refactorSection``: scoped-forbidden for this agent, it must return
    ``blocked`` instead of the usual ``needs_confirmation``.
    """
    agents = AgentRegistry()
    agents.register(
        "AI-Writer", forbidden_operations=frozenset({"refactorSection"})
    )
    server = build_server(gov_registry, gov_co, gov_round_tracker, agents=agents)

    result = call_tool(
        server, "refactorSection", {"sectionId": 0, "instruction": "new"}
    )
    assert result["status"] == "blocked"
    assert result["ok"] is False
    assert "refactorSection" in result["error"]
    # the document was never touched: no redline, no save, no co commit
    assert gov_doc.calls == []
    assert gov_co.calls == []


def test_write_rate_limit_triggers_after_threshold(
    gov_registry: DocumentRegistry,
    gov_co: FakeGovCo,
    gov_round_tracker: RoundTracker,
    gov_limiter: RateLimiter,
) -> None:
    server = build_server(
        gov_registry, gov_co, gov_round_tracker, rate_limiter=gov_limiter
    )
    for _ in range(gov_limiter.limit):
        result = call_tool(server, "insertText", {"pos": "end", "text": "x"})
        assert result["ok"] is True
    # threshold crossed: the next write is rejected before any UNO call
    blocked = call_tool(server, "insertText", {"pos": "end", "text": "y"})
    assert blocked["status"] == "rate_limited"
    assert "rate limit" in blocked["error"]
    assert gov_doc_final_text(gov_registry) == "x" * gov_limiter.limit


def test_protected_region_denies_overlapping_edit(
    gov_registry: DocumentRegistry,
    gov_co: FakeGovCo,
    gov_round_tracker: RoundTracker,
    gov_doc: FakeGovDocument,
) -> None:
    gov_doc._text = "x" * 200  # noqa: SLF001
    regions = ProtectedRegionGuard([ProtectedRegion(label="Signature", start=90, end=110)])
    server = build_server(
        gov_registry, gov_co, gov_round_tracker, protected_regions=regions
    )
    blocked = call_tool(
        server,
        "replaceRange",
        {"range": {"start": 95, "end": 105}, "text": "y"},
    )
    assert blocked["status"] == "blocked"
    assert "Signature" in blocked["error"]
    assert gov_doc._text == "x" * 200  # noqa: SLF001 - untouched
    # an edit outside the region still works
    ok = call_tool(
        server, "replaceRange", {"range": {"start": 0, "end": 5}, "text": "z"}
    )
    assert ok["ok"] is True


# --------------------------------------------------------------------------
# audit log after a round commit (acceptance: entries contain all 8.4 fields)
# --------------------------------------------------------------------------


def test_round_commit_writes_full_audit_record(
    gov_registry: DocumentRegistry,
    gov_co: FakeGovCo,
    gov_round_tracker: RoundTracker,
    gov_audit: AuditLog,
    gov_doc: FakeGovDocument,
) -> None:
    gov_doc._text = "Hello"  # noqa: SLF001
    # building the server wires the round-snapshot hook + audit callback
    build_server(gov_registry, gov_co, gov_round_tracker, audit_log=gov_audit)
    round_ = gov_round_tracker.start_round("AI-Writer", "alice")
    assert round_ is not None

    entries = gov_audit.read()
    assert len(entries) == 1
    entry = entries[0]
    assert set(entry) == set(AUDIT_FIELDS)
    assert entry["author"] == "AI-Writer"
    assert entry["human_operator"] == "alice"
    assert entry["message"] == f"pre: {round_.round_id}"
    assert entry["branch"] == "main"
    assert entry["files_changed"] == ["word/document.xml"]
    assert entry["parent"] is None  # first commit on this doc
    assert entry["hash"]

    # a second round produces a second record whose parent is the first hash
    gov_round_tracker.start_round("AI-Writer", "bob")
    entries = gov_audit.read()
    assert len(entries) == 2
    assert entries[1]["parent"] == entries[0]["hash"]
    assert entries[1]["human_operator"] == "bob"


def gov_doc_final_text(registry: DocumentRegistry) -> str:
    return registry.get(DEFAULT_DOC_ID).get_text()
