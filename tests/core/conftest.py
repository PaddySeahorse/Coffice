"""Shared fixtures for the LibreOffice core integration tests.

Integration tests in this directory are gated with
``@pytest.mark.skipif(LO_UNAVAILABLE, ...)``; the fixtures below are only
instantiated when an actual test requests them, so nothing touches LibreOffice
when it is not installed.
"""

from __future__ import annotations

import socket

import pytest

from coffice.core.lo_manager import LoManager


@pytest.fixture
def free_port() -> int:
    """Return a currently-free TCP port on 127.0.0.1."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def lo_manager(free_port: int) -> LoManager:
    """A running headless LibreOffice instance, torn down after the session."""
    manager = LoManager(port=free_port, startup_timeout=60.0)
    manager.ensure_running()
    try:
        yield manager
    finally:
        manager.stop()


@pytest.fixture(scope="session")
def desktop(lo_manager: LoManager):
    """The ``com.sun.star.frame.Desktop`` of the running LO instance."""
    return lo_manager.get_desktop()
