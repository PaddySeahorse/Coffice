"""co CLI wrapper providing Git-style version-control snapshots (planning doc ch. 14).

Public API (see :mod:`coffice.versioning.co_client` for full docs)::

    from coffice.versioning import CoClient, co_available, CommitInfo, DiffEntry

    client = CoClient()          # resolves CO_BIN / PATH
    if co_available():
        h = client.commit("doc.docx", "pre: insertText", author="AI-Writer")
        history = client.log("doc.docx", limit=20)
"""

from coffice.versioning.co_client import (
    INCLUDE_CO_WARNING,
    BranchResult,
    CheckoutResult,
    CoClient,
    CoCommandError,
    CoError,
    CommitInfo,
    CoNotAvailableError,
    CoUnsupportedError,
    DiffEntry,
    ExportResult,
    ImportResult,
    MergeResult,
    TagResult,
    co_available,
    compose_commit_message,
    default_client,
    require_co,
    resolve_bin_path,
)

__all__ = [
    "CoClient",
    "CoError",
    "CoNotAvailableError",
    "CoUnsupportedError",
    "CoCommandError",
    "CommitInfo",
    "DiffEntry",
    "CheckoutResult",
    "BranchResult",
    "MergeResult",
    "TagResult",
    "ExportResult",
    "ImportResult",
    "INCLUDE_CO_WARNING",
    "compose_commit_message",
    "resolve_bin_path",
    "co_available",
    "require_co",
    "default_client",
]
