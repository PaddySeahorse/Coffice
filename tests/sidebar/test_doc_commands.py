"""Unit tests for the document-command execution (coffice.sidebar.doc_commands).

The extension's native panel buttons and (future) JS postMessage bridge both
route through :func:`coffice.sidebar.doc_commands.execute`, so the behaviour
here is the shell's end of the hosting contract.
"""

from __future__ import annotations

from typing import Any

from coffice.sidebar import doc_commands
from coffice.sidebar.contract import make_result


class FakeBridge:
    """Records calls; mimics the UNO bridge interface of the extension."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.open_path_result: str | None = "doc:1"
        self.prompt_open_result: str | None = None
        self.save_result: str | None = None

    def open_path(self, path: str) -> str | None:
        self.calls.append(("open_path", path))
        return self.open_path_result

    def prompt_open(self) -> str | None:
        self.calls.append(("prompt_open",))
        return self.prompt_open_result

    def focus(self) -> None:
        self.calls.append(("focus",))

    def save(self, path: str | None = None) -> str | None:
        self.calls.append(("save", path))
        return path if path is not None else self.save_result

    def current_doc_id(self) -> str | None:
        self.calls.append(("current_doc_id",))
        return "doc:current"


def test_open_doc_with_path():
    bridge = FakeBridge()
    result = doc_commands.execute("openDoc", {"path": "/tmp/report.docx"}, bridge)
    assert result == {
        "ok": True,
        "result": {"path": "/tmp/report.docx", "docId": "doc:1"},
        "error": None,
    }
    assert bridge.calls == [("open_path", "/tmp/report.docx")]


def test_open_doc_prompts_when_no_path():
    bridge = FakeBridge()
    result = doc_commands.execute("openDoc", {}, bridge)
    assert result["ok"] is True
    assert result["result"]["path"] is None
    assert bridge.calls == [("prompt_open",)]


def test_open_doc_rejects_blank_path():
    bridge = FakeBridge()
    result = doc_commands.execute("openDoc", {"path": "   "}, bridge)
    assert result["ok"] is False
    assert result["error"] and "path" in result["error"]
    assert bridge.calls == []


def test_focus():
    bridge = FakeBridge()
    result = doc_commands.execute("focus", {}, bridge)
    assert result["ok"] is True
    assert result["result"] == {"docId": "doc:current"}
    assert bridge.calls == [("focus",), ("current_doc_id",)]


def test_save_with_path():
    bridge = FakeBridge()
    result = doc_commands.execute("save", {"path": "/tmp/out.docx"}, bridge)
    assert result == {
        "ok": True,
        "result": {"path": "/tmp/out.docx"},
        "error": None,
    }
    assert bridge.calls == [("save", "/tmp/out.docx")]


def test_save_current():
    bridge = FakeBridge()
    result = doc_commands.execute("save", {}, bridge)
    assert result["ok"] is True
    assert bridge.calls == [("save", None)]


def test_unknown_command_is_error():
    bridge = FakeBridge()
    result = doc_commands.execute("deleteEverything", {}, bridge)
    assert result["ok"] is False
    assert "unknown document command" in (result["error"] or "")
    assert bridge.calls == []


def test_command_error_becomes_failure():
    class FailingBridge(FakeBridge):
        def open_path(self, path: str) -> str | None:
            raise doc_commands.CommandError("file is locked")

    result = doc_commands.execute("openDoc", {"path": "/tmp/a.docx"}, FailingBridge())
    assert result["ok"] is False
    assert result["error"] == "file is locked"


def test_unexpected_exception_is_caught():
    class ExplodingBridge(FakeBridge):
        def focus(self) -> None:
            raise RuntimeError("uno bridge exploded")

    result = doc_commands.execute("focus", {}, ExplodingBridge())
    assert result["ok"] is False
    assert result["error"] == "RuntimeError: uno bridge exploded"


def test_result_payload_wraps_into_contract_result():
    bridge = FakeBridge()
    payload = doc_commands.execute("openDoc", {"path": "/tmp/a.docx"}, bridge)
    message = make_result("m42", payload["ok"], result=payload["result"], error=payload["error"])
    assert message["type"] == "coffice.result"
    assert message["id"] == "m42"
    assert message["ok"] is True
    assert message["result"] == {"path": "/tmp/a.docx", "docId": "doc:1"}
