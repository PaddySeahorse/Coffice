"""Unit tests for coffice.versioning.co_client against the fake co binary.

The fake binary (``fake_co.py``) emulates the co CLI output formats so these
tests run without the real ``co`` binary installed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from coffice.versioning import (
    INCLUDE_CO_WARNING,
    CoCommandError,
    CommitInfo,
    CoNotAvailableError,
    CoUnsupportedError,
    DiffEntry,
    co_available,
    compose_commit_message,
    require_co,
    resolve_bin_path,
)
from coffice.versioning.co_client import CoClient

FAKE_CO = Path(__file__).parent / "fake_co.py"


@pytest.fixture()
def fake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Point the fake binary at an isolated state file and record log."""
    state = tmp_path / "state.json"
    record = tmp_path / "invocations.jsonl"
    env = {
        "FAKE_CO_STATE": str(state),
        "FAKE_CO_RECORD": str(record),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    monkeypatch.setenv("FAKE_CO_STATE", str(state))
    return env


@pytest.fixture()
def client(fake_env: dict[str, str]) -> CoClient:
    return CoClient(bin_path=str(FAKE_CO), env=fake_env)


def make_docx(path: Path) -> Path:
    """Create a minimal (non-parsed by the fake) .docx placeholder."""
    path.write_text("placeholder docx", encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# binary discovery / feature detection
# --------------------------------------------------------------------------


def test_co_available_with_fake(fake_env: dict[str, str]) -> None:
    assert co_available(bin_path=str(FAKE_CO)) is True


def test_co_available_missing_binary(tmp_path: Path) -> None:
    assert co_available(bin_path=str(tmp_path / "nope")) is False


def test_resolve_bin_path_prefers_cob(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CO_BIN", str(FAKE_CO))
    assert resolve_bin_path() == str(FAKE_CO)


def test_resolve_bin_path_finds_windows_style_name(monkeypatch, tmp_path: Path) -> None:
    candidate = tmp_path / "co.exe"
    candidate.write_text("fake")
    monkeypatch.delenv("CO_BIN", raising=False)
    monkeypatch.delenv("COFFICE_CO_BIN", raising=False)
    monkeypatch.setattr(
        "shutil.which", lambda name: str(candidate) if name == "co.exe" else None
    )
    assert resolve_bin_path() == str(candidate)


def test_require_co_missing_mentions_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CO_BIN", raising=False)
    monkeypatch.delenv("COFFICE_CO_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("os.path.isfile", lambda _: False)
    with pytest.raises(CoNotAvailableError, match="install_co.sh"):
        require_co(bin_path=None)


def test_client_missing_binary_raises_not_available(tmp_path: Path) -> None:
    client = CoClient(bin_path=str(tmp_path / "missing-co"))
    with pytest.raises(CoNotAvailableError, match="install_co.sh"):
        client.commit("doc.docx", "msg")


# --------------------------------------------------------------------------
# commit + log
# --------------------------------------------------------------------------


def test_commit_returns_hash(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    hash_ = client.commit(str(doc), "Initial import", author="AI-Writer")
    assert len(hash_) == 40
    assert all(c in "0123456789abcdef" for c in hash_)


def test_commit_records_audit_fields(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    client.commit(str(doc), "Draft edits", author="human:张三", human_operator="alice")
    commits = client.log(str(doc))
    assert len(commits) == 1
    entry = commits[0]
    assert entry.author == "human:张三"
    assert "Draft edits" in entry.message
    assert "Coffice-Operator: alice" in entry.message
    assert entry.timestamp is not None
    assert entry.hash


def test_compose_commit_message() -> None:
    assert compose_commit_message("msg") == "msg"
    assert compose_commit_message("msg", None) == "msg"
    assert compose_commit_message("msg", "alice") == "msg\n\nCoffice-Operator: alice"


def test_log_returns_newest_first(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    h1 = client.commit(str(doc), "first", author="AI-Writer")
    h2 = client.commit(str(doc), "second", author="AI-Writer")
    commits = client.log(str(doc))
    assert [c.hash for c in commits] == [h2, h1]
    assert [c.message for c in commits] == ["second", "first"]


def test_log_text_parses_multiline_message(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    client.commit(str(doc), "line one\nline two", author="AI-Writer")
    commits = client.log(str(doc))
    assert commits[0].message == "line one\nline two"


def test_log_limit(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    client.commit(str(doc), "first")
    client.commit(str(doc), "second")
    client.commit(str(doc), "third")
    commits = client.log(str(doc), limit=2)
    assert [c.message for c in commits] == ["third", "second"]


def test_log_empty_repo(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    assert client.log(str(doc)) == []


def test_log_json_mode(tmp_path: Path) -> None:
    """When the binary supports --json, the wrapper parses JSON."""
    doc = make_docx(tmp_path / "doc.docx")
    client = CoClient(
        bin_path=str(FAKE_CO),
        env={
            "FAKE_CO_JSON_LOG": "1",
            "FAKE_CO_STATE": str(tmp_path / "state.json"),
        },
    )
    client.commit(str(doc), "json commit", author="AI-Writer")
    commits = client.log(str(doc))
    assert isinstance(commits, list)
    assert commits[0].message == "json commit"
    assert commits[0].timestamp is not None


def test_log_returns_commitinfo_type(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    client.commit(str(doc), "m", author="AI-Writer")
    assert isinstance(client.log(str(doc))[0], CommitInfo)


# --------------------------------------------------------------------------
# diff / checkout
# --------------------------------------------------------------------------


def test_diff_parses_entries(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    h1 = client.commit(str(doc), "a")
    h2 = client.commit(str(doc), "b")
    entries = client.diff(str(doc), h1, h2)
    assert len(entries) == 1
    assert isinstance(entries[0], DiffEntry)
    assert entries[0].status == "M"
    assert entries[0].path == "word/document.xml"


def test_diff_custom_lines(
    client: CoClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    monkeypatch.setenv("FAKE_CO_DIFF", "A word/styles.xml\nD word/footer.xml")
    entries = client.diff(str(doc), "a", "b")
    assert [(e.status, e.path) for e in entries] == [
        ("A", "word/styles.xml"),
        ("D", "word/footer.xml"),
    ]


def test_checkout(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    h = client.commit(str(doc), "snap")
    result = client.checkout(str(doc), h)
    assert result.hash == h


# --------------------------------------------------------------------------
# branch / merge / tag (emulated by the fake binary)
# --------------------------------------------------------------------------


def test_branch(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    h = client.commit(str(doc), "base")
    result = client.branch(str(doc), "feature")
    assert result.name == "feature"
    assert result.hash == h


def test_merge(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    client.commit(str(doc), "base")
    client.branch(str(doc), "feature")
    result = client.merge(str(doc), "feature")
    assert result.branch == "feature"
    assert result.hash is not None
    assert "Merged feature" in result.message


def test_tag(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    h = client.commit(str(doc), "base")
    result = client.tag(str(doc), "v1.0")
    assert result.name == "v1.0"
    assert result.hash == h


def test_merge_tag_unsupported_when_not_advertised(tmp_path: Path) -> None:
    """merge/tag raise CoUnsupportedError when the binary omits them."""
    client = CoClient(
        bin_path=str(FAKE_CO),
        env={"FAKE_CO_NO_BRANCH": "1", "FAKE_CO_STATE": str(tmp_path / "state.json")},
    )
    doc = make_docx(tmp_path / "doc.docx")
    with pytest.raises(CoUnsupportedError, match="merge"):
        client.merge(str(doc), "feature")
    with pytest.raises(CoUnsupportedError, match="tag"):
        client.tag(str(doc), "v1")


# --------------------------------------------------------------------------
# export / import (ADR-005)
# --------------------------------------------------------------------------


def test_export_pure_no_warning(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    client.commit(str(doc), "m")
    out = tmp_path / "pure.docx"
    result = client.export(str(doc), include_co=False, out_path=str(out))
    assert result.include_co is False
    assert result.warning == ""
    assert out.exists()
    assert Path(result.bundle_path).exists()


def test_export_include_co_adds_warning(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    client.commit(str(doc), "m")
    out = tmp_path / "withco.docx"
    result = client.export(str(doc), include_co=True, out_path=str(out))
    assert result.include_co is True
    assert result.warning == INCLUDE_CO_WARNING
    assert out.exists()
    assert Path(result.bundle_path).exists()


def test_import_bundle_roundtrip_preserves_commits(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    client.commit(str(doc), "one")
    client.commit(str(doc), "two")
    assert len(client.log(str(doc))) == 2

    out = tmp_path / "exported.docx"
    result = client.export(str(doc), include_co=False, out_path=str(out))

    fresh = make_docx(tmp_path / "fresh.docx")
    imp = client.import_bundle(str(fresh), result.bundle_path)
    assert imp.bundle_path == result.bundle_path
    assert imp.warning == ""
    assert len(client.log(str(fresh))) == 2


# --------------------------------------------------------------------------
# error paths
# --------------------------------------------------------------------------


def test_command_error_captures_stderr(tmp_path: Path) -> None:
    client = CoClient(
        bin_path=str(FAKE_CO),
        env={"FAKE_CO_FAIL_COMMANDS": "commit", "FAKE_CO_STATE": str(tmp_path / "state.json")},
    )
    doc = make_docx(tmp_path / "doc.docx")
    with pytest.raises(CoCommandError) as excinfo:
        client.commit(str(doc), "boom")
    assert excinfo.value.returncode == 1
    assert "fake failure: commit" in excinfo.value.stderr


def test_command_error_message_includes_command(tmp_path: Path) -> None:
    client = CoClient(
        bin_path=str(FAKE_CO),
        env={"FAKE_CO_FAIL_COMMANDS": "log", "FAKE_CO_STATE": str(tmp_path / "state.json")},
    )
    doc = make_docx(tmp_path / "doc.docx")
    with pytest.raises(CoCommandError) as excinfo:
        client.log(str(doc))
    assert "co log failed" in str(excinfo.value)


def test_commit_requires_message(client: CoClient, tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "doc.docx")
    with pytest.raises(CoCommandError):
        client.commit(str(doc), "")
