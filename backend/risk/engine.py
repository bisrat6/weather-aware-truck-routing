"""Pure-Python weather risk engine. No Django imports.

Classifies the weather at one checkpoint into a RiskLevel using the brief's
wind, rain and snow bands plus the load rules. See docs/ASSUMPTIONS.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum


class RiskLevel(IntEnum):
    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4


_LABELS: dict[RiskLevel, str] = {
    RiskLevel.LOW: "Low",
    RiskLevel.MODERATE: "Moderate",
    RiskLevel.HIGH: "High",
    RiskLevel.SEVERE: "Severe",
    RiskLevel.NO_TRAVEL: "No Travel",
}


@dataclass(frozen=True)
class Weather:
    wind_mph: float
    rain_in_hr: float
    snow_in_hr: float


@dataclass(frozen=True)
class CheckpointRisk:
    """Risk at one checkpoint.

    `wind_level` includes any load-rule escalation, because the load rules are
    conditions on wind speed. This keeps `level == max(wind, rain, snow level)`.
    """

    level: RiskLevel
    wind_level: RiskLevel
    rain_level: RiskLevel
    snow_level: RiskLevel
    reasons: tuple[str, ...]


def _wind_band(wind_mph: float) -> RiskLevel:
    if wind_mph >= 55:
        return RiskLevel.NO_TRAVEL
    if wind_mph >= 45:
        return RiskLevel.SEVERE
    if wind_mph >= 35:
        return RiskLevel.HIGH
    if wind_mph >= 25:
        return RiskLevel.MODERATE
    return RiskLevel.LOW


def _rain_band(rain_in_hr: float) -> RiskLevel:
    # 1.00 is inclusive Severe: only values above it are No Travel.
    if rain_in_hr > 1.00:
        return RiskLevel.NO_TRAVEL
    if rain_in_hr >= 0.50:
        return RiskLevel.SEVERE
    if rain_in_hr >= 0.25:
        return RiskLevel.HIGH
    if rain_in_hr >= 0.10:
        return RiskLevel.MODERATE
    return RiskLevel.LOW


def _snow_band(snow_in_hr: float) -> RiskLevel:
    # 3.0 is inclusive Severe: only values above it are No Travel.
    if snow_in_hr > 3.0:
        return RiskLevel.NO_TRAVEL
    if snow_in_hr >= 2.0:
        return RiskLevel.SEVERE
    if snow_in_hr >= 1.0:
        return RiskLevel.HIGH
    if snow_in_hr >= 0.5:
        return RiskLevel.MODERATE
    return RiskLevel.LOW


def _load_escalation(wind_mph: float, load_lb: float) -> RiskLevel:
    """Level demanded by the load rules alone (LOW when none applies).

    Every rule is evaluated; there are no early returns.
    """
    demanded = [RiskLevel.LOW]
    if 45 <= wind_mph < 55 and load_lb > 30_000:
        demanded.append(RiskLevel.NO_TRAVEL)
    if 35 <= wind_mph < 45 and load_lb > 40_000:
        demanded.append(RiskLevel.SEVERE)
    return max(demanded)


def _validate(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value!r}")


def _fmt_load(load_lb: float) -> str:
    load = float(load_lb)
    return f"{load:,.0f}" if load.is_integer() else f"{load:,}"


def _reason(
    factor: str, value: float, unit: str, level: RiskLevel, load_lb: float | None = None
) -> str:
    load_part = f", load {_fmt_load(load_lb)} lb" if load_lb is not None else ""
    return f"{factor} {float(value)!r} {unit}{load_part}: {_LABELS[level]}"


def assess_checkpoint(weather: Weather, load_lb: float) -> CheckpointRisk:
    """Classify the weather at a checkpoint for a truck carrying `load_lb` pounds.

    Raises ValueError for NaN, infinite or negative inputs.
    """
    _validate("wind_mph", weather.wind_mph)
    _validate("rain_in_hr", weather.rain_in_hr)
    _validate("snow_in_hr", weather.snow_in_hr)
    _validate("load_lb", load_lb)

    wind_band = _wind_band(weather.wind_mph)
    rain_level = _rain_band(weather.rain_in_hr)
    snow_level = _snow_band(weather.snow_in_hr)
    load_level = _load_escalation(weather.wind_mph, load_lb)
    wind_level = max(wind_band, load_level)

    reasons: list[str] = []
    if wind_band > RiskLevel.LOW:
        reasons.append(_reason("wind", weather.wind_mph, "mph", wind_band))
    if load_level > wind_band:
        reasons.append(_reason("wind", weather.wind_mph, "mph", load_level, load_lb))
    if rain_level > RiskLevel.LOW:
        reasons.append(_reason("rain", weather.rain_in_hr, "in/hr", rain_level))
    if snow_level > RiskLevel.LOW:
        reasons.append(_reason("snow", weather.snow_in_hr, "in/hr", snow_level))

    return CheckpointRisk(
        level=max(wind_level, rain_level, snow_level),
        wind_level=wind_level,
        rain_level=rain_level,
        snow_level=snow_level,
        reasons=tuple(reasons),
    )
