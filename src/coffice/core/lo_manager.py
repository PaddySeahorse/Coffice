"""Lifecycle manager for a long-running headless LibreOffice instance.

Coffice never modifies LibreOffice source or internal files -- all interaction
goes through the UNO bridge (ADR-003). This module owns the single soffice
process for the MVP: starting it headless with a UNO socket listener,
booting UNO via ``uno.getComponentContext()`` + ``UnoUrlResolver``, health
checking/pinging it, gracefully shutting it down, and crash-restarting it.

How LibreOffice + PyUNO are expected to be installed
----------------------------------------------------
PyUNO (the ``uno``/``pyuno`` modules) is *not* installable from PyPI; it ships
with LibreOffice. Two supported setups:

1. System package (Debian/Ubuntu)::

       sudo apt install libreoffice python3-uno

   After this the *system* Python has ``uno`` importable and ``soffice`` on
   PATH, so the package and its tests run under the ordinary interpreter.

2. LibreOffice's bundled interpreter: LibreOffice ships its own Python at
   ``<soffice-install>/program/python`` (Debian: ``/usr/lib/libreoffice/program/python``).
   Running Coffice under that interpreter makes ``uno`` importable with no
   system package. :func:`find_bundled_python` locates it.

Nothing in this module fails at import time when LO is missing. Callers gate
LO-dependent behaviour on :func:`lo_available` / ``LO_UNAVAILABLE`` (the
integration tests in ``tests/core/`` use ``@pytest.mark.skipif(LO_UNAVAILABLE)``).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from coffice.core.exceptions import (
    LibreOfficeError,
    LibreOfficeUnavailableError,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2002

_SOFFICE_CANDIDATES = (
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/usr/lib/libreoffice/program/soffice",
    "/usr/local/lib/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
)

_BUNDLED_PYTHON_CANDIDATES = (
    "/usr/lib/libreoffice/program/python",
    "/usr/local/lib/libreoffice/program/python",
    "/opt/libreoffice/program/python",
)


def _platform_candidates(*relative_paths: str) -> list[Path]:
    """Return installation paths for the current desktop platform."""
    candidates: list[Path] = []
    if os.name == "nt":
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        for root in filter(None, roots):
            candidates.extend(Path(root) / path for path in relative_paths)
    elif sys.platform == "darwin":
        candidates.extend(Path("/Applications") / path for path in relative_paths)
        candidates.extend(Path.home() / "Applications" / path for path in relative_paths)
    return candidates


def _soffice_candidates() -> list[Path]:
    candidates = [Path(path) for path in _SOFFICE_CANDIDATES]
    candidates.extend(
        _platform_candidates(
            "LibreOffice.app/Contents/MacOS/soffice",
            "LibreOffice.app/Contents/MacOS/libreoffice",
            "LibreOffice/program/soffice.exe",
            "LibreOffice/program/soffice.com",
        )
    )
    return candidates


def _bundled_python_candidates() -> list[Path]:
    candidates = [Path(path) for path in _BUNDLED_PYTHON_CANDIDATES]
    candidates.extend(
        _platform_candidates(
            "LibreOffice.app/Contents/MacOS/python",
            "LibreOffice.app/Contents/Resources/python",
            "LibreOffice/program/python.exe",
            "LibreOffice/program/python.bin",
        )
    )
    return candidates


def find_soffice_binary() -> str | None:
    """Locate the ``soffice`` executable.

    Checks ``COFFICE_SOFFICE_BIN`` first, then ``PATH``, then a list of common
    install locations. Returns ``None`` when LibreOffice is not installed.
    """
    env_bin = os.environ.get("COFFICE_SOFFICE_BIN")
    if env_bin and os.path.isfile(env_bin):
        return env_bin
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _soffice_candidates():
        if candidate.is_file():
            return str(candidate)
    return None


def find_bundled_python() -> str | None:
    """Locate the Python interpreter bundled with LibreOffice.

    Returns the path to ``program/python`` inside the LO install, or ``None``.
    """
    candidates = _bundled_python_candidates()
    soffice = find_soffice_binary()
    if soffice:
        resolved = Path(soffice).resolve()
        candidates.insert(0, resolved.parent / "python")
        candidates.insert(0, resolved.parent / "python.bin")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def pyuno_available() -> bool:
    """Return True when the ``uno`` module is importable in this interpreter."""
    try:
        import uno  # type: ignore[import-not-found]  # noqa: F401

        return True
    except ImportError:
        return False


def lo_available() -> bool:
    """Return True when both soffice and PyUNO are usable in this process."""
    return bool(find_soffice_binary()) and pyuno_available()


LO_UNAVAILABLE = not lo_available()

_manager: LoManager | None = None


def get_manager(**kwargs: Any) -> LoManager:
    """Return the process-wide singleton :class:`LoManager`.

    The MVP runs a single soffice instance; all callers share it. Extra
    keyword arguments are only honoured on the first call.
    """
    global _manager
    if _manager is None:
        _manager = LoManager(**kwargs)
    return _manager


class LoManager:
    """Start, ping, and stop one headless LibreOffice process over UNO.

    Parameters
    ----------
    host:
        Interface the UNO socket listener binds to (default ``127.0.0.1``).
    port:
        TCP port for the UNO socket listener (default ``2002``).
    soffice_bin:
        Path to the soffice executable; defaults to :func:`find_soffice_binary`.
    profile_dir:
        Explicit LibreOffice user profile. When omitted a fresh temporary
        profile is created per process start, which avoids lock conflicts from
        crashed instances.
    startup_timeout:
        Seconds to wait for the UNO socket to accept connections.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        soffice_bin: str | None = None,
        profile_dir: str | None = None,
        startup_timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self._soffice_bin = soffice_bin or find_soffice_binary()
        self._profile_dir = profile_dir
        self._startup_timeout = startup_timeout
        self._process: subprocess.Popen[str] | None = None
        self._context: Any = None
        self._owns_profile = False
        self._active_profile_dir: str | None = None
        self._lock = threading.RLock()

    # -- connection metadata -------------------------------------------------

    @property
    def accept_string(self) -> str:
        """The ``--accept`` argument passed to soffice."""
        return f"socket,host={self.host},port={self.port};urp;"

    @property
    def uno_url(self) -> str:
        """The UNO URL the resolver connects to."""
        return f"uno:socket,host={self.host},port={self.port};urp;StarOffice.ComponentContext"

    # -- process health -------------------------------------------------------

    def _port_open(self, timeout: float = 1.0) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    def is_running(self) -> bool:
        """True when the tracked process is alive AND the UNO port is open."""
        if self._process is not None and self._process.poll() is None:
            return self._port_open()
        return False

    def ping(self, timeout: float = 2.0) -> bool:
        """Health-check the instance: process alive, port accepting, and UNO responsive."""
        if self._process is not None and self._process.poll() is not None:
            return False
        if not self._port_open(timeout=timeout):
            return False
        # Verify UNO bridge is actually responsive with a lightweight call
        try:
            self.get_component_context()
            return True
        except Exception:
            return False

    # -- lifecycle ------------------------------------------------------------

    def _build_command(self, profile_dir: str) -> list[str]:
        if not self._soffice_bin or not os.path.isfile(self._soffice_bin):
            raise LibreOfficeUnavailableError(
                "soffice binary not found. Install LibreOffice "
                "(e.g. `sudo apt install libreoffice` on Debian/Ubuntu) or set "
                "COFFICE_SOFFICE_BIN to the soffice executable path."
            )
        return [
            self._soffice_bin,
            "--headless",
            "--invisible",
            "--norestore",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            f"-env:UserInstallation={Path(profile_dir).resolve().as_uri()}",
            f"--accept={self.accept_string}",
        ]

    def _new_profile_dir(self) -> str:
        if self._profile_dir:
            os.makedirs(self._profile_dir, exist_ok=True)
            return self._profile_dir
        self._owns_profile = True
        return tempfile.mkdtemp(prefix="coffice-lo-")

    def start(self) -> LoManager:
        """Start the soffice process and wait for the UNO socket to come up."""
        if self.ping():
            return self
        if not pyuno_available():
            raise LibreOfficeUnavailableError(
                "PyUNO (`uno`) is not importable in this interpreter. Install "
                "`python3-uno` (e.g. `sudo apt install python3-uno`) or run "
                "Coffice under the LibreOffice-bundled interpreter at "
                f"{find_bundled_python() or '<soffice-install>/program/python'}."
            )
        with self._lock:
            self.stop()
            if not self._soffice_bin or not os.path.isfile(self._soffice_bin):
                raise LibreOfficeUnavailableError(
                    "soffice binary not found. Install LibreOffice "
                    "(e.g. `sudo apt install libreoffice` on Debian/Ubuntu) or set "
                    "COFFICE_SOFFICE_BIN to the soffice executable path."
                )
            profile_dir = self._new_profile_dir()
            self._active_profile_dir = profile_dir
            command = self._build_command(profile_dir)
            try:
                self._process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    text=True,
                )
            except OSError as exc:
                raise LibreOfficeError(f"failed to start soffice: {exc}") from exc
            process = self._process
            assert process is not None
            deadline = time.monotonic() + self._startup_timeout
            while time.monotonic() < deadline:
                if self._port_open():
                    self._context = None
                    return self
                if process.poll() is not None:
                    break
                time.sleep(0.25)
            exit_code = process.poll()
            raise LibreOfficeError(
                f"soffice did not open the UNO socket on {self.host}:{self.port} "
                f"within {self._startup_timeout}s (exited: {exit_code})"
            )

    def stop(self, timeout: float = 10.0) -> None:
        """Shut the instance down gracefully, then kill leftovers if needed."""
        with self._lock:
            try:
                if self._context is not None or self._port_open():
                    desktop = self.get_desktop()
                    desktop.terminate()
            except Exception:
                pass
            if self._process is not None:
                try:
                    self._process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=5.0)
                self._process = None
            self._context = None
            profile = self._active_profile_dir
            self._active_profile_dir = None
            if self._owns_profile and profile:
                shutil.rmtree(profile, ignore_errors=True)
            self._owns_profile = False

    def restart(self) -> LoManager:
        """Stop the current instance (if any) and start a fresh one."""
        self.stop()
        return self.start()

    def ensure_running(self) -> LoManager:
        """Crash-restart: guarantee a healthy instance, starting one if not."""
        if self.ping():
            return self
        with self._lock:
            if self.ping():
                return self
            self.stop()
            return self.start()

    # -- UNO access -----------------------------------------------------------

    def _bootstrap(self) -> Any:
        import uno  # type: ignore[import-not-found]

        local_context = uno.getComponentContext()
        service_manager = local_context.ServiceManager
        resolver = service_manager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local_context
        )
        context = resolver.resolve(self.uno_url)
        if context is None:
            raise LibreOfficeError(f"could not resolve UNO URL: {self.uno_url}")
        return context

    def get_component_context(self) -> Any:
        """Return the UNO component context for the running instance (cached)."""
        if self._context is None:
            self._context = self._bootstrap()
        return self._context

    def get_desktop(self) -> Any:
        """Return the ``com.sun.star.frame.Desktop`` of the running instance."""
        context = self.get_component_context()
        return context.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", context
        )
