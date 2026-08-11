"""Integration tests against the real co binary (skipped when unavailable).

These tests create a real .docx (a valid ZIP), drive the actual co CLI, and
verify the wrapper end-to-end: commit -> log -> diff -> checkout, export
(ADR-005 pure vs include-co), and import round-trip. Skipped cleanly when the
co binary is not installed in the sandbox.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from coffice.versioning import (
    INCLUDE_CO_WARNING,
    co_available,
    resolve_bin_path,
)
from coffice.versioning.co_client import CoClient

pytestmark = pytest.mark.skipif(
    not co_available(),
    reason="co binary not installed (run scripts/install_co.sh)",
)


@pytest.fixture(scope="module")
def client() -> CoClient:
    return CoClient(bin_path=resolve_bin_path())


def make_docx(path: Path, text: str = "Hello from Coffice") -> Path:
    """Create a minimal but valid .docx (OPC zip), preserving existing entries."""
    existing: dict[str, bytes] = {}
    if path.exists():
        try:
            with zipfile.ZipFile(path) as zf:
                existing = {name: zf.read(name) for name in zf.namelist()}
        except zipfile.BadZipFile:
            existing = {}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        content_types = existing.get(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType='
            '"application/vnd.openxmlformats-package.relationships+xml"/>'
            "</Types>",
        )
        rels = existing.get(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type='
            '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        document = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
        ).encode()
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
        for name, data in existing.items():
            if name in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
                continue
            zf.writestr(name, data)
    return path


def test_real_commit_log_diff_flow(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "report.docx")
    client.init(str(doc))
    h1 = client.commit(str(doc), "Initial import", author="AI-Writer")
    assert len(h1) == 40

    make_docx(doc, "Hello from Coffice v2")
    h2 = client.commit(str(doc), "Second edit", author="human:张三")
    assert h2 != h1

    commits = client.log(str(doc))
    assert [c.hash for c in commits] == [h2, h1]
    assert commits[0].author == "human:张三"
    assert commits[1].author == "AI-Writer"
    assert commits[0].timestamp is not None
    assert commits[1].timestamp is not None

    entries = client.diff(str(doc), h1, h2)
    assert isinstance(entries, list)
    assert all(entry.path for entry in entries)


def test_real_checkout_restores_content(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "report.docx", "version one")
    client.init(str(doc))
    h1 = client.commit(str(doc), "one", author="AI-Writer")
    make_docx(doc, "version two")
    h2 = client.commit(str(doc), "two", author="AI-Writer")

    result = client.checkout(str(doc), h1)
    assert result.hash == h1
    with zipfile.ZipFile(doc) as zf:
        content = zf.read("word/document.xml").decode()
    assert "version one" in content

    client.checkout(str(doc), h2)
    with zipfile.ZipFile(doc) as zf:
        content = zf.read("word/document.xml").decode()
    assert "version two" in content


def test_real_export_pure_vs_include_co(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "report.docx")
    client.init(str(doc))
    client.commit(str(doc), "m", author="AI-Writer")

    pure = tmp_path / "pure.docx"
    pure_result = client.export(str(doc), include_co=False, out_path=str(pure))
    assert pure_result.include_co is False
    assert pure_result.warning == ""
    assert pure.exists()
    with zipfile.ZipFile(pure) as zf:
        names = zf.namelist()
    assert not any(n.startswith(".co/") for n in names), "pure export must not contain .co/"

    with_co = tmp_path / "withco.docx"
    co_result = client.export(str(doc), include_co=True, out_path=str(with_co))
    assert co_result.include_co is True
    assert co_result.warning == INCLUDE_CO_WARNING
    assert with_co.exists()
    assert Path(co_result.bundle_path).exists()
    with zipfile.ZipFile(with_co) as zf:
        names = zf.namelist()
    assert any(n.startswith(".co/") for n in names), "include_co export must retain .co/"


def test_real_import_roundtrip_preserves_history(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "report.docx")
    client.init(str(doc))
    client.commit(str(doc), "one", author="AI-Writer")
    client.commit(str(doc), "two", author="AI-Writer")
    before = len(client.log(str(doc)))
    assert before == 2

    out = tmp_path / "exported.docx"
    result = client.export(str(doc), include_co=False, out_path=str(out))
    assert Path(result.bundle_path).exists()

    fresh = make_docx(tmp_path / "fresh.docx")
    imp = client.import_bundle(str(fresh), result.bundle_path, force=True)
    assert imp.bundle_path == result.bundle_path
    assert len(client.log(str(fresh))) == before


def test_real_branch(client: CoClient, tmp_path: Path) -> None:
    """Upstream co now supports branch natively."""
    doc = make_docx(tmp_path / "report.docx")
    client.init(str(doc))
    h = client.commit(str(doc), "base", author="AI-Writer")
    result = client.branch(str(doc), "feature")
    assert result.name == "feature"
    assert result.hash == h


def test_real_merge_tag_unsupported(client: CoClient, tmp_path: Path) -> None:
    """Upstream co does not implement merge/tag -> typed error."""
    doc = make_docx(tmp_path / "report.docx")
    client.init(str(doc))
    client.commit(str(doc), "m", author="AI-Writer")
