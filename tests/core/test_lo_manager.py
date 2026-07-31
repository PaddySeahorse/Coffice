"""Unit + integration tests for the soffice lifecycle manager.

Pure-logic tests run everywhere; tests that need a real LibreOffice process
are individually gated with ``@pytest.mark.skipif(LO_UNAVAILABLE)``.
"""

from __future__ import annotations

import pytest

from coffice.core.exceptions import LibreOfficeUnavailableError
from coffice.core.lo_manager import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    LO_UNAVAILABLE,
    LoManager,
    find_soffice_binary,
    lo_available,
    pyuno_available,
)

# -- pure-logic tests (no LibreOffice required) --------------------------------


def test_defaults() -> None:
    manager = LoManager()
    assert manager.host == DEFAULT_HOST
    assert manager.port == DEFAULT_PORT


def test_connection_metadata_format() -> None:
    manager = LoManager(host="10.0.0.5", port=4321)
    assert manager.accept_string == "socket,host=10.0.0.5,port=4321;urp;"
    assert manager.uno_url == (
        "uno:socket,host=10.0.0.5,port=4321;urp;StarOffice.ComponentContext"
    )


def test_find_soffice_binary_prefers_env(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "soffice"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("COFFICE_SOFFICE_BIN", str(fake))
    assert find_soffice_binary() == str(fake)


def test_availability_checks_return_bools() -> None:
    assert isinstance(lo_available(), bool)
    assert isinstance(pyuno_available(), bool)
    assert LO_UNAVAILABLE == (not lo_available())


def test_start_without_binary_raises() -> None:
    manager = LoManager(soffice_bin="/nonexistent/soffice")
    with pytest.raises(LibreOfficeUnavailableError):
        manager.start()


def test_start_with_nonexistent_binary_raises_unavailable_with_pyuno(monkeypatch) -> None:
    monkeypatch.setattr("coffice.core.lo_manager.pyuno_available", lambda: True)
    manager = LoManager(soffice_bin="/nonexistent/soffice")
    with pytest.raises(LibreOfficeUnavailableError):
        manager.start()


# -- integration tests (require LibreOffice + PyUNO) ---------------------------


@pytest.mark.skipif(LO_UNAVAILABLE, reason="LibreOffice/PyUNO not available")
def test_start_ping_stop(free_port: int) -> None:
    manager = LoManager(port=free_port, startup_timeout=60.0)
    try:
        manager.start()
        assert manager.is_running()
        assert manager.ping()
        context = manager.get_component_context()
        assert context is not None
        assert manager.get_desktop() is not None
    finally:
        manager.stop()
    assert not manager.ping()


@pytest.mark.skipif(LO_UNAVAILABLE, reason="LibreOffice/PyUNO not available")
def test_crash_restart(free_port: int) -> None:
    manager = LoManager(port=free_port, startup_timeout=60.0)
    manager.ensure_running()
    assert manager.ping()
    process = manager._process
    assert process is not None
    process.kill()
    process.wait()
    assert not manager.ping()
    manager.ensure_running()
    assert manager.ping()
    manager.stop()
    assert not manager.ping()


@pytest.mark.skipif(LO_UNAVAILABLE, reason="LibreOffice/PyUNO not available")
def test_restart(free_port: int) -> None:
    manager = LoManager(port=free_port, startup_timeout=60.0)
    manager.ensure_running()
    first_port = manager.port
    manager.restart()
    assert manager.port == first_port
    assert manager.ping()
    manager.stop()
