"""Exceptions raised by the routing package."""

from __future__ import annotations


class RoutingError(Exception):
    """Base class for every routing failure."""


class MapboxError(RoutingError):
    """Base class for Mapbox failures. Messages never contain the access token."""

    def __init__(
        self, message: str, *, status_code: int | None = None, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class MapboxTimeoutError(MapboxError):
    """The request timed out."""


class MapboxConnectionError(MapboxError):
    """The request could not reach Mapbox (DNS, refused connection, TLS, ...)."""


class MapboxAuthError(MapboxError):
    """Missing, invalid or unauthorised access token (HTTP 401/403)."""


class MapboxRateLimitError(MapboxError):
    """Rate limit exceeded (HTTP 429). `retry_after` is seconds, if Mapbox said."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, status_code=429, code="RateLimited")
        self.retry_after = retry_after


class MapboxServerError(MapboxError):
    """Mapbox failed on its side (HTTP 5xx)."""


class MapboxNoRouteError(MapboxError):
    """No drivable route between the points (code NoRoute / NoSegment)."""


class MapboxRequestError(MapboxError):
    """Mapbox rejected the request (other 4xx, invalid input, unknown code)."""


class MapboxResponseError(MapboxError):
    """The response was not valid JSON or did not have the expected shape."""
