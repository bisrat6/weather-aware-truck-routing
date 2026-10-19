"""Open-Meteo forecast client. Pure Python, no Django imports.

One request covers many points (comma-separated coordinates). Units are fixed to mph and
inches, time to UTC. The response units are checked, so a unit mix-up fails loudly instead
of producing wrong risk levels.

Variables (names confirmed in the official docs, https://open-meteo.com/en/docs):
wind_speed_10m, rain, showers, snowfall. Gusts are deliberately not requested: the project
guide uses sustained 10 m wind.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import requests

from weather.errors import (
    WeatherConnectionError,
    WeatherError,
    WeatherRateLimitError,
    WeatherRequestError,
    WeatherResponseError,
    WeatherServerError,
    WeatherTimeoutError,
)
from weather.models import (
    VAR_RAIN,
    VAR_SHOWERS,
    VAR_SNOW,
    VAR_WIND,
    HourlySeries,
    HourValues,
    Point,
)

OPEN_METEO_BASE_URL = "https://api.open-meteo.com"
BASE_URL = f"{OPEN_METEO_BASE_URL}/v1/forecast"
HOURLY_VARIABLES: tuple[str, ...] = (VAR_WIND, VAR_RAIN, VAR_SHOWERS, VAR_SNOW)
WIND_SPEED_UNIT = "mph"
PRECIPITATION_UNIT = "inch"
TIMEZONE = "UTC"
MAX_FORECAST_DAYS = 16  # documented range of forecast_days is 0-16
MAX_POINTS_PER_REQUEST = 100  # conservative; the docs do not state a cap
DEFAULT_TIMEOUT: tuple[float, float] = (3.0, 20.0)  # (connect, read) seconds

# What the API echoes back in `hourly_units`. For mph it returns "mp/h" (seen live, not in
# the docs), so both spellings are accepted.
_ACCEPTED_WIND_UNITS = frozenset({"mp/h", "mph"})
_REASON_LIMIT = 300
_LOG_REASON_LIMIT = 200
_RATE_LIMIT_RETRIES = 2
_RATE_LIMIT_BUDGET_SECONDS = 6.0
_RATE_LIMIT_BACKOFF_SECONDS = 1.0
_TRANSIENT_RETRIES = 2  # 5xx or "overloaded": backoff 1 s then 2 s, same budget as 429
_FAILURE_REASON_LIMIT = 100
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_SENSITIVE_PARAMETER_PATTERN = re.compile(
    r"\b(?:access_token|api[_-]?key|token|key|latitude|longitude|lat|lon)=[^\s&]+",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)

_in_flight = 0  # Open-Meteo requests currently running in this process (all threads)
_in_flight_lock = threading.Lock()


def _enter_flight() -> None:
    global _in_flight
    with _in_flight_lock:
        _in_flight += 1


def _leave_flight() -> int:
    """Leave the in-flight set; return how many other requests are still running."""
    global _in_flight
    with _in_flight_lock:
        _in_flight -= 1
        return _in_flight


def _fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def build_params(points: Sequence[Point], forecast_days: int) -> dict[str, str]:
    """Query parameters for one request covering all `points` (lat, lon)."""
    if not points:
        raise ValueError("at least one point is required")
    if not 1 <= forecast_days <= MAX_FORECAST_DAYS:
        raise ValueError(f"forecast_days must be 1-{MAX_FORECAST_DAYS}, got {forecast_days!r}")
    for lat, lon in points:
        if not (math.isfinite(lat) and math.isfinite(lon)):
            raise ValueError(f"point must be finite, got ({lat!r}, {lon!r})")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"point out of range: ({lat!r}, {lon!r})")
    return {
        "latitude": ",".join(_fmt(lat) for lat, _ in points),
        "longitude": ",".join(_fmt(lon) for _, lon in points),
        "hourly": ",".join(HOURLY_VARIABLES),
        "wind_speed_unit": WIND_SPEED_UNIT,
        "precipitation_unit": PRECIPITATION_UNIT,
        "timezone": TIMEZONE,
        "forecast_days": str(forecast_days),
    }


# --------------------------------------------------------------------------- HTTP


def _reason(response: requests.Response) -> str:
    """The provider's `reason` text, or the start of the body, truncated."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("reason"), str):
        raw = body["reason"]
    else:
        raw = response.text or ""
    return _safe_reason(raw)[:_REASON_LIMIT]


