"""Score routes using sampled weather and the shared risk engine."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from analysis.models import CheckpointResult, RouteRanking, RouteResult, RouteSummary
from risk import RiskLevel, Weather, assess_checkpoint
from routing.checkpoints import sample_checkpoints
from routing.models import Route
from weather.models import MissingWeather, WeatherSample


class WeatherLookup(Protocol):
    def weather_at_many(
        self, queries: Sequence[tuple[float, float, datetime]]
    ) -> Sequence[WeatherSample | MissingWeather]: ...


def analyze_route(
    route: Route,
    departure_utc: datetime,
    interval_miles: float,
    load_lb: float,
    weather_service: WeatherLookup,
) -> RouteResult:
    """Sample weather and score a route. Missing weather stays unclassified."""
    if departure_utc.tzinfo is None or departure_utc.utcoffset() is None:
        raise ValueError("departure_utc must be timezone-aware")
    departure = departure_utc.astimezone(UTC)
    checkpoints = sample_checkpoints(route, interval_miles)
    queries = [
        (cp.lat, cp.lon, departure + timedelta(seconds=cp.eta_offset_s)) for cp in checkpoints
    ]
    weather_results = list(weather_service.weather_at_many(queries))
    if len(weather_results) != len(checkpoints):
        raise ValueError("weather service returned a different number of results than requested")

    output: list[CheckpointResult] = []
    miles = {level: 0.0 for level in RiskLevel}
    risk_weighted = 0.0
    scored_miles = 0.0
    missing_weather_miles = 0.0
    levels: list[RiskLevel] = []
    for cp, eta, sample in zip(checkpoints, queries, weather_results, strict=True):
        eta_utc = eta[2]
        if isinstance(sample, MissingWeather):
            missing_weather_miles += cp.owned_mi
            output.append(
                CheckpointResult(
                    cp.index, cp.lat, cp.lon, cp.dist_mi, eta_utc,
                    None, None, None, None, (), cp.owned_mi, True, str(sample),
                )
            )
            continue

        assessment = assess_checkpoint(
            Weather(sample.wind_mph, sample.rain_in_hr, sample.snow_in_hr), load_lb
        )
        miles[assessment.level] += cp.owned_mi
        risk_weighted += int(assessment.level) * cp.owned_mi
        scored_miles += cp.owned_mi
        levels.append(assessment.level)
        output.append(
            CheckpointResult(
                cp.index, cp.lat, cp.lon, cp.dist_mi, eta_utc,
                sample.wind_mph, sample.rain_in_hr, sample.snow_in_hr,
                assessment.level, assessment.reasons, cp.owned_mi, False,
            )
        )

    summary = RouteSummary(
        miles_by_level=miles,
        max_level=max(levels) if levels else None,
        average_risk=risk_weighted / scored_miles if scored_miles else None,
        duration_s=route.duration_s,
        eta_method=route.eta_method,
        missing_weather_count=sum(cp.weather_missing for cp in output),
        missing_weather_miles=missing_weather_miles,
    )
    return RouteResult(tuple(output), summary, route.origin_label)


def _risk_rank_key(result: RouteResult) -> tuple[bool, float, int, int, float, float]:
    summary = result.summary
    no_travel_mi = summary.miles_by_level[RiskLevel.NO_TRAVEL]
    blocked = no_travel_mi > 0
    return (
        blocked,
        no_travel_mi if blocked else 0.0,
        round(summary.miles_by_level[RiskLevel.SEVERE]),
        round(summary.miles_by_level[RiskLevel.HIGH]),
        round(summary.average_risk, 2) if summary.average_risk is not None else math.inf,
        summary.duration_s,
    )


def _rank_key(result: RouteResult) -> tuple[bool, bool, float, int, int, float, float]:
    return (result.summary.missing_weather_count > 0, *_risk_rank_key(result))


def _missing_note(result: RouteResult) -> str:
    summary = result.summary
    return (
        f"{result.origin_label} has incomplete weather data: "
        f"{summary.missing_weather_miles:.1f} miles across "
        f"{summary.missing_weather_count} checkpoints"
    )


def _reason(best: RouteResult, other: RouteResult) -> str:
    a, b = best.summary, other.summary
    notes: list[str] = []
    if a.missing_weather_count:
        notes.append(f"Recommended route has {_missing_note(best)}")
    elif b.missing_weather_count and _risk_rank_key(other) < _risk_rank_key(best):
        notes.append(
            "A lower-risk-looking route was not recommended because "
            f"{_missing_note(other)}"
        )

    if a.miles_by_level[RiskLevel.NO_TRAVEL] or b.miles_by_level[RiskLevel.NO_TRAVEL]:
        primary = "Recommended route has fewer No Travel miles"
    else:
        severe = round(b.miles_by_level[RiskLevel.SEVERE]) - round(
            a.miles_by_level[RiskLevel.SEVERE]
        )
        high = round(b.miles_by_level[RiskLevel.HIGH]) - round(a.miles_by_level[RiskLevel.HIGH])
        mins = round((a.duration_s - b.duration_s) / 60)
        if severe:
            primary = f"{abs(severe)} {'fewer' if severe > 0 else 'more'} Severe miles"
        elif high:
            primary = f"{abs(high)} {'fewer' if high > 0 else 'more'} High miles"
        elif a.average_risk != b.average_risk and (
            a.average_risk is None or b.average_risk is None
        ):
            primary = "Recommended route has a calculable average risk"
        elif a.average_risk is not None and b.average_risk is not None and round(
            a.average_risk, 2
        ) != round(b.average_risk, 2):
            primary = f"Lower average risk ({a.average_risk:.2f} vs {b.average_risk:.2f})"
        elif mins:
            primary = (
                f"{abs(mins)} min {'longer' if mins > 0 else 'shorter'} "
                "with the same rounded risk"
            )
        else:
            primary = "Routes tie on all ranking criteria; this route is listed first"
    return "; ".join([primary, *notes])


def rank_routes(results: Sequence[RouteResult]) -> RouteRanking:
    """Prefer complete weather, then apply No Travel, Severe, High, average, duration."""
    if not results:
        raise ValueError("at least one route result is required")
    ordered = tuple(sorted(results, key=_rank_key))
    if len(ordered) > 1:
        reason = _reason(ordered[0], ordered[1])
    elif ordered[0].summary.missing_weather_count:
        reason = f"Only route available; {_missing_note(ordered[0])}"
    else:
        reason = "Only route available"
    return RouteRanking(ordered, ordered[0], reason)
