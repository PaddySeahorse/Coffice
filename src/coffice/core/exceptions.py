"""Typed exceptions for the Coffice LibreOffice core."""


class CofficeError(Exception):
    """Base class for all Coffice errors."""


class LibreOfficeError(CofficeError):
    """Raised when the LibreOffice process or UNO bridge fails."""


class LibreOfficeUnavailableError(LibreOfficeError):
    """Raised when ``soffice`` or PyUNO cannot be found on this machine."""


class DocumentError(CofficeError):
    """Raised for document-level operation failures."""


class DocumentNotFoundError(DocumentError):
    """Raised when a requested document, range, or redline does not exist."""