def _safe_reason(reason: str) -> str:
    """Keep useful provider text while removing URLs, credentials, and coordinates."""
    without_urls = _URL_PATTERN.sub("[redacted-url]", reason)
    return _SENSITIVE_PARAMETER_PATTERN.sub("[redacted]", without_urls)


def _retry_after_seconds(value: str | None) -> float | None:
    """Read an HTTP Retry-After delta or date; unusable and past values have no hint."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at.astimezone(UTC) - datetime.now(UTC)).total_seconds()
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _log_rate_limit(response: requests.Response, retry_after: float | None) -> None:
    logger.warning(
        "weather provider rate limited provider=%s status=%s retry_after=%s reason=%s",
        "Open-Meteo",
        response.status_code,
        retry_after,
        _reason(response)[:_LOG_REASON_LIMIT],
    )


def _error_reason(response: requests.Response) -> str | None:
    """The provider's reason if this response is an error, else None."""
    if response.status_code != 200:
        return _reason(response)
    try:
        body = response.json()
    except ValueError:
        return None
    return _reason(response) if isinstance(body, dict) and body.get("error") is True else None


def _is_transient(status: int, reason: str) -> bool:
    return status >= 500 or "overloaded" in reason.casefold()


def _transient_delay(attempt: int, started_at: float) -> float | None:
    """Seconds to wait before retry number attempt + 1, or None when retries are used up."""
    remaining = _RATE_LIMIT_BUDGET_SECONDS - (time.monotonic() - started_at)
    if attempt >= _TRANSIENT_RETRIES or remaining <= 0:
        return None
    return min(_RATE_LIMIT_BACKOFF_SECONDS * 2**attempt, remaining)


def _log_failure(
    exception: str,
    status: int | None,
    reason: str,
    *,
    attempt: int,
    started_at: float,
    locations: int,
    chunk: tuple[int, int],
    other_in_flight: int,
    will_retry: bool = False,
) -> None:
    """One WARNING per failed attempt. Never includes URLs, coordinates or tokens."""
    logger.warning(
        "weather request failed provider=%s exception=%s status=%s attempt=%d/%d "
        "elapsed_s=%.2f locations=%d chunk=%d/%d other_requests_in_flight=%d retry=%s reason=%s",
        "Open-Meteo",
        exception,
        status,
        attempt,
        _TRANSIENT_RETRIES + 1,
        time.monotonic() - started_at,
        locations,
        chunk[0],
        chunk[1],
        other_in_flight,
        will_retry,
        " ".join(_safe_reason(reason).split())[:_FAILURE_REASON_LIMIT],
    )


def _forecast_url(base_url: str) -> str:
    """The forecast endpoint below a configured Open-Meteo host or proxy."""
    return f"{base_url.rstrip('/')}/v1/forecast"


