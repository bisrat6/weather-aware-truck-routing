"""Exceptions raised by the routing package."""

from __future__ import annotations


class RoutingError(Exception):
    """Base class for every routing failure."""


class ProviderError(RoutingError):
    """Base class for routing and geocoding provider failures (never contains the API key)."""

    def __init__(
        self, message: str, *, status_code: int | None = None, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ProviderTimeoutError(ProviderError):
    """The request timed out."""


class ProviderConnectionError(ProviderError):
    """The request could not reach the provider (DNS, refused connection, TLS, ...)."""


class ProviderAuthError(ProviderError):
    """Missing, invalid or unauthorised API key (HTTP 401/403)."""


class ProviderRateLimitError(ProviderError):
    """Rate limit exceeded (HTTP 429). `retry_after` is seconds, if the provider said."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, status_code=429, code="RateLimited")
        self.retry_after = retry_after


class ProviderServerError(ProviderError):
    """The provider failed on its side (HTTP 5xx)."""


class ProviderNoRouteError(ProviderError):
    """No drivable route between the points (no route found)."""


class ProviderRequestError(ProviderError):
    """The provider rejected the request (other 4xx, invalid input, unknown code)."""


class ProviderResponseError(ProviderError):
    """The response was not valid JSON or did not have the expected shape."""
