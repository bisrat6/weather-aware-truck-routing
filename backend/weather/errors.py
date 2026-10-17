"""Exceptions raised by the weather package.

Messages never contain the request URL; they carry the provider's own `reason` text
(truncated) where there is one.
"""

from __future__ import annotations


class WeatherError(Exception):
    """Base class for every weather failure."""


class WeatherTimeoutError(WeatherError):
    """The request to Open-Meteo timed out."""


class WeatherConnectionError(WeatherError):
    """Open-Meteo could not be reached (DNS, refused connection, TLS, ...)."""


class WeatherRateLimitError(WeatherError):
    """Open-Meteo rate limit exceeded (HTTP 429). `retry_after` is seconds, if given."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class WeatherRequestError(WeatherError):
    """Open-Meteo rejected the request (HTTP 4xx or an `error: true` body)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WeatherServerError(WeatherError):
    """Open-Meteo failed on its side (HTTP 5xx)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WeatherResponseError(WeatherError):
    """The response was not valid JSON, had the wrong shape, or used unexpected units."""


class MissingWeatherDataError(WeatherError):
    """A needed value is null, absent, or outside the forecast range.

    Raised instead of ever substituting 0. `missing` is the flag object describing what
    was missing (see weather.models.MissingWeather).
    """

    def __init__(self, missing: object) -> None:
        super().__init__(str(missing))
        self.missing = missing
