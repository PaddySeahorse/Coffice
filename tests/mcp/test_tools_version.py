"""Unit tests for the version-control MCP tools (planning doc 14.3 + ADR-005).

Covers the acceptance criteria:

- all 7 version tools (snapshot, rollback, branch, merge, tag, exportDoc,
  importBundle) are registered and callable (alongside the existing
  getHistory read tool they form the full version-tool surface);
- exportDoc defaults to a PURE export (no .co/ embedded);
- includeCo triggers the ADR-005 warning string;
- bundle export/import round-trips history (import preserves the exported
  commits and appends the current state as a new commit).

The co side uses the real :class:`CoClient` against
``tests/versioning/fake_co.py``; the own-ZIP fallback path (no co CLI) is
exercised with a client pointing at a missing binary.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from tests.mcp.test_server import call_tool, list_tools

from coffice.mcp import VERSION_TOOL_NAMES, create_server
from coffice.mcp.middleware import RoundTracker
from coffice.mcp.registry import DEFAULT_DOC_ID, DocumentRegistry
from coffice.versioning import INCLUDE_CO_WARNING
from coffice.versioning.co_client import CoClient

FAKE_CO = Path(__file__).resolve().parent.parent / "versioning" / "fake_co.py"


class FakeVersionDocument:
    """Minimal in-memory stand-in for Document (version tools need only path)."""

    def get_text(self) -> str:
        return "Hello from Coffice"

    def get_outline(self) -> list[dict[str, Any]]:
        return []


def make_zip_docx(path: Path, with_co: bool = False) -> Path:
    """Create a minimal but valid .docx (OPC zip), optionally with .co/ entries."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType='
            '"application/vnd.openxmlformats-package.relationships+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type='
            '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Hello from Coffice</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )
        if with_co:
            zf.writestr(".co/HEAD", "ref: refs/heads/main\n")
            zf.writestr(".co/refs/heads/main", "abc123\n")
    return path


