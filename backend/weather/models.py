"""Weather data models. Pure Python, no Django imports."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

Point = tuple[float, float]  # (lat, lon)

# Open-Meteo variable names, as requested (confirmed against the official docs).
VAR_WIND = "wind_speed_10m"
VAR_RAIN = "rain"
VAR_SHOWERS = "showers"
VAR_SNOW = "snowfall"


@dataclass(frozen=True)
class HourValues:
    """Raw values for one forecast hour at one grid cell. None means null/missing."""

    wind_mph: float | None
    rain_in: float | None  # `rain`: large-scale rain, inches in the preceding hour
    showers_in: float | None  # `showers`: convective rain, inches in the preceding hour
    snow_in: float | None  # `snowfall`: inches in the preceding hour


@dataclass(frozen=True)
class HourlySeries:
    """One location's hourly forecast: strictly increasing UTC hours and their values."""

    times: tuple[datetime, ...]
    values: tuple[HourValues, ...]

    def items(self) -> Iterator[tuple[datetime, HourValues]]:
        return zip(self.times, self.values, strict=True)


@dataclass(frozen=True)
class WeatherSample:
    """Weather for one checkpoint, ready for the risk engine.

    The field names match `risk.Weather` (wind_mph, rain_in_hr, snow_in_hr) so a caller can
    build one with `Weather(s.wind_mph, s.rain_in_hr, s.snow_in_hr)`. `rain_in_hr` is
    `rain + showers`. All values are real numbers; missing data is never turned into 0.
    """

    hour: datetime  # the forecast hour that was used (UTC)
    wind_mph: float
    rain_in_hr: float
    snow_in_hr: float


@dataclass(frozen=True)
class MissingWeather:
    """Flag returned in place of a WeatherSample when data is unavailable."""

    hour: datetime | None
    reason: str
    variables: tuple[str, ...] = ()  # Open-Meteo variable names that were null

    def __str__(self) -> str:
        when = self.hour.isoformat() if self.hour else "unknown hour"
        names = f" ({', '.join(self.variables)})" if self.variables else ""
        return f"weather unavailable for {when}: {self.reason}{names}"
