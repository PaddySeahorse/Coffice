"""co CLI subprocess wrapper (planning doc ch. 14).

Wraps the external ``co`` binary (https://github.com/PaddySeahorse/co), a
Git-style version-control CLI for Office files (.docx/.xlsx/.pptx) that stores
history in a ``.co/`` directory inside the ZIP container and supports
``.co-bundle`` companion export/import.

Installation
------------
Run ``scripts/install_co.sh`` to clone, build (CMake, C++17) and install the
binary, then export the path in your environment::

    export CO_BIN=/path/to/co        # optional; falls back to $PATH

The binary is resolved in this order:

1. ``$CO_BIN`` (set by ``scripts/install_co.sh``)
2. ``$COFFICE_CO_BIN`` (alias)
3. ``co`` on ``PATH``
4. ``~/.local/bin/co``, ``/usr/local/bin/co``

If no binary is found, ``co_available()`` returns ``False`` and every call
raises :class:`CoNotAvailableError` pointing at ``scripts/install_co.sh``.

API
---
The API is stable; the MCP server (``getHistory``/``getDiff``) and the
write-tools middleware (``commit`` snapshots) build on it. All operations raise
typed exceptions (see below), capture stderr for debugging, and return
dataclasses (:class:`CommitInfo`, :class:`DiffEntry`, ...) with a ``to_dict()``
helper for JSON serialization.

Commands ``branch``/``merge``/``tag`` are NOT implemented by the upstream co
binary (which offers ``init commit log status diff checkout gc export import
verify-bundle bundle-merge migrate``). The wrapper keeps these methods for API
stability and raises :class:`CoUnsupportedError` when the installed binary does
not advertise the command; the fake test binary emulates them so the full flow
is exercised in unit tests.

Audit fields (planning doc 8.4)
------------------------------
Each commit records:

- author  -> ``CO_AUTHOR_NAME`` (e.g. ``"AI-Writer"`` or ``"human:张三"``)
- message -> ``-m``; use :func:`compose_commit_message` to attach a
  ``human_operator`` as a ``Coffice-Operator:`` trailer line
- timestamp -> commit time, surfaced by ``co log`` and normalized to ISO-8601
  in :class:`CommitInfo.timestamp`

``co log`` does not support ``--json``; the wrapper probes once and falls back
to parsing the git-style text output.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Stable, machine-readable warning surfaced when a document export keeps .co/
#: history inside the file (ADR-005).
INCLUDE_CO_WARNING = (
    "Version history inside the file will be lost if opened with Word/WPS; "
    "recommended to also export a .co-bundle"
)

_INSTALL_HINT = (
    "The co CLI is not available. Install it with: bash scripts/install_co.sh "
    "(or point CO_BIN at the co binary)."
)


# ============================================================================
# Exceptions
# ============================================================================


class CoError(Exception):
    """Base class for all co wrapper errors."""


class CoNotAvailableError(CoError):
    """The co binary could not be found or executed."""


class CoUnsupportedError(CoError):
    """The installed co binary does not support the requested command.

    Attributes:
        command: the unsupported command name.
        supported: the commands the binary does advertise (or ``None`` if the
            binary could not be queried).
    """

    def __init__(self, command: str, supported: frozenset[str] | None = None) -> None:
        self.command = command
        self.supported = supported
        msg = f"co does not support the '{command}' command."
        if supported:
            msg += f" Supported commands: {', '.join(sorted(supported))}."
        msg += (
            " The upstream co CLI (https://github.com/PaddySeahorse/co) "
            "does not implement merge/tag."
        )
        super().__init__(msg)


class CoCommandError(CoError):
    """A co invocation failed (non-zero exit or unparseable output).

    Attributes:
        command: the co subcommand that failed (e.g. ``"commit"``).
        cmd_args: the full argument list (excluding the binary path).
        returncode: process exit code.
        stdout: captured stdout (may be ``""``).
        stderr: captured stderr (may be ``""``) — useful for debugging.
    """

    def __init__(
        self,
        command: str,
        args: Sequence[str],
        returncode: int,
        stdout: str = "",
        stderr: str = "",
        detail: str = "",
    ) -> None:
        self.command = command
        self.cmd_args = list(args)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        msg = f"co {command} failed (exit {returncode})"
        if detail:
            msg += f": {detail}"
        if stderr.strip():
            msg += f"\nstderr: {stderr.strip()}"
        if stdout.strip():
            msg += f"\nstdout: {stdout.strip()}"
        super().__init__(msg)


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass(frozen=True)
class CommitInfo:
    """A single commit from ``co log``."""

    hash: str
    author: str = ""
    email: str = ""
    message: str = ""
    #: ISO-8601 UTC timestamp, or None when co reported no date.
    timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiffEntry:
    """A changed zip-internal path from ``co diff``."""

    status: str  #: A (added), D (deleted), M (modified)
    path: str  #: zip-internal path, e.g. "word/document.xml"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CheckoutResult:
    """Result of ``co checkout``."""

    hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BranchResult:
    """Result of ``co branch`` (emulated by the fake binary)."""

    name: str
    hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MergeResult:
    """Result of ``co merge`` (emulated by the fake binary)."""

    branch: str
    message: str
    hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TagResult:
    """Result of ``co tag`` (emulated by the fake binary)."""

    name: str
    hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExportResult:
    """Result of ``co export`` + document copy handling (ADR-005)."""

    out_path: str  #: path of the exported document copy
    bundle_path: str  #: path of the written .co-bundle companion
    include_co: bool  #: whether the exported document retains .co/
    warning: str = ""  #: non-empty when include_co is True (ADR-005)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImportResult:
    """Result of ``co import``."""

    bundle_path: str
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compose_commit_message(message: str, human_operator: str | None = None) -> str:
    """Attach a ``Coffice-Operator:`` trailer so audit field 8.4 survives.

    co has no dedicated human-operator field; we record it as a trailer line
    so it is visible in ``co log`` output.
    """
    if not human_operator:
        return message
    return f"{message.rstrip()}\n\nCoffice-Operator: {human_operator}"


# ============================================================================
# Binary discovery / feature detection
# ============================================================================


def resolve_bin_path() -> str | None:
    """Locate the co binary (env CO_BIN/COFFICE_CO_BIN, PATH, common dirs)."""
    for env_var in ("CO_BIN", "COFFICE_CO_BIN"):
        candidate = os.environ.get(env_var)
        if candidate and os.path.isfile(candidate):
            return candidate
    for name in ("co", "co.exe"):
        found = shutil.which(name)
        if found:
            return found
    common_paths = [Path.home() / ".local/bin/co", Path("/usr/local/bin/co")]
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            common_paths.extend(
                [
                    Path(local_app_data) / "co" / "co.exe",
                    Path(local_app_data) / "bin" / "co.exe",
                ]
            )
        common_paths.extend(
            [Path.home() / ".local/bin/co.exe", Path.home() / "AppData/Local/co/co.exe"]
        )
    for common in common_paths:
        if common.is_file():
            return str(common)
    return None


def co_available(bin_path: str | None = None) -> bool:
    """Return True when a working co binary is installed.

    ``bin_path`` overrides discovery (used by tests and the integration-test
    gate ``@pytest.mark.skipif(not co_available(), ...)``).
    """
    path = bin_path or resolve_bin_path()
    if not path or not os.path.isfile(path) or (
        os.name != "nt" and not os.access(path, os.X_OK)
    ):
        return False
    try:
        proc = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def require_co(bin_path: str | None = None) -> str:
    """Return the co binary path or raise :class:`CoNotAvailableError`."""
    path = bin_path or resolve_bin_path()
    if not path:
        raise CoNotAvailableError(_INSTALL_HINT)
    if not os.path.isfile(path) or (os.name != "nt" and not os.access(path, os.X_OK)):
        raise CoNotAvailableError(f"co binary not executable: {path}. {_INSTALL_HINT}")
    return path


# ============================================================================
# Client
# ============================================================================


_HASH_RE = re.compile(r"([0-9a-fA-F]{7,64})")
_COMMIT_RE = re.compile(r"^Committed ([0-9a-fA-F]{7,64})")
_CHECKOUT_RE = re.compile(r"^Checked out ([0-9a-fA-F]{7,64})")
_BRANCH_RE = re.compile(r"^Created branch (\S+)")
_TAG_RE = re.compile(r"^Created tag (\S+) at ([0-9a-fA-F]{7,64})")
_MERGE_HASH_RE = re.compile(r"commit:?\s+([0-9a-fA-F]{7,64})")
_LOG_COMMIT_RE = re.compile(r"^commit ([0-9a-fA-F]{7,64})\s*$")
_LOG_AUTHOR_RE = re.compile(r"^Author:\s*(.*?)\s*<([^>]*)>\s*$")
_LOG_DATE_RE = re.compile(r"^Date:\s*(.+?)\s*$")
_CAPABILITY_RE = re.compile(r"^  ([a-z][a-z-]*)\s")


def _to_iso(timestamp: str) -> str | None:
    """Normalize an RFC-1123 (or ISO) timestamp string to ISO-8601 UTC."""
    if not timestamp:
        return None
    try:
        dt = parsedate_to_datetime(timestamp)
    except (TypeError, ValueError, IndexError, OverflowError):
        return timestamp
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


class CoClient:
    """Subprocess wrapper around the co CLI.

    Args:
        bin_path: explicit binary path; defaults to :func:`resolve_bin_path`.
        env: extra environment variables merged over ``os.environ`` for every
            subprocess call (e.g. to point the fake test binary at state).
        timeout: per-call subprocess timeout in seconds.
    """

    def __init__(
        self,
        bin_path: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._bin_path = bin_path
        self._env = dict(env or {})
        self._timeout = timeout
        self._capabilities: frozenset[str] | None = None
        self._json_supported: bool | None = None

    # -- internals ----------------------------------------------------------

    def _binary(self) -> str:
        if self._bin_path:
            if not os.path.isfile(self._bin_path) or (
                os.name != "nt" and not os.access(self._bin_path, os.X_OK)
            ):
                raise CoNotAvailableError(
                    f"co binary not executable: {self._bin_path}. {_INSTALL_HINT}"
                )
            return self._bin_path
        return require_co()

    def _run(
        self,
        command: str,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        binary = self._binary()
        cmd = [binary, command, *args]
        merged_env = {**os.environ, **self._env, **(env or {})}
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=merged_env,
            )
        except FileNotFoundError as exc:
            raise CoNotAvailableError(
                f"failed to execute co binary {binary!r}: {exc}. {_INSTALL_HINT}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CoCommandError(
                command, args, -1, stderr=f"timed out after {self._timeout}s"
            ) from exc
        if proc.returncode != 0:
            raise CoCommandError(command, args, proc.returncode, proc.stdout, proc.stderr)
        return proc

    def _finish(
        self,
        command: str,
        args: Sequence[str],
        proc: subprocess.CompletedProcess[str],
        pattern: re.Pattern[str],
    ) -> str:
        match = pattern.search(proc.stdout or "")
        if not match:
            raise CoCommandError(command, args, 0, proc.stdout, proc.stderr, "unexpected output")
        return match.group(1)

    def capabilities(self) -> frozenset[str]:
        """Return the set of commands the installed binary advertises."""
        if self._capabilities is None:
            proc = self._run("--help", [])
            caps: set[str] = set()
            in_commands = False
            for line in (proc.stdout or "").splitlines():
                if line.startswith("Commands:"):
                    in_commands = True
                    continue
                if in_commands:
                    match = _CAPABILITY_RE.match(line)
                    if match:
                        caps.add(match.group(1))
                        continue
                    if line and not line.startswith("  "):
                        in_commands = False
            self._capabilities = frozenset(caps)
        return self._capabilities

    def supports(self, command: str) -> bool:
        return command in self.capabilities()

    # -- commands -----------------------------------------------------------

    def init(self, path: str) -> str:
        """Initialize a .co/ repository inside an Office file."""
        proc = self._run("init", [path])
        return (proc.stdout or "").strip()

    def commit(
        self,
        path: str,
        message: str,
        author: str | None = None,
        human_operator: str | None = None,
        author_email: str | None = None,
        bundle: str | None = None,
    ) -> str:
        """Create a snapshot commit; returns the commit hash.

        Args:
            path: the .docx/.xlsx/.pptx file to commit.
            message: commit message. Pass through
                :func:`compose_commit_message` to record a ``human_operator``.
            author: value for ``CO_AUTHOR_NAME`` (audit field 8.4), e.g.
                ``"AI-Writer"`` or ``"human:张三"``.
            human_operator: appended as a ``Coffice-Operator:`` trailer.
            author_email: value for ``CO_AUTHOR_EMAIL``.
            bundle: optional ``.co-bundle`` to use as external history store.
        """
        args = ["-m", compose_commit_message(message, human_operator), path]
        if bundle:
            args += ["--external", bundle]
        env: dict[str, str] = {}
        if author:
            env["CO_AUTHOR_NAME"] = author
        if author_email:
            env["CO_AUTHOR_EMAIL"] = author_email
        proc = self._run("commit", args, env=env)
        return self._finish("commit", args, proc, _COMMIT_RE)

    def checkout(
        self, path: str, hash: str, bundle: str | None = None
    ) -> CheckoutResult:
        """Restore the Office file contents to a specific commit."""
        args = [hash, path]
        if bundle:
            args += ["--external", bundle]
        proc = self._run("checkout", args)
        resolved = self._finish("checkout", args, proc, _CHECKOUT_RE)
        return CheckoutResult(hash=resolved)

    def log(
        self, path: str, limit: int | None = None, bundle: str | None = None
    ) -> list[CommitInfo]:
        """Return commit history (newest first) as :class:`CommitInfo` objects.

        ``co`` has no ``--json`` or ``--limit``; the wrapper probes ``--json``
        once and otherwise parses the git-style text output, applying ``limit``
        client-side.
        """
        if self._json_supported is None:
            self._json_supported = self._probe_json(path, bundle)
        if self._json_supported:
            args = ["--json", path]
            if bundle:
                args += ["--external", bundle]
            proc = self._run("log", args)
            return self._parse_log_json(proc.stdout, limit)
        # Fallback: text output is brittle; log a warning so operators know
        # history parsing may be incomplete if the upstream binary changes format.
        logger.warning(
            "co log --json not supported; falling back to text parsing which "
            "may break if the upstream binary changes its output format"
        )
        args = [path]
        if bundle:
            args += ["--external", bundle]
        proc = self._run("log", args)
        return self._parse_log_text(proc.stdout, limit)

    def _probe_json(self, path: str, bundle: str | None) -> bool:
        args = ["--json", path]
        if bundle:
            args += ["--external", bundle]
        try:
            proc = self._run("log", args)
            return _looks_like_json(proc.stdout)
        except CoCommandError:
            return False

    def diff(self, path: str, h1: str, h2: str, bundle: str | None = None) -> list[DiffEntry]:
        """Compare two commits; returns changed zip-internal paths (A/D/M)."""
        args = [h1, h2, path]
        if bundle:
            args += ["--external", bundle]
        proc = self._run("diff", args)
        entries: list[DiffEntry] = []
        for line in (proc.stdout or "").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("No changes"):
                continue
            if len(stripped) >= 2 and stripped[1] == " ":
                entries.append(DiffEntry(status=stripped[0], path=stripped[2:]))
        return entries

    def branch(self, path: str, name: str) -> BranchResult:
        """Create a branch."""
        args = [name, path]
        proc = self._run("branch", args)
        match = _BRANCH_RE.search(proc.stdout or "")
        if not match:
            raise CoCommandError("branch", args, 0, proc.stdout, proc.stderr, "unexpected output")

        # Fetch the head commit hash (co branch output does not include it)
        try:
            commits = self.log(path, limit=1)
            hash_ = commits[0].hash if commits else ""
        except CoError:
            hash_ = ""

        return BranchResult(name=match.group(1), hash=hash_)

    def merge(self, path: str, branch: str) -> MergeResult:
        """Merge a branch into the current one. Unsupported upstream."""
        self._require_command("merge")
        args = [path, branch]
        proc = self._run("merge", args)
        stdout = (proc.stdout or "").strip()
        match = _MERGE_HASH_RE.search(stdout)
        return MergeResult(branch=branch, message=stdout, hash=match.group(1) if match else None)

    def tag(self, path: str, name: str) -> TagResult:
        """Create a tag. Unsupported by the upstream binary."""
        self._require_command("tag")
        args = [path, name]
        proc = self._run("tag", args)
        match = _TAG_RE.search(proc.stdout or "")
        if not match:
            raise CoCommandError("tag", args, 0, proc.stdout, proc.stderr, "unexpected output")
        return TagResult(name=match.group(1), hash=match.group(2))

    def export(
        self,
        path: str,
        include_co: bool = True,
        out_path: str | None = None,
        bundle_path: str | None = None,
    ) -> ExportResult:
        """Export the document honoring ADR-005.

        Always writes a ``.co-bundle`` companion. When ``include_co`` is False
        the exported copy at ``out_path`` has ``.co/`` stripped (PURE export,
        100% editor-compatible) and no warning is returned. When True the copy
        keeps history and the ADR-005 warning is returned.
        """
        out_path = out_path or path
        bundle_path = bundle_path or f"{out_path}.co-bundle"
        if include_co:
            self._run("export", [path, "--output", bundle_path])
            if os.path.abspath(out_path) != os.path.abspath(path):
                shutil.copy2(path, out_path)
            return ExportResult(
                out_path=out_path,
                bundle_path=bundle_path,
                include_co=True,
                warning=INCLUDE_CO_WARNING,
            )
        args = [path, "--output", bundle_path, "--clean", "--clean-output", out_path]
        self._run("export", args)
        return ExportResult(out_path=out_path, bundle_path=bundle_path, include_co=False)

    def import_bundle(
        self, path: str, bundle_path: str, force: bool = False
    ) -> ImportResult:
        """Restore history from a companion .co-bundle onto a document.

        ``co`` verifies the bundle's recorded source SHA-256 against the file
        and refuses to import when it does not match (e.g. after Word/WPS
        rewrote the file — ADR-005). Pass ``force=True`` to import anyway.
        """
        args = [path, bundle_path]
        if force:
            args.append("--force")
        proc = self._run("import", args)
        stdout = (proc.stdout or "").strip()
        warning = ""
        if "WARNING" in stdout.upper():
            warning = stdout
        return ImportResult(bundle_path=bundle_path, warning=warning)

    # -- parsing helpers ----------------------------------------------------

    def _require_command(self, command: str) -> None:
        if not self.supports(command):
            raise CoUnsupportedError(command, self.capabilities())

    def _parse_log_text(self, stdout: str, limit: int | None) -> list[CommitInfo]:
        commits: list[CommitInfo] = []
        current: dict[str, Any] | None = None
        message_lines: list[str] = []
        for line in (stdout or "").splitlines():
            commit_match = _LOG_COMMIT_RE.match(line)
            if commit_match:
                if current is not None:
                    commits.append(self._build_commit(current, message_lines))
                current = {"hash": commit_match.group(1)}
                message_lines = []
                continue
            if current is None:
                continue
            author_match = _LOG_AUTHOR_RE.match(line)
            if author_match:
                current["author"] = author_match.group(1).strip()
                current["email"] = author_match.group(2).strip()
                continue
            date_match = _LOG_DATE_RE.match(line)
            if date_match:
                current["timestamp"] = _to_iso(date_match.group(1).strip())
                continue
            if line.startswith("    "):
                message_lines.append(line[4:])
                continue
            # Skip empty lines between message paragraphs
            if line == "" and message_lines and message_lines[-1] == "":
                continue
        if current is not None:
            commits.append(self._build_commit(current, message_lines))
        if limit is not None:
            commits = commits[:limit]
        if not commits and stdout.strip():
            # Non-empty output but no commits parsed -- log for debugging
            logger.warning(
                "co log text parsing returned 0 commits; output may have "
                "changed format. Raw output (first 500 chars): %s",
                stdout[:500],
            )
        return commits

    @staticmethod
    def _build_commit(data: Mapping[str, Any], message_lines: list[str]) -> CommitInfo:
        message = "\n".join(message_lines).strip()
        return CommitInfo(
            hash=str(data.get("hash", "")),
            author=str(data.get("author", "")),
            email=str(data.get("email", "")),
            message=message,
            timestamp=data.get("timestamp"),
        )

    @staticmethod
    def _parse_log_json(stdout: str, limit: int | None) -> list[CommitInfo]:
        data = json.loads(stdout)
        commits: list[CommitInfo] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            commits.append(
                CommitInfo(
                    hash=str(entry.get("hash") or entry.get("commit") or ""),
                    author=str(entry.get("author") or entry.get("authorName") or ""),
                    email=str(entry.get("email") or entry.get("authorEmail") or ""),
                    message=str(entry.get("message") or ""),
                    timestamp=_to_iso(str(entry.get("timestamp") or "")),
                )
            )
        if limit is not None:
            commits = commits[:limit]
        return commits


def _looks_like_json(text: str) -> bool:
    try:
        json.loads(text or "")
    except json.JSONDecodeError:
        return False
    return True


def default_client() -> CoClient:
    """Create a client using the standard binary discovery."""
    return CoClient()


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
