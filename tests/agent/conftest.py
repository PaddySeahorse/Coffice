"""Shared fixtures for the agent-loop unit tests (no LibreOffice required).

The document side reuses the in-memory :class:`FakeWriteDocument` and the
real :class:`CoClient` against ``tests/versioning/fake_co.py`` from the
write-tools tests, so subprocess invocations (the pre-round snapshot commit,
reject rollback checkout) are observable through ``FAKE_CO_RECORD``. The LLM
side is a :class:`ScriptedLLM` returning scripted tool calls / final text.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from coffice.agent import AgentSession, ChatMessage, LLMClient, ToolCall
from coffice.governance import AuditLog
from coffice.mcp import create_server
from coffice.mcp.middleware import RoundTracker
from coffice.mcp.registry import DocumentRegistry
from coffice.versioning.co_client import CoClient

FAKE_CO = Path(__file__).resolve().parent.parent / "versioning" / "fake_co.py"


class ScriptedLLM:
    """Fake chat-completer returning one scripted step per invocation."""

    def __init__(self, steps: list[ChatMessage] | None = None) -> None:
        self._steps = list(steps or [])
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    @property
    def steps(self) -> list[ChatMessage]:
        return self._steps

    @steps.setter
    def steps(self, value: list[ChatMessage]) -> None:
        self._steps = list(value)

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> ChatMessage:
        self.calls.append((list(messages), list(tools) if tools else None))
        if not self._steps:
            return ChatMessage(content="(no more scripted steps)")
        return self._steps.pop(0)

    def tool_names_seen(self) -> list[str]:
        """The tool names offered to the model in the last call."""
        if not self.calls:
            return []
        tools = self.calls[-1][1] or []
        return [t["function"]["name"] for t in tools if t.get("type") == "function"]


def tool_call(tool_call_id: str, name: str, **arguments: Any) -> ChatMessage:
    return ChatMessage(tool_calls=[ToolCall(id=tool_call_id, name=name, arguments=arguments)])


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
def doc(tmp_path: Path):
    """An in-memory document with a real on-disk path (so co commits work)."""
    from tests.mcp.test_tools_write import FakeWriteDocument

    path = tmp_path / "report.docx"
    path.write_text("", encoding="utf-8")
    return FakeWriteDocument(text="", path=str(path))


@pytest.fixture()
def registry(doc) -> DocumentRegistry:
    return DocumentRegistry(
        doc_path=doc._path,  # noqa: SLF001
        factory=lambda doc_id, path: doc,
    )


@pytest.fixture()
def round_tracker() -> RoundTracker:
    return RoundTracker()


@pytest.fixture()
def audit_log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


class _AgentFakeProjector:
    """Minimal stand-in for DiffProjector: returns one virtual redline so the
    agent-loop acceptance test can assert "one redline group" without
    LibreOffice or a real co checkout."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def redlines(
        self,
        path: str,
        current_text: str,
        baseline_hash: str | None = None,
        author: str = "AI-Writer",
    ) -> list[dict[str, Any]]:
        self.calls.append(("redlines", (path, current_text)))
        if not current_text:
            return []
        return [
            {
                "id": "h0_insert_agentdemo",
                "type": "insert",
                "author": author,
                "baseline_range": {"start": 0, "end": 0},
                "current_range": {"start": 0, "end": len(current_text)},
                "baseline_text": "",
                "current_text": current_text,
            }
        ]

    def rebuild_after_reject(
        self,
        path: str,
        current_text: str,
        rejected_ids: set[str],
        baseline_hash: str | None = None,
    ) -> str:
        self.calls.append(("rebuild_after_reject", (path, set(rejected_ids))))
        return current_text


@pytest.fixture()
def fake_projector() -> _AgentFakeProjector:
    return _AgentFakeProjector()


@pytest.fixture()
def agent_server(
    registry: DocumentRegistry,
    co_client: CoClient,
    round_tracker: RoundTracker,
    audit_log: AuditLog,
    fake_projector: _AgentFakeProjector,
):
    """FastMCP server wired with the fake doc/co/projector and an audit log."""
    return create_server(
        registry=registry,
        co_client=co_client,
        round_tracker=round_tracker,
        audit_log=audit_log,
        projector=fake_projector,
    )


@pytest.fixture()
def session_factory(agent_server, round_tracker: RoundTracker):
    """Build an AgentSession sharing the server's tracker with scripted steps."""

    def _make(
        steps: list[ChatMessage] | None = None,
        human_operator: str | None = "alice",
        max_tool_calls: int = 24,
    ) -> tuple[AgentSession, ScriptedLLM]:
        llm = ScriptedLLM(steps)
        session = AgentSession(
            llm_client=llm,
            server=agent_server,
            round_tracker=round_tracker,
            human_operator=human_operator,
            max_tool_calls=max_tool_calls,
        )
        return session, llm

    return _make


@pytest.fixture()
def llm_client() -> LLMClient:
    return LLMClient(base_url="http://llm.test/v1", model="fake-model")
