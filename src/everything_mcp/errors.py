"""Domain exceptions for Everything_Mew."""


class EverythingMcpError(RuntimeError):
    """Base error for recoverable Everything_Mew failures."""


class BackendUnavailableError(EverythingMcpError):
    """Raised when no configured Everything backend can run the request."""


class QueryError(EverythingMcpError):
    """Raised when a backend reports a malformed or unsupported query."""