def read_invocations(fake_co_env: dict[str, str]) -> list[list[str]]:
    """Read the fake co binary's recorded argv lines."""
    record = Path(fake_co_env["FAKE_CO_RECORD"])
    if not record.exists():
        return []
    return [
        json.loads(line)
        for line in record.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def commits_of(invocations: list[list[str]]) -> list[list[str]]:
    return [line for line in invocations if line[1] == "commit"]


def zip_names(path: str | Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def fake_co_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Point the fake co binary at an isolated state + invocation record."""
    env = {
        "FAKE_CO_STATE": str(tmp_path / "state.json"),
        "FAKE_CO_RECORD": str(tmp_path / "invocations.jsonl"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    monkeypatch.setenv("FAKE_CO_STATE", env["FAKE_CO_STATE"])
    return env


@pytest.fixture()
def co_client(fake_co_env: dict[str, str]) -> CoClient:
    return CoClient(bin_path=str(FAKE_CO), env=fake_co_env)


@pytest.fixture()
def version_doc(tmp_path: Path) -> Path:
    """A real zip docx carrying .co/ history entries, at a registry path."""
    return make_zip_docx(tmp_path / "report.docx", with_co=True)


@pytest.fixture()
def version_registry(version_doc: Path) -> DocumentRegistry:
    return DocumentRegistry(
        doc_path=str(version_doc),
        factory=lambda doc_id, path: FakeVersionDocument(),
    )


@pytest.fixture()
def version_server(version_registry: DocumentRegistry, co_client: CoClient) -> FastMCP:
    return create_server(
        registry=version_registry, co_client=co_client, round_tracker=RoundTracker()
    )


@pytest.fixture()
def no_co_server(tmp_path: Path, version_registry: DocumentRegistry) -> FastMCP:
    """Server whose co client points at a missing binary (own-ZIP fallback)."""
    missing = CoClient(bin_path=str(tmp_path / "missing-co"))
    return create_server(
        registry=version_registry, co_client=missing, round_tracker=RoundTracker()
    )


@pytest.fixture()
def no_path_server(fake_co_env: dict[str, str]) -> FastMCP:
    """Server on an in-memory registry with no on-disk path (unsaved doc)."""
    registry = DocumentRegistry(factory=lambda doc_id, path: FakeVersionDocument())
    return create_server(
        registry=registry,
        co_client=CoClient(bin_path=str(FAKE_CO), env=fake_co_env),
        round_tracker=RoundTracker(),
    )


# --------------------------------------------------------------------------
# registration (acceptance: all version tools registered and callable)
# --------------------------------------------------------------------------


def test_all_version_tools_registered(version_server: FastMCP) -> None:
    tools = {t["name"]: t for t in list_tools(version_server)}
    assert set(VERSION_TOOL_NAMES) <= set(tools)
    for name in VERSION_TOOL_NAMES:
        tool = tools[name]
        assert tool["description"], f"{name} missing description"
        assert "properties" in tool["inputSchema"], f"{name} missing inputSchema"


def test_get_history_still_registered_beside_version_tools(
    version_server: FastMCP,
) -> None:
    """getHistory (read side) + the 7 new tools form the version surface."""
    tools = {t["name"]: t for t in list_tools(version_server)}
    assert "getHistory" in tools
    for name in VERSION_TOOL_NAMES:
        assert name in tools


# --------------------------------------------------------------------------
# snapshot / rollback (doc 14.3: co commit / co checkout)
# --------------------------------------------------------------------------


def test_snapshot_commits_with_label(version_server: FastMCP, fake_co_env: dict[str, str]) -> None:
    result = call_tool(version_server, "snapshot", {"docId": DEFAULT_DOC_ID, "label": "v1"})
    assert result["ok"] is True
    assert result["tool"] == "snapshot"
    assert result["label"] == "v1"
    assert len(result["hash"]) == 40
    commits = commits_of(read_invocations(fake_co_env))
    assert commits[-1][:3] == ["co", "commit", "-m"]
    assert commits[-1][3] == "v1"


def test_rollback_checkouts_hash(version_server: FastMCP, fake_co_env: dict[str, str]) -> None:
    snap = call_tool(version_server, "snapshot", {"docId": DEFAULT_DOC_ID, "label": "base"})
    result = call_tool(version_server, "rollback", {"docId": DEFAULT_DOC_ID, "hash": snap["hash"]})
    assert result["ok"] is True
    assert result["hash"] == snap["hash"]
    checkouts = [line for line in read_invocations(fake_co_env) if line[1] == "checkout"]
    assert checkouts[-1][:2] == ["co", "checkout"]
    assert checkouts[-1][2] == snap["hash"]


def test_rollback_requires_hash(version_server: FastMCP) -> None:
    result = call_tool(version_server, "rollback", {"docId": DEFAULT_DOC_ID, "hash": ""})
    assert result["ok"] is False
    assert "hash" in result["error"]


def test_snapshot_requires_saved_path(no_path_server: FastMCP) -> None:
    result = call_tool(no_path_server, "snapshot", {"docId": DEFAULT_DOC_ID, "label": "x"})
    assert result["ok"] is False
    assert "no on-disk" in result["error"]


# --------------------------------------------------------------------------
# branch / merge / tag (doc 14.3, emulated by the fake binary)
# --------------------------------------------------------------------------


def test_branch_merge_tag(version_server: FastMCP, fake_co_env: dict[str, str]) -> None:
    call_tool(version_server, "snapshot", {"docId": DEFAULT_DOC_ID, "label": "base"})
    branched = call_tool(version_server, "branch", {"docId": DEFAULT_DOC_ID, "name": "feature"})
    assert branched["ok"] is True
    assert branched["name"] == "feature"
    assert branched["hash"]

    merged = call_tool(version_server, "merge", {"docId": DEFAULT_DOC_ID, "branch": "feature"})
    assert merged["ok"] is True
    assert merged["branch"] == "feature"
    assert merged["hash"]

    tagged = call_tool(version_server, "tag", {"docId": DEFAULT_DOC_ID, "name": "v1.0"})
    assert tagged["ok"] is True
    assert tagged["name"] == "v1.0"

    invocations = read_invocations(fake_co_env)
    # The 'log' probe call happens inside `self.log` on the first call, so there are two 'log' commands:
    # 1. log --json (probe fails or parses)
    # 2. log (or --json again depending on probe)
    # Let's filter out multiple 'log' occurrences to make the assert simpler and robust.
    commands = [line[1] for line in invocations if line[1] != "--help"]
    assert commands == ["commit", "branch", "log", "log", "merge", "tag"]


def test_branch_requires_name(version_server: FastMCP) -> None:
    result = call_tool(version_server, "branch", {"docId": DEFAULT_DOC_ID, "name": ""})
    assert result["ok"] is False


# --------------------------------------------------------------------------
# exportDoc (ADR-005)
# --------------------------------------------------------------------------


def test_export_default_is_pure(
    version_server: FastMCP, tmp_path: Path
) -> None:
    out = tmp_path / "exported.docx"
    result = call_tool(version_server, "exportDoc", {"docId": DEFAULT_DOC_ID, "path": str(out)})
    assert result["ok"] is True
    assert result["includeCo"] is False
    assert result["warning"] == ""
    assert result["warnings"] == []
    assert result["path"] == str(out)
    assert Path(result["path"]).exists()
    assert result["bundlePath"] and Path(result["bundlePath"]).exists()


def test_export_include_co_adds_warning(version_server: FastMCP, tmp_path: Path) -> None:
    out = tmp_path / "withco.docx"
    result = call_tool(
        version_server,
        "exportDoc",
        {"docId": DEFAULT_DOC_ID, "path": str(out), "includeCo": True},
    )
    assert result["ok"] is True
    assert result["includeCo"] is True
    assert result["warning"] == INCLUDE_CO_WARNING
    assert INCLUDE_CO_WARNING in result["warnings"]
    assert Path(result["path"]).exists()
    assert Path(result["bundlePath"]).exists()


def test_export_pure_fallback_never_contains_co(no_co_server: FastMCP, tmp_path: Path) -> None:
    """No co CLI: the own-ZIP export must still strip every .co/ entry."""
    out = tmp_path / "pure.docx"
    result = call_tool(no_co_server, "exportDoc", {"docId": DEFAULT_DOC_ID, "path": str(out)})
    assert result["ok"] is True
    assert result["includeCo"] is False
    names = zip_names(result["path"])
    assert not any(name.startswith(".co/") for name in names)
    assert "word/document.xml" in names


def test_export_no_path_fallback_does_not_corrupt_source(
    no_co_server: FastMCP, version_doc: Path
) -> None:
    """Regression (PR #8): exportDoc with no path on a no-CLI server must not
    truncate the source document, and the bundle must still carry history."""
    before = version_doc.read_bytes()
    result = call_tool(no_co_server, "exportDoc", {"docId": DEFAULT_DOC_ID})
    assert result["ok"] is True
    assert result["includeCo"] is False
    assert result["path"] == str(version_doc)
    # the source is intact and still a readable zip
    assert version_doc.read_bytes() == before
    assert "word/document.xml" in zip_names(version_doc)
    # the bundle companion carries the source's .co/ history
    bundle = result["bundlePath"]
    assert bundle and Path(bundle).exists()
    assert any(name.startswith(".co/") for name in zip_names(bundle))


def test_export_include_co_fallback_keeps_history(no_co_server: FastMCP, tmp_path: Path) -> None:
    out = tmp_path / "withco.docx"
    result = call_tool(
        no_co_server,
        "exportDoc",
        {"docId": DEFAULT_DOC_ID, "path": str(out), "includeCo": True},
    )
    assert result["ok"] is True
    assert result["includeCo"] is True
    assert result["warning"] == INCLUDE_CO_WARNING
    names = zip_names(result["path"])
    assert any(name.startswith(".co/") for name in names)


def test_export_package_zips_document_and_bundle(version_server: FastMCP, tmp_path: Path) -> None:
    out = tmp_path / "exported.docx"
    result = call_tool(
        version_server,
        "exportDoc",
        {"docId": DEFAULT_DOC_ID, "path": str(out), "package": True},
    )
    assert result["ok"] is True
    package = result["packagePath"]
    assert package and Path(package).exists()
    names = zip_names(package)
    assert Path(out).name in names
    assert Path(out).name + ".co-bundle" in names


def test_export_doc_requires_saved_path(no_path_server: FastMCP) -> None:
    result = call_tool(no_path_server, "exportDoc", {"docId": DEFAULT_DOC_ID})
    assert result["ok"] is False
    assert "no on-disk" in result["error"]


# --------------------------------------------------------------------------
# importBundle (doc 14.6 scenario B): bundle round-trips history
# --------------------------------------------------------------------------


def test_import_bundle_roundtrip_preserves_commits(
    version_server: FastMCP,
    co_client: CoClient,
    version_doc: Path,
    tmp_path: Path,
) -> None:
    call_tool(version_server, "snapshot", {"docId": DEFAULT_DOC_ID, "label": "one"})
    call_tool(version_server, "snapshot", {"docId": DEFAULT_DOC_ID, "label": "two"})
    assert len(co_client.log(str(version_doc))) == 2

    out = tmp_path / "exported.docx"
    exported = call_tool(version_server, "exportDoc", {"docId": DEFAULT_DOC_ID, "path": str(out)})
    bundle_path = exported["bundlePath"]
    assert Path(bundle_path).exists()

    fresh = make_zip_docx(tmp_path / "fresh.docx")
    fresh_registry = DocumentRegistry(
        doc_path=str(fresh),
        factory=lambda doc_id, path: FakeVersionDocument(),
    )
    fresh_server = create_server(
        registry=fresh_registry, co_client=co_client, round_tracker=RoundTracker()
    )
    imported = call_tool(
        fresh_server, "importBundle", {"docId": DEFAULT_DOC_ID, "bundlePath": bundle_path}
    )
    assert imported["ok"] is True
    assert imported["bundlePath"] == bundle_path
    assert imported["importedCommits"] == 2
    assert imported["commit"]

    commits = co_client.log(str(fresh))
    messages = {c.message for c in commits}
    # the exported history is preserved, and the current state was appended
    assert {"one", "two"} <= messages
    assert any(c.message.startswith("import: ") for c in commits)
    assert len(commits) == 3


def test_import_bundle_missing_bundle_errors(version_server: FastMCP, tmp_path: Path) -> None:
    result = call_tool(
        version_server,
        "importBundle",
        {"docId": DEFAULT_DOC_ID, "bundlePath": str(tmp_path / "nope.co-bundle")},
    )
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_import_bundle_requires_bundle_path(version_server: FastMCP) -> None:
    result = call_tool(version_server, "importBundle", {"docId": DEFAULT_DOC_ID, "bundlePath": ""})
    assert result["ok"] is False
    assert "bundlePath" in result["error"]


def test_import_bundle_no_cli_fallback_reports_restore(
    no_co_server: FastMCP, version_doc: Path, tmp_path: Path
) -> None:
    """No co CLI: importBundle restores the bundle's .co/ entries and reports
    the degraded outcome (history restored, no commit appended) instead of a
    silent zero-count success."""
    out = tmp_path / "exported.docx"
    exported = call_tool(
        no_co_server, "exportDoc", {"docId": DEFAULT_DOC_ID, "path": str(out)}
    )
    bundle_path = exported["bundlePath"]
    assert bundle_path and Path(bundle_path).exists()

    fresh = make_zip_docx(tmp_path / "fresh.docx")
    fresh_registry = DocumentRegistry(
        doc_path=str(fresh),
        factory=lambda doc_id, path: FakeVersionDocument(),
    )
    missing = CoClient(bin_path=str(tmp_path / "missing-co"))
    fresh_server = create_server(
        registry=fresh_registry, co_client=missing, round_tracker=RoundTracker()
    )
    imported = call_tool(
        fresh_server,
        "importBundle",
        {"docId": DEFAULT_DOC_ID, "bundlePath": bundle_path},
    )
    assert imported["ok"] is True
    assert imported["importedCommits"] > 0
    assert imported["commit"] is None
    assert "without co CLI" in imported["warning"]
    assert any(name.startswith(".co/") for name in zip_names(fresh))
