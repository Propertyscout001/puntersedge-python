"""Exception types raised by the PuntersEdge client."""


class PuntersEdgeError(Exception):
    """Base error for all PuntersEdge API failures."""

    def __init__(self, message, status_code=None, response=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class AuthenticationError(PuntersEdgeError):
    """Raised on 401/403 — missing, invalid, or unauthorised API key."""


class RateLimitError(PuntersEdgeError):
    """Raised on 429 — monthly credit cap or per-minute rate limit hit."""


class NotFoundError(PuntersEdgeError):
    """Raised on 404 — unknown sport key, race, or resource."""


class ServerError(PuntersEdgeError):
    """Raised on 5xx — transient API/server-side error (retried automatically)."""
