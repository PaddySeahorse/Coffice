"""Version-control MCP tools (planning doc 14.3) + export/import (ADR-005).

The seven tools here wire the ``co`` CLI capabilities into the agent tool
surface (doc 14.3 table): ``snapshot``/``rollback``/``branch``/``merge``/
``tag`` map 1:1 onto ``co commit``/``co checkout``/``co branch``/``co
merge``/``co tag``, and ``exportDoc``/``importBundle`` implement the
ADR-005 export/import flow (see :mod:`coffice.versioning.export`).

Handlers share the same ``(ctx, doc_id, *args) -> dict`` shape as the write
tools, so ``coffice.mcp.server`` can wrap them with the same guarded/governed
callers. Every handler returns a JSON-serializable dict; ``CoError``
failures are shaped as ``{ok: False, status: "error", error}`` so the agent
always gets a readable result.

Export semantics (ADR-005)
--------------------------
- ``exportDoc`` defaults to a PURE export (no ``.co/`` embedded) -- 100%
  compatible with any editor; history lives in the ``<file>.co-bundle``
  companion.
- ``includeCo: true`` is allowed but the result carries the ADR-005 warning
  string (``coffice.versioning.INCLUDE_CO_WARNING``), surfaced by the UI as a
  warning dialog.
- ``includeBundle: true`` (default) writes the ``.co-bundle`` companion;
  ``package: true`` additionally zips document + bundle into
  ``<file>.coffice.zip``.
- ``importBundle`` restores history from a bundle onto a document (doc 14.6
  scenario B) and appends the current state as a new commit.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from coffice.mcp.registry import DEFAULT_DOC_ID
from coffice.mcp.tools_write import WriteContext
from coffice.versioning.co_client import INCLUDE_CO_WARNING, CoError
from coffice.versioning.export import export_document
from coffice.versioning.export import import_bundle as import_bundle_flow

logger = logging.getLogger(__name__)

_NO_PATH_ERROR = (
    "no on-disk document path for this docId; version tools need a saved "
    "document (set COFFICE_DOC_PATH or open the doc with an explicit path)"
)

_MISSING_ARG_ERROR = "requires both docId and {arg}"


def _path_of(ctx: WriteContext, doc_id: str) -> str | None:
    """Return the on-disk path of ``doc_id``, or None when unsaved."""
    return ctx.registry.path(doc_id)


def _no_path(tool: str, doc_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "docId": doc_id,
        "status": "error",
        "error": _NO_PATH_ERROR,
    }


# ============================================================================
# doc 14.3 version tools (co commit/checkout/branch/merge/tag)
# ============================================================================


def snapshot(
    ctx: WriteContext,
    doc_id: str = DEFAULT_DOC_ID,
    label: str = "",
) -> dict[str, Any]:
    """Create a version snapshot of the current document state.

    Maps to ``co commit -m <label>``; returns the new commit hash. This is
    the manual snapshot counterpart to the automatic pre-round snapshot the
    write-tools middleware takes (doc 4.1).
    """
    path = _path_of(ctx, doc_id)
    if not path:
        return _no_path("snapshot", doc_id)
    try:
        hash_ = ctx.co_client.commit(
            path, label, author=ctx.author, human_operator=ctx.human_operator
        )
    except CoError as exc:
        logger.warning("snapshot tool failed for docId=%r: %s", doc_id, exc)
        return {
            "ok": False,
            "tool": "snapshot",
            "docId": doc_id,
            "status": "error",
            "error": str(exc),
        }
    ctx.record_audit(
        "version_snapshot", tool="snapshot", doc_id=doc_id, hash=hash_, label=label
    )
    return {
        "ok": True,
        "tool": "snapshot",
        "docId": doc_id,
        "hash": hash_,
        "label": label,
    }


def rollback(
    ctx: WriteContext,
    doc_id: str = DEFAULT_DOC_ID,
    hash: str = "",
) -> dict[str, Any]:
    """Restore the document contents to a previous commit hash.

    Maps to ``co checkout <hash>``; the document file on disk is replaced
    with the given snapshot's contents.
    """
    if not hash:
        return {
            "ok": False,
            "tool": "rollback",
            "docId": doc_id,
            "status": "error",
            "error": _MISSING_ARG_ERROR.format(arg="hash"),
        }
    path = _path_of(ctx, doc_id)
    if not path:
        return _no_path("rollback", doc_id)
    try:
        result = ctx.co_client.checkout(path, hash)
    except CoError as exc:
        logger.warning("rollback tool failed for docId=%r: %s", doc_id, exc)
        return {
            "ok": False,
            "tool": "rollback",
            "docId": doc_id,
            "status": "error",
            "error": str(exc),
        }
    ctx.record_audit(
        "version_rollback", tool="rollback", doc_id=doc_id, hash=result.hash
    )
    return {
        "ok": True,
        "tool": "rollback",
        "docId": doc_id,
        "hash": result.hash,
    }


def branch(
    ctx: WriteContext,
    doc_id: str = DEFAULT_DOC_ID,
    name: str = "",
) -> dict[str, Any]:
    """Create a named branch at the current commit (doc 14.3)."""
    if not name:
        return {
            "ok": False,
            "tool": "branch",
            "docId": doc_id,
            "status": "error",
            "error": _MISSING_ARG_ERROR.format(arg="name"),
        }
    path = _path_of(ctx, doc_id)
    if not path:
        return _no_path("branch", doc_id)
    try:
        result = ctx.co_client.branch(path, name)
    except CoError as exc:
        logger.warning("branch tool failed for docId=%r: %s", doc_id, exc)
        return {
            "ok": False,
            "tool": "branch",
            "docId": doc_id,
            "status": "error",
            "error": str(exc),
        }
    ctx.record_audit(
        "version_branch", tool="branch", doc_id=doc_id, name=result.name, hash=result.hash
    )
    return {
        "ok": True,
        "tool": "branch",
        "docId": doc_id,
        "name": result.name,
        "hash": result.hash,
    }


def merge(
    ctx: WriteContext,
    doc_id: str = DEFAULT_DOC_ID,
    branch: str = "",
) -> dict[str, Any]:
    """Merge a named branch into the current one (doc 14.3)."""
    if not branch:
        return {
            "ok": False,
            "tool": "merge",
            "docId": doc_id,
            "status": "error",
            "error": _MISSING_ARG_ERROR.format(arg="branch"),
        }
    path = _path_of(ctx, doc_id)
    if not path:
        return _no_path("merge", doc_id)
    try:
        result = ctx.co_client.merge(path, branch)
    except CoError as exc:
        logger.warning("merge tool failed for docId=%r: %s", doc_id, exc)
        return {
            "ok": False,
            "tool": "merge",
            "docId": doc_id,
            "status": "error",
            "error": str(exc),
        }
    ctx.record_audit(
        "version_merge", tool="merge", doc_id=doc_id, branch=result.branch, hash=result.hash
    )
    return {
        "ok": True,
        "tool": "merge",
        "docId": doc_id,
        "branch": result.branch,
        "hash": result.hash,
        "message": result.message,
    }


def tag(
    ctx: WriteContext,
    doc_id: str = DEFAULT_DOC_ID,
    name: str = "",
) -> dict[str, Any]:
    """Tag the current commit with a human-readable name (doc 14.3)."""
    if not name:
        return {
            "ok": False,
            "tool": "tag",
            "docId": doc_id,
            "status": "error",
            "error": _MISSING_ARG_ERROR.format(arg="name"),
        }
    path = _path_of(ctx, doc_id)
    if not path:
        return _no_path("tag", doc_id)
    try:
        result = ctx.co_client.tag(path, name)
    except CoError as exc:
        logger.warning("tag tool failed for docId=%r: %s", doc_id, exc)
        return {
            "ok": False,
            "tool": "tag",
            "docId": doc_id,
            "status": "error",
            "error": str(exc),
        }
    ctx.record_audit(
        "version_tag", tool="tag", doc_id=doc_id, name=result.name, hash=result.hash
    )
    return {
        "ok": True,
        "tool": "tag",
        "docId": doc_id,
        "name": result.name,
        "hash": result.hash,
    }


# ============================================================================
# export / import (ADR-005)
# ============================================================================


def export_doc(
    ctx: WriteContext,
    doc_id: str = DEFAULT_DOC_ID,
    include_co: bool = False,
    include_bundle: bool = True,
    path: str | None = None,
    package: bool = False,
) -> dict[str, Any]:
    """Export the document honoring ADR-005.

    Args:
        include_co: when True the exported copy keeps the ``.co/`` history
            inside the file AND the result carries the ADR-005 warning
            ("Version history inside the file will be lost if opened with
            Word/WPS; recommended to also export a .co-bundle"). Default
            False = PURE export (no ``.co/``), 100% editor-compatible.
        include_bundle: write a ``<file>.co-bundle`` companion carrying the
            full history (default True).
        path: destination for the exported document copy; defaults to the
            document's own path.
        package: additionally zip document + bundle into
            ``<file>.coffice.zip``.
    """
    src = _path_of(ctx, doc_id)
    if not src:
        return _no_path("exportDoc", doc_id)
    try:
        result = export_document(
            src,
            include_co=include_co,
            include_bundle=include_bundle,
            out_path=path,
            package=package,
            co_client=ctx.co_client,
        )
    except (CoError, OSError, ValueError) as exc:
        logger.warning("exportDoc tool failed for docId=%r: %s", doc_id, exc)
        return {
            "ok": False,
            "tool": "exportDoc",
            "docId": doc_id,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    ctx.record_audit(
        "version_export",
        tool="exportDoc",
        doc_id=doc_id,
        out_path=result.out_path,
        bundle_path=result.bundle_path,
        include_co=result.include_co,
    )
    return {
        "ok": True,
        "tool": "exportDoc",
        "docId": doc_id,
        "path": result.out_path,
        "bundlePath": result.bundle_path,
        "packagePath": result.package_path,
        "includeCo": result.include_co,
        "warning": INCLUDE_CO_WARNING if result.include_co else "",
        "warnings": list(result.warnings),
    }


def import_bundle(
    ctx: WriteContext,
    doc_id: str = DEFAULT_DOC_ID,
    bundle_path: str = "",
) -> dict[str, Any]:
    """Restore history from a ``.co-bundle`` onto the document (doc 14.6 B).

    The bundle's commits are merged into the document's history and the
    current document state is appended as a new commit, so the restored
    history continues from the document's actual state.
    """
    if not bundle_path:
        return {
            "ok": False,
            "tool": "importBundle",
            "docId": doc_id,
            "status": "error",
            "error": _MISSING_ARG_ERROR.format(arg="bundlePath"),
        }
    doc_path = _path_of(ctx, doc_id)
    if not doc_path:
        return _no_path("importBundle", doc_id)
    if not Path(bundle_path).is_file():
        return {
            "ok": False,
            "tool": "importBundle",
            "docId": doc_id,
            "status": "error",
            "error": f"bundle not found: {bundle_path}",
        }
    try:
        result = import_bundle_flow(
            doc_path,
            bundle_path,
            co_client=ctx.co_client,
            author=ctx.author,
            human_operator=ctx.human_operator,
        )
    except (CoError, OSError, ValueError) as exc:
        logger.warning("importBundle tool failed for docId=%r: %s", doc_id, exc)
        return {
            "ok": False,
            "tool": "importBundle",
            "docId": doc_id,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    ctx.record_audit(
        "version_import",
        tool="importBundle",
        doc_id=doc_id,
        bundle_path=bundle_path,
        imported_commits=result.imported_commits,
        commit=result.commit_hash,
    )
    return {
        "ok": True,
        "tool": "importBundle",
        "docId": doc_id,
        "bundlePath": result.bundle_path,
        "importedCommits": result.imported_commits,
        "commit": result.commit_hash,
        "warning": result.warning,
    }


__all__ = [
    "branch",
    "export_doc",
    "import_bundle",
    "merge",
    "rollback",
    "snapshot",
    "tag",
]