def fetch_raw(
    points: Sequence[Point],
    forecast_days: int,
    *,
    timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
    base_url: str = OPEN_METEO_BASE_URL,
    proxy_token: str | None = None,
    chunk: tuple[int, int] = (1, 1),
) -> object:
    """One GET to Open-Meteo for all `points`; returns the decoded JSON.

    Raises a WeatherError subclass for every failure. Underlying `requests` exceptions are
    not chained, and no message contains the request URL. HTTP 5xx and "overloaded" replies
    are retried twice (1 s, then 2 s) within the same budget as the 429 path. `chunk` is
    (index, total) and only labels log lines. Every failed attempt is logged.
    """
    params = build_params(points, forecast_days)
    request_kwargs = {"headers": {"X-Proxy-Token": proxy_token}} if proxy_token else {}
    started_at = time.monotonic()
    rate_limit_attempt = 0
    transient_attempt = 0
    attempt = 0
    while True:
        attempt += 1
        _enter_flight()
        try:
            response = requests.get(
                _forecast_url(base_url), params=params, timeout=timeout, **request_kwargs
            )
        except requests.RequestException as exc:
            timed_out = isinstance(exc, requests.Timeout)
            # A connect timeout means the request never reached the server, so it is safe to
            # retry. Read timeouts are not retried: they already cost the full read timeout.
            retry_connect = (
                isinstance(exc, requests.ConnectTimeout) and transient_attempt < _TRANSIENT_RETRIES
            )
            _log_failure(
                "WeatherTimeoutError" if timed_out else "WeatherConnectionError",
                None,
                type(exc).__name__,
                attempt=attempt,
                started_at=started_at,
                locations=len(points),
                chunk=chunk,
                other_in_flight=_leave_flight(),
                will_retry=retry_connect,
            )
            if retry_connect:
                time.sleep(_RATE_LIMIT_BACKOFF_SECONDS * 2**transient_attempt)
                transient_attempt += 1
                continue
            if timed_out:
                raise WeatherTimeoutError(
                    f"Open-Meteo request timed out (timeout={timeout})"
                ) from None
            message = f"could not reach Open-Meteo ({type(exc).__name__})"
            raise WeatherConnectionError(message) from None
        others = _leave_flight()

        status = response.status_code
        if status != 429:
            failure = _error_reason(response)
            if failure is not None:
                delay = (
                    _transient_delay(transient_attempt, started_at)
                    if _is_transient(status, failure)
                    else None
                )
                _log_failure(
                    "WeatherServerError" if status >= 500 else "WeatherRequestError",
                    status,
                    failure,
                    attempt=attempt,
                    started_at=started_at,
                    locations=len(points),
                    chunk=chunk,
                    other_in_flight=others,
                    will_retry=delay is not None,
                )
                if delay is not None:
                    time.sleep(delay)
                    transient_attempt += 1
                    continue
            break

        retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
        _log_rate_limit(response, retry_after)
        if "daily" in _reason(response).casefold():
            raise WeatherRateLimitError(
                f"Open-Meteo rate limit exceeded: {_reason(response)}", retry_after=retry_after
            )
        if rate_limit_attempt >= _RATE_LIMIT_RETRIES:
            raise WeatherRateLimitError(
                f"Open-Meteo rate limit exceeded: {_reason(response)}", retry_after=retry_after
            )

        elapsed = time.monotonic() - started_at
        remaining = _RATE_LIMIT_BUDGET_SECONDS - elapsed
        if remaining <= 0:
            raise WeatherRateLimitError(
                f"Open-Meteo rate limit exceeded: {_reason(response)}", retry_after=retry_after
            )
        backoff = _RATE_LIMIT_BACKOFF_SECONDS * 2**rate_limit_attempt
        delay = min(retry_after if retry_after is not None else backoff, remaining)
        time.sleep(delay)
        rate_limit_attempt += 1

    if status >= 500:
        raise WeatherServerError(
            f"Open-Meteo returned HTTP {status}: {_reason(response)}", status_code=status
        )
    if status != 200:
        raise WeatherRequestError(
            f"Open-Meteo rejected the request (HTTP {status}): {_reason(response)}",
            status_code=status,
        )

    try:
        payload = response.json()
    except ValueError:
        _log_failure(
            "WeatherResponseError",
            status,
            "response was not valid JSON",
            attempt=attempt,
            started_at=started_at,
            locations=len(points),
            chunk=chunk,
            other_in_flight=others,
        )
        raise WeatherResponseError("Open-Meteo response was not valid JSON") from None
    if isinstance(payload, dict) and payload.get("error") is True:
        raise WeatherRequestError(
            f"Open-Meteo returned an error: {_reason(response)}", status_code=status
        )
    return payload


# --------------------------------------------------------------------------- parsing


