"""LibreOffice/PyUNO bridge: soffice lifecycle manager and Document wrapper (planning doc ch. 5).

Public API
----------
- :class:`LoManager` -- start/ping/stop a single headless soffice over UNO.
- :class:`Document` -- high-level operations on a loaded Writer document.
- :func:`get_manager` -- process-wide singleton :class:`LoManager` (single instance for MVP).
- ``LO_UNAVAILABLE`` / :func:`lo_available` -- gate LO-dependent behaviour
  (integration tests use ``@pytest.mark.skipif(LO_UNAVAILABLE)``).
"""

from coffice.core.document import Document
from coffice.core.exceptions import (
    CofficeError,
    DocumentError,
    DocumentNotFoundError,
    LibreOfficeError,
    LibreOfficeUnavailableError,
)
from coffice.core.lo_manager import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    LO_UNAVAILABLE,
    LoManager,
    find_bundled_python,
    find_soffice_binary,
    get_manager,
    lo_available,
    pyuno_available,
)

__all__ = [
    "CofficeError",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "LO_UNAVAILABLE",
    "Document",
    "DocumentError",
    "DocumentNotFoundError",
    "LibreOfficeError",
    "LibreOfficeUnavailableError",
    "LoManager",
    "find_bundled_python",
    "find_soffice_binary",
    "get_manager",
    "lo_available",
    "pyuno_available",
]
