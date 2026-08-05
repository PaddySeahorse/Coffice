"""Unit tests for the MCP server: tool discovery, schemas, and read tool
result shaping against an in-memory fake document registry.

The acceptance criteria ("an MCP client can list the 7 read tools and invoke
getOutline/getStyles against an opened doc or a mocked registry") are covered
in-process via ``FastMCP.list_tools()`` / ``FastMCP.call_tool()`` here, plus a
real stdio-client round trip in :mod:`tests.mcp.test_stdio_client`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from tests.mcp.conftest import FakeCoClient, FakeDocument, FakeProjector

from coffice.mcp import READ_TOOL_NAMES, create_server
from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry
from coffice.mcp.tools_read import RENDER_TILE_MVP_ERROR


def list_tools(server: FastMCP) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.inputSchema,
        }
        for tool in asyncio.run(server.list_tools())
    ]


def call_tool(server: FastMCP, name: str, arguments: dict[str, Any]) -> Any:
    result = asyncio.run(server.call_tool(name, arguments))
    # FastMCP.call_tool returns (content_blocks, structured_content); the raw
    # JSON-serializable result is the second element for dict-returning tools.
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    return result


@pytest.fixture()
def server(
    fake_registry: DocumentRegistry,
    fake_co_client: FakeCoClient,
    fake_projector: FakeProjector,
) -> FastMCP:
    return create_server(
        registry=fake_registry, co_client=fake_co_client, projector=fake_projector
    )


# --------------------------------------------------------------------------
# discovery (doc 4.1: tools are self-describing)
# --------------------------------------------------------------------------


def test_server_lists_7_read_tools(server: FastMCP) -> None:
    tools = list_tools(server)
    names = [tool["name"] for tool in tools]
    assert names[:7] == list(READ_TOOL_NAMES)
    assert "renderTile" in names[7:]


def test_tools_carry_description_and_input_schema(server: FastMCP) -> None:
    by_name = {tool["name"]: tool for tool in list_tools(server)}
    for name in READ_TOOL_NAMES:
        tool = by_name[name]
        assert tool["description"], f"{name} missing description"
        assert "type" in tool["inputSchema"]
        assert "properties" in tool["inputSchema"], f"{name} missing inputSchema"


def test_get_selection_schema_exposes_range(server: FastMCP) -> None:
    tool = {t["name"]: t for t in list_tools(server)}["getSelection"]
    props = tool["inputSchema"]["properties"]
    assert "docId" in props
    range_schema = props["range"]
    assert range_schema.get("type") == "object" or any(
        variant.get("type") == "object" for variant in range_schema.get("anyOf", [])
    )


def test_get_diff_schema_requires_hashes(server: FastMCP) -> None:
    tool = {t["name"]: t for t in list_tools(server)}["getDiff"]
    required = tool["inputSchema"].get("required", [])
    assert "h1" in required and "h2" in required


def test_render_tile_is_registered_but_mvp_error(server: FastMCP) -> None:
    tool = {t["name"]: t for t in list_tools(server)}["renderTile"]
    assert "Phase 2" in tool["description"]


# --------------------------------------------------------------------------
# read tool results (mocked registry)
# --------------------------------------------------------------------------


def test_get_outline_shapes_result(server: FastMCP) -> None:
    result = call_tool(server, "getOutline", {"docId": DEFAULT_DOC_ID})
    assert result["docId"] == DEFAULT_DOC_ID
    assert result["outline"][0]["text"] == "Report"
    assert result["outline"][0]["level"] == 1


def test_get_outline_defaults_to_default_doc(server: FastMCP) -> None:
    result = call_tool(server, "getOutline", {})
    assert result["docId"] == DEFAULT_DOC_ID


def test_get_selection_whole_document(server: FastMCP) -> None:
    result = call_tool(server, "getSelection", {"docId": DEFAULT_DOC_ID})
    assert result["selection"]["text"] == "Report"
    assert result["selection"]["paragraph_style"] == "Heading 1"


def test_get_selection_with_range(server: FastMCP) -> None:
    result = call_tool(
        server, "getSelection", {"docId": DEFAULT_DOC_ID, "range": {"start": 0, "end": 3}}
    )
    assert result["selection"]["start"] == 0
    assert result["selection"]["end"] == 3


def test_get_styles_shapes_result(server: FastMCP) -> None:
    result = call_tool(server, "getStyles", {"docId": DEFAULT_DOC_ID})
    assert "Heading 1" in result["styles"]["paragraph"]
    assert "Emphasis" in result["styles"]["character"]


def test_get_tables_shapes_result(server: FastMCP) -> None:
    result = call_tool(server, "getTables", {"docId": DEFAULT_DOC_ID})
    table = result["tables"][0]
    assert table["name"] == "Table1"
    assert table["cells"][0] == ["a", "b"]


def test_get_redlines_shapes_result(server: FastMCP) -> None:
    result = call_tool(server, "getRedlines", {"docId": DEFAULT_DOC_ID})
    redline = result["redlines"][0]
    assert redline["type"] == "insert"
    assert redline["author"] == "AI-Writer"
    assert isinstance(redline["id"], str)
    assert redline["id"]  # non-empty sha256 fingerprint
    assert "baseline_range" in redline
    assert "current_range" in redline


def test_accept_redline_requires_redline_id(server: FastMCP) -> None:
    with pytest.raises(Exception, match="redlineId|Field required|validation error"):
        call_tool(server, "acceptRedline", {"docId": DEFAULT_DOC_ID})


def test_accept_redline_records_decision(
    server: FastMCP, fake_projector: FakeProjector
) -> None:
    redlines = call_tool(server, "getRedlines", {"docId": DEFAULT_DOC_ID})["redlines"]
    rid = redlines[0]["id"]
    result = call_tool(server, "acceptRedline", {"redlineId": rid})
    assert result["ok"] is True
    assert result["action"] == "accept"
    assert result["redline_id"] == rid


def test_reject_redline_rebuilds_text_and_commits(server: FastMCP) -> None:
    result = call_tool(server, "rejectRedline", {"redlineId": "h0_insert_aabbcc"})
    assert result["ok"] is True
    assert result["action"] == "reject"
    assert result["commit_hash"]  # co commit recorded the rollback


# --------------------------------------------------------------------------
# version tools via the fake co client
# --------------------------------------------------------------------------


def test_get_history_returns_commit_fields(server: FastMCP) -> None:
    result = call_tool(server, "getHistory", {"docId": DEFAULT_DOC_ID})
    commits = result["commits"]
    assert len(commits) == 2
    for commit in commits:
        assert {"hash", "author", "message", "timestamp"} <= set(commit)


def test_get_history_honors_limit(server: FastMCP) -> None:
    result = call_tool(server, "getHistory", {"docId": DEFAULT_DOC_ID, "limit": 1})
    assert len(result["commits"]) == 1
    assert result["commits"][0]["message"] == "pre: round 1"


def test_get_diff_returns_paths(server: FastMCP) -> None:
    result = call_tool(server, "getDiff", {"docId": DEFAULT_DOC_ID, "h1": "abc123", "h2": "def456"})
    assert result["diff"] == [{"status": "M", "path": "word/document.xml"}]


def test_get_diff_requires_both_hashes(server: FastMCP) -> None:
    """The schema makes h1/h2 mandatory, so an incomplete call is rejected."""
    with pytest.raises(Exception, match="h2|Field required|validation error"):
        call_tool(server, "getDiff", {"docId": DEFAULT_DOC_ID, "h1": "abc123"})


# --------------------------------------------------------------------------
# error paths
# --------------------------------------------------------------------------


def test_render_tile_returns_structured_mvp_error(server: FastMCP) -> None:
    result = call_tool(server, "renderTile", {"docId": DEFAULT_DOC_ID})
    assert result == {"docId": DEFAULT_DOC_ID, **RENDER_TILE_MVP_ERROR}


def test_tool_failure_returns_structured_error(fake_document: FakeDocument) -> None:
    """If opening the document fails, the tool returns an error dict."""
    registry = DocumentRegistry(
        doc_path="/nope.docx",
        factory=lambda doc_id, path: (_ for _ in ()).throw(RuntimeError("no LO")),
    )
    server = create_server(registry=registry, co_client=FakeCoClient())
    result = call_tool(server, "getOutline", {"docId": DEFAULT_DOC_ID})
    assert "error" in result
    assert "no LO" in result["error"]


def test_get_history_error_when_co_fails(fake_registry: DocumentRegistry) -> None:
    server = create_server(registry=fake_registry, co_client=FakeCoClient(raise_error=True))
    result = call_tool(server, "getHistory", {"docId": DEFAULT_DOC_ID})
    assert "error" in result


def test_server_info_reports_name_and_version(fake_registry: DocumentRegistry) -> None:
    server = create_server(registry=fake_registry, co_client=FakeCoClient())
    assert server._mcp_server.name == "coffice"
    assert server._mcp_server.version == "0.1.0"
