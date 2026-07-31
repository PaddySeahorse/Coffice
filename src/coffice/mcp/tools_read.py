"""Read-side MCP tool implementations (planning doc 4.2 read table).

Each function here is a plain, testable handler taking the document registry
(and co client where version data is needed); the FastMCP layer in
``coffice.mcp.server`` wraps them into registered tools whose self-describing
JSON-Schema inputs come from the closure signatures. Every handler returns a
JSON-serializable dict, so agent tool output is stable regardless of transport.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry
from coffice.versioning.co_client import CoClient

#: Structured error returned by tools that are out of MVP scope.
RENDER_TILE_MVP_ERROR = {
    "error": "renderTile requires Phase 2 (LOKit tiled rendering); not supported in MVP"
}

_NO_PATH_ERROR = (
    "no on-disk document path for this docId; history needs a saved document "
    "(set COFFICE_DOC_PATH or open the doc with an explicit path)"
)


def get_outline(
    registry: DocumentRegistry, doc_id: str = DEFAULT_DOC_ID
) -> dict[str, Any]:
    """Return the document outline (headings with character offsets)."""
    doc = registry.get(doc_id)
    return {"docId": doc_id, "outline": doc.get_outline()}


def get_selection(
    registry: DocumentRegistry,
    doc_id: str = DEFAULT_DOC_ID,
    range: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Return text + style info for ``range`` (defaults to the whole doc)."""
    doc = registry.get(doc_id)
    return {"docId": doc_id, "selection": doc.get_selection(range)}


def get_styles(
    registry: DocumentRegistry, doc_id: str = DEFAULT_DOC_ID
) -> dict[str, Any]:
    """Return the document's paragraph and character style names."""
    doc = registry.get(doc_id)
    return {"docId": doc_id, "styles": doc.get_styles()}


def get_tables(
    registry: DocumentRegistry, doc_id: str = DEFAULT_DOC_ID
) -> dict[str, Any]:
    """Return all tables with dimensions and cell contents."""
    doc = registry.get(doc_id)
    return {"docId": doc_id, "tables": doc.get_tables()}


def get_redlines(
    registry: DocumentRegistry, doc_id: str = DEFAULT_DOC_ID
) -> dict[str, Any]:
    """Return the tracked-changes (redline) list of the document."""
    doc = registry.get(doc_id)
    return {"docId": doc_id, "redlines": doc.get_redlines()}


def get_history(
    registry: DocumentRegistry,
    doc_id: str,
    co_client: CoClient,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return the co version history: hash/author/message/timestamp per commit."""
    path = registry.path(doc_id)
    if not path:
        return {"docId": doc_id, "error": _NO_PATH_ERROR}
    try:
        commits = co_client.log(path, limit=limit)
    except Exception as exc:  # co binary unavailable / command failed
        return {"docId": doc_id, "error": str(exc)}
    return {"docId": doc_id, "commits": [commit.to_dict() for commit in commits]}


def get_diff(
    registry: DocumentRegistry,
    doc_id: str,
    co_client: CoClient,
    h1: str = "",
    h2: str = "",
) -> dict[str, Any]:
    """Return the diff between two commit hashes (changed zip-internal paths)."""
    path = registry.path(doc_id)
    if not path:
        return {"docId": doc_id, "error": _NO_PATH_ERROR}
    if not h1 or not h2:
        return {
            "docId": doc_id,
            "error": "getDiff requires both h1 and h2 commit hashes",
        }
    try:
        entries = co_client.diff(path, h1, h2)
    except Exception as exc:
        return {"docId": doc_id, "error": str(exc)}
    return {"docId": doc_id, "diff": [entry.to_dict() for entry in entries]}


def render_tile(
    doc_id: str = DEFAULT_DOC_ID,
    page: int = 1,
    x: int = 0,
    y: int = 0,
    width: int = 512,
    height: int = 512,
) -> dict[str, Any]:
    """AI vision tile rendering (Phase 2); explicitly out of MVP scope."""
    return {"docId": doc_id, **RENDER_TILE_MVP_ERROR}


__all__ = [
    "RENDER_TILE_MVP_ERROR",
    "get_outline",
    "get_selection",
    "get_styles",
    "get_tables",
    "get_redlines",
    "get_history",
    "get_diff",
    "render_tile",
]
