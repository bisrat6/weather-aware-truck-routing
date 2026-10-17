"""Choosing the forecast hour for an ETA and turning raw hour values into a sample.

Pure Python, no Django imports.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from weather.models import (
    VAR_RAIN,
    VAR_SHOWERS,
    VAR_SNOW,
    VAR_WIND,
    HourValues,
    MissingWeather,
    WeatherSample,
)

_HALF_HOUR = timedelta(minutes=30)
_ONE_HOUR = timedelta(hours=1)

# rain + showers is summed in floating point, so 0.01 + 0.09 is 0.09999999999999999, just
# under the 0.10 band edge. Rounding to 6 decimals removes that noise without moving any
# real value (the provider reports 2-3 decimals).
_RAIN_SUM_DECIMALS = 6


def select_hour(eta: datetime) -> datetime:
    """The forecast hour nearest to `eta` (UTC, on the hour).

    The project guide says "nearest forecast hour". Exactly :30:00 rounds up, so
    10:29:59 -> 10:00 and 10:30:00 -> 11:00. `eta` must be timezone-aware.
    """
    if eta.tzinfo is None or eta.utcoffset() is None:
        raise ValueError("eta must be timezone-aware")
    moment = eta.astimezone(UTC)
    floor = moment.replace(minute=0, second=0, microsecond=0)
    return floor + _ONE_HOUR if moment - floor >= _HALF_HOUR else floor


def sample_hour(hour: datetime, values: HourValues) -> WeatherSample | MissingWeather:
    """Risk-engine inputs for one forecast hour, or a flag if any needed value is null.

    rain_in_hr is `rain + showers` (project guide); both must be present. A null is never
    treated as 0: it returns MissingWeather naming the null variables. A real 0.0 is kept.
    """
    missing: list[str] = []
    if values.wind_mph is None:
        missing.append(VAR_WIND)
    if values.rain_in is None:
        missing.append(VAR_RAIN)
    if values.showers_in is None:
        missing.append(VAR_SHOWERS)
    if values.snow_in is None:
        missing.append(VAR_SNOW)
    if missing:
        return MissingWeather(hour, "null value in forecast", tuple(missing))
    assert values.wind_mph is not None
    assert values.rain_in is not None
    assert values.showers_in is not None
    assert values.snow_in is not None
    return WeatherSample(
        hour=hour,
        wind_mph=values.wind_mph,
        rain_in_hr=round(values.rain_in + values.showers_in, _RAIN_SUM_DECIMALS),
        snow_in_hr=values.snow_in,
    )
