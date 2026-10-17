"""Open-Meteo forecast client. Pure Python, no Django imports.

One request covers many points (comma-separated coordinates). Units are fixed to mph and
inches, time to UTC. The response units are checked, so a unit mix-up fails loudly instead
of producing wrong risk levels.

Variables (names confirmed in the official docs, https://open-meteo.com/en/docs):
wind_speed_10m, rain, showers, snowfall. Gusts are deliberately not requested: the project
guide uses sustained 10 m wind.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime

import requests

from weather.errors import (
    WeatherConnectionError,
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

BASE_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARIABLES: tuple[str, ...] = (VAR_WIND, VAR_RAIN, VAR_SHOWERS, VAR_SNOW)
WIND_SPEED_UNIT = "mph"
PRECIPITATION_UNIT = "inch"
TIMEZONE = "UTC"
MAX_FORECAST_DAYS = 16  # documented range of forecast_days is 0-16
MAX_POINTS_PER_REQUEST = 100  # conservative; the docs do not state a cap
DEFAULT_TIMEOUT: tuple[float, float] = (5.0, 20.0)  # (connect, read) seconds

# What the API echoes back in `hourly_units`. For mph it returns "mp/h" (seen live, not in
# the docs), so both spellings are accepted.
_ACCEPTED_WIND_UNITS = frozenset({"mp/h", "mph"})
_REASON_LIMIT = 300


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
        return body["reason"][:_REASON_LIMIT]
    return (response.text or "")[:_REASON_LIMIT]


def fetch_raw(
    points: Sequence[Point],
    forecast_days: int,
    *,
    timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
) -> object:
    """One GET to Open-Meteo for all `points`; returns the decoded JSON.

    Raises a WeatherError subclass for every failure. Underlying `requests` exceptions are
    not chained, and no message contains the request URL.
    """
    params = build_params(points, forecast_days)
    try:
        response = requests.get(BASE_URL, params=params, timeout=timeout)
    except requests.Timeout:
        raise WeatherTimeoutError(f"Open-Meteo request timed out (timeout={timeout})") from None
    except requests.RequestException as exc:
        raise WeatherConnectionError(f"could not reach Open-Meteo ({type(exc).__name__})") from None

    status = response.status_code
    if status == 429:
        try:
            retry_after: float | None = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry_after = None
        raise WeatherRateLimitError(
            f"Open-Meteo rate limit exceeded: {_reason(response)}", retry_after=retry_after
        )
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
) -> list[HourlySeries]:
    """Hourly forecasts for `points`, one series per point in the same order.

    All points go in a single request, split only when there are more than
    MAX_POINTS_PER_REQUEST. Raises ValueError for an empty list.
    """
    if not points:
        raise ValueError("at least one point is required")
    series: list[HourlySeries] = []
    for start in range(0, len(points), MAX_POINTS_PER_REQUEST):
        chunk = points[start : start + MAX_POINTS_PER_REQUEST]
        payload = fetch_raw(chunk, forecast_days, timeout=timeout)
        series.extend(parse_payload(payload, len(chunk)))
    return series
