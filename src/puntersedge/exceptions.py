"""Exception types raised by the PuntersEdge client."""


class PuntersEdgeError(Exception):
    """Base error for all PuntersEdge API failures."""

    def __init__(self, message, status_code=None, response=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response

    # `self.response` is a live `requests.Response`, and `response.request.headers` holds
    # the `X-API-Key` header. `str()` and `repr()` of this exception are clean, which is
    # exactly what made the leak easy to miss — but serialising it is not:
    # `pickle.dumps(exc)` was verified (2026-08-20, sentinel key) to contain the key in
    # plaintext. An exception travels further than anyone expects: Celery result queues,
    # multiprocessing, joblib caches, crash reporters.
    #
    # The client also scrubs the header off the request before constructing the error, so
    # this is the second of two independent defences. Keep both — the scrub protects live
    # attribute access, this protects anything that serialises.
    def __getstate__(self):
        state = dict(self.__dict__)
        state["response"] = None
        return state

    def __reduce__(self):
        return (_rebuild_error, (type(self), self.args, self.status_code))


def _rebuild_error(cls, args, status_code):
    """Unpickle helper — reconstructs without the response object."""
    exc = cls(*args) if args else cls("")
    exc.status_code = status_code
    exc.response = None
    return exc


class AuthenticationError(PuntersEdgeError):
    """Raised on 401/403 — missing, invalid, or unauthorised API key."""


class RateLimitError(PuntersEdgeError):
    """Raised on 429 — monthly credit cap or per-minute rate limit hit."""


class NotFoundError(PuntersEdgeError):
    """Raised on 404 — unknown sport key, race, or resource."""


class ServerError(PuntersEdgeError):
    """Raised on 5xx — transient API/server-side error (retried automatically)."""


class ConfigError(PuntersEdgeError):
    """Raised when a config file is missing at an explicit path, unreadable, malformed,
    or contains a section or option this package refuses to hold."""


class ApiKeyError(ConfigError):
    """Raised when no API key could be resolved from any source.

    Named `ApiKeyError`, not `CredentialError`. The generic noun has no ceiling: a
    package with a `CredentialError` grows a place to put bookmaker logins, and this
    package must never hold one. The specific name says what it holds — one API key.
    """