def _number_or_none(value: object) -> float | None:
    """A finite, non-negative number, else None (null or unusable means missing, never 0)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _check_units(units: object) -> None:
    if not isinstance(units, dict):
        raise WeatherResponseError("response has no hourly_units, so units cannot be verified")
    wind = units.get(VAR_WIND)
    if wind not in _ACCEPTED_WIND_UNITS:
        raise WeatherResponseError(f"unexpected unit {wind!r} for {VAR_WIND}; expected mph")
    for variable in (VAR_RAIN, VAR_SHOWERS, VAR_SNOW):
        unit = units.get(variable)
        if unit != PRECIPITATION_UNIT:
            raise WeatherResponseError(
                f"unexpected unit {unit!r} for {variable}; expected {PRECIPITATION_UNIT!r}"
            )


def _parse_times(raw: object) -> tuple[datetime, ...]:
    if not isinstance(raw, list) or not raw:
        raise WeatherResponseError("hourly.time is missing or empty")
    times: list[datetime] = []
    for item in raw:
        try:
            moment = datetime.fromisoformat(str(item))
        except ValueError:
            raise WeatherResponseError(f"unparseable hourly time {item!r}") from None
        # Naive times are UTC because timezone=UTC was requested.
        moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
        if moment.minute or moment.second or moment.microsecond:
            raise WeatherResponseError(f"hourly time {item!r} is not on the hour")
        if times and moment <= times[-1]:
            raise WeatherResponseError("hourly.time is not strictly increasing")
        times.append(moment)
    return tuple(times)


def _parse_location(item: object) -> HourlySeries:
    if not isinstance(item, dict):
        raise WeatherResponseError("location entry is not an object")
    if item.get("utc_offset_seconds", 0) != 0:
        raise WeatherResponseError("response is not in UTC (utc_offset_seconds != 0)")
    _check_units(item.get("hourly_units"))
    hourly = item.get("hourly")
    if not isinstance(hourly, dict):
        raise WeatherResponseError("response has no hourly data")
    times = _parse_times(hourly.get("time"))
    columns: dict[str, list[float | None]] = {}
    for variable in HOURLY_VARIABLES:
        raw = hourly.get(variable)
        if not isinstance(raw, list) or len(raw) != len(times):
            raise WeatherResponseError(
                f"hourly.{variable} is missing or has a different length than hourly.time"
            )
        columns[variable] = [_number_or_none(v) for v in raw]
    values = tuple(
        HourValues(
            wind_mph=columns[VAR_WIND][i],
            rain_in=columns[VAR_RAIN][i],
            showers_in=columns[VAR_SHOWERS][i],
            snow_in=columns[VAR_SNOW][i],
        )
        for i in range(len(times))
    )
    return HourlySeries(times=times, values=values)


def parse_payload(payload: object, n_points: int) -> list[HourlySeries]:
    """Convert a decoded response into one HourlySeries per requested point, in order.

    A single location comes back as an object, several as a list (seen live and in the
    docs); both are accepted. Nulls become None; they are never turned into 0.
    """
    items = payload if isinstance(payload, list) else [payload]
    if len(items) != n_points:
        raise WeatherResponseError(
            f"expected {n_points} location(s) in the response, got {len(items)}"
        )
    return [_parse_location(item) for item in items]


def fetch_hourly(
    points: Sequence[Point],
    forecast_days: int,
    *,
    timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
    base_url: str = OPEN_METEO_BASE_URL,
    proxy_token: str | None = None,
) -> list[HourlySeries]:
    """Hourly forecasts for `points`, one series per point in the same order.

    All points go in a single request, split only when there are more than
    MAX_POINTS_PER_REQUEST. Raises ValueError for an empty list.
    """
    if not points:
        raise ValueError("at least one point is required")
    series: list[HourlySeries] = []
    total = math.ceil(len(points) / MAX_POINTS_PER_REQUEST)
    for number, start in enumerate(range(0, len(points), MAX_POINTS_PER_REQUEST), start=1):
        chunk = points[start : start + MAX_POINTS_PER_REQUEST]
        started_at = time.monotonic()
        payload = fetch_raw(
            chunk,
            forecast_days,
            timeout=timeout,
            base_url=base_url,
            proxy_token=proxy_token,
            chunk=(number, total),
        )
        try:
            series.extend(parse_payload(payload, len(chunk)))
        except WeatherError as exc:
            _log_failure(
                type(exc).__name__,
                200,
                str(exc),
                attempt=1,
                started_at=started_at,
                locations=len(chunk),
                chunk=(number, total),
                other_in_flight=_in_flight,
            )
            raise
    return series
