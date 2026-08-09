"""
Custom exceptions for tick-proxy. Prevents stack traces and hides secrets.
"""


class TickProxyError(Exception):
    """Base exception for all tick-proxy errors.

    Raised for configuration problems, payload problems and any TickTick API
    failure that must reach the user as a clean one-line message instead of a
    Python traceback (secrets must never leak through a stack trace).

    Args:
        message (str): Human-readable, actionable error text. Should tell the
            user what to run next when a fix exists.

    Examples:
        >>> raise TickProxyError("Config not found. Run 'tick-proxy admin setup'.")
        TickProxyError: Config not found. Run 'tick-proxy admin setup'.
        >>> str(TickProxyError("V2 session expired"))
        'V2 session expired'
    """

    def __init__(self, message: str):
        super().__init__(message)


class TickTickAPIError(TickProxyError):
    """A TickTick HTTP call failed with a non-recoverable status.

    Args:
        status (int): HTTP status code returned by TickTick (0 when the failure
            happened before any HTTP exchange, e.g. missing credentials).
        message (str): Explanation plus the recommended fix.

    Examples:
        >>> TickTickAPIError(404, "Not found — check project_id").status
        404
        >>> str(TickTickAPIError(429, "Rate limit exceeded"))
        '[429] Rate limit exceeded'
    """

    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"[{status}] {message}")
