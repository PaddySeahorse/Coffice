"""Unit tests for the sidebar save pipeline (coffice.sidebar.co_save).

The pipeline must preserve the ``.co/`` version history across a LibreOffice
save that would otherwise wipe it. The ``co`` CLI paths are exercised against
the fake binary (``tests/versioning/fake_co.py``); the no-CLI zip fallback is
exercised with real ZIP fixtures.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from coffice.sidebar import co_save

FAKE_CO = Path(__file__).resolve().parents[1] / "versioning" / "fake_co.py"

CO_ENTRIES = (".co/refs/HEAD", ".co/objects/abc123")


def make_docx_with_co(path: Path) -> Path:
    """Create a minimal .docx placeholder that carries a ``.co/`` directory."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "<w:document/>")
        zf.writestr(CO_ENTRIES[0], "ref: abc123")
        zf.writestr(CO_ENTRIES[1], "blob")
    return path


def make_plain_docx(path: Path) -> Path:
    """Create a minimal .docx placeholder without ``.co/`` history."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "<w:document/>")
    return path


def wipe_co(path: Path) -> None:
    """Simulate a LibreOffice save: rewrite the file dropping every .co/ entry."""
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(
        path.with_suffix(path.suffix + ".tmp"), "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for info in zin.infolist():
            if info.filename.startswith(co_save.CO_DIR_PREFIX):
                continue
            zout.writestr(info, zin.read(info.filename))
    path.with_suffix(path.suffix + ".tmp").replace(path)


def zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


@pytest.fixture()
def no_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the zip fallback: discovery finds no co binary."""
    monkeypatch.delenv("CO_BIN", raising=False)
    monkeypatch.delenv("COFFICE_CO_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(co_save, "find_co_binary", lambda: None)


@pytest.fixture()
def fake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the fake co binary at an isolated state file."""
    monkeypatch.setenv("FAKE_CO_STATE", str(tmp_path / "state.json"))


# --------------------------------------------------------------------------
# binary discovery
# --------------------------------------------------------------------------


def test_find_co_binary_prefers_cob(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    candidate = tmp_path / "co"
    candidate.write_text("fake binary", encoding="utf-8")
    monkeypatch.setenv("CO_BIN", str(candidate))
    monkeypatch.delenv("COFFICE_CO_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert co_save.find_co_binary() == str(candidate)


def test_find_co_binary_falls_back_to_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CO_BIN", raising=False)
    monkeypatch.delenv("COFFICE_CO_BIN", raising=False)
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/co" if name == "co" else None
    )
    assert co_save.find_co_binary() == "/usr/local/bin/co"


def test_find_co_binary_checks_common_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CO_BIN", raising=False)
    monkeypatch.delenv("COFFICE_CO_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    co = tmp_path / ".local" / "bin" / "co"
    co.parent.mkdir(parents=True)
    co.write_text("fake binary", encoding="utf-8")
    assert co_save.find_co_binary() == str(co)


# --------------------------------------------------------------------------
# zip fallback (no co CLI)
# --------------------------------------------------------------------------


def test_backup_history_writes_bundle_via_zip(no_cli: None, tmp_path: Path) -> None:
    doc = make_docx_with_co(tmp_path / "report.docx")
    bundle = co_save.backup_history(str(doc))
    assert bundle is not None
    assert CO_ENTRIES[0] in zip_names(Path(bundle))
    assert CO_ENTRIES[1] in zip_names(Path(bundle))


def test_backup_history_returns_none_without_history(
    no_cli: None, tmp_path: Path
) -> None:
    doc = make_plain_docx(tmp_path / "plain.docx")
    assert co_save.backup_history(str(doc)) is None


def test_restore_history_reinjects_co_entries(no_cli: None, tmp_path: Path) -> None:
    doc = make_docx_with_co(tmp_path / "report.docx")
    bundle = co_save.backup_history(str(doc))
    assert bundle is not None
    wipe_co(doc)
    assert not {n for n in zip_names(doc) if n.startswith(co_save.CO_DIR_PREFIX)}
    co_save.restore_history(str(doc), bundle)
    names = zip_names(doc)
    assert CO_ENTRIES[0] in names
    assert CO_ENTRIES[1] in names


def test_restore_history_rejects_empty_bundle(no_cli: None, tmp_path: Path) -> None:
    doc = make_docx_with_co(tmp_path / "report.docx")
    empty = tmp_path / "empty.co-bundle"
    with zipfile.ZipFile(empty, "w"):
        pass
    with pytest.raises(co_save.CoSaveError):
        co_save.restore_history(str(doc), str(empty))


def test_commit_returns_none_without_cli(no_cli: None, tmp_path: Path) -> None:
    assert co_save.commit_snapshot(str(tmp_path / "report.docx")) is None


# --------------------------------------------------------------------------
# co CLI path (fake binary)
# --------------------------------------------------------------------------


def test_backup_history_uses_cli_export(fake_env: None, tmp_path: Path) -> None:
    doc = make_docx_with_co(tmp_path / "report.docx")
    bundle = co_save.backup_history(str(doc), bin_path=str(FAKE_CO))
    assert bundle is not None
    assert Path(bundle).is_file()


def test_restore_history_uses_cli_import(fake_env: None, tmp_path: Path) -> None:
    doc = make_docx_with_co(tmp_path / "report.docx")
    bundle = co_save.backup_history(str(doc), bin_path=str(FAKE_CO))
    assert bundle is not None
    co_save.restore_history(str(doc), bundle, bin_path=str(FAKE_CO))  # no raise


def test_commit_snapshot_returns_hash(fake_env: None, tmp_path: Path) -> None:
    doc = make_docx_with_co(tmp_path / "report.docx")
    bundle = co_save.backup_history(str(doc), bin_path=str(FAKE_CO))
    assert bundle is not None
    co_save.restore_history(str(doc), bundle, bin_path=str(FAKE_CO))
    commit = co_save.commit_snapshot(str(doc), bin_path=str(FAKE_CO))
    assert commit is not None


def test_cli_failure_raises_co_save_error(
    fake_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FAKE_CO_FAIL_COMMANDS", "export")
    doc = make_docx_with_co(tmp_path / "report.docx")
    with pytest.raises(co_save.CoSaveError):
        co_save.backup_history(str(doc), bin_path=str(FAKE_CO))
