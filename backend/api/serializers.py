"""Explicit JSON serializers for analysis results."""

from __future__ import annotations

from datetime import UTC, datetime

from analysis.models import CheckpointResult, RouteResult
from risk import RiskLevel
from routing.mapbox import PROVIDER_LABEL
from routing.models import Route
from routing.simplify import simplify_coords

_LEVEL_NAMES = {
    RiskLevel.LOW: "Low",
    RiskLevel.MODERATE: "Moderate",
    RiskLevel.HIGH: "High",
    RiskLevel.SEVERE: "Severe",
    RiskLevel.NO_TRAVEL: "No Travel",
}


def _time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def serialize_checkpoint(checkpoint: CheckpointResult) -> dict[str, object]:
    weather = None
    if not checkpoint.weather_missing:
        weather = {
            "wind_mph": checkpoint.wind_mph,
            "rain_in_hr": checkpoint.rain_in_hr,
            "snow_in_hr": checkpoint.snow_in_hr,
        }
    return {
        "index": checkpoint.index,
        "lat": checkpoint.lat,
        "lon": checkpoint.lon,
        "dist_mi": checkpoint.dist_mi,
        "eta_utc": _time(checkpoint.eta_utc),
        "weather": weather,
        "risk_level": _LEVEL_NAMES.get(checkpoint.risk_level),
        "risk_level_minus_2h": _LEVEL_NAMES.get(checkpoint.risk_level_minus_2h),
        "risk_level_plus_2h": _LEVEL_NAMES.get(checkpoint.risk_level_plus_2h),
        "unstable": checkpoint.unstable,
        "reasons": list(checkpoint.reasons),
        "owned_mi": checkpoint.owned_mi,
        "weather_missing": checkpoint.weather_missing,
        "missing_reason": checkpoint.missing_reason,
    }


def serialize_route(index: int, route: Route, result: RouteResult) -> dict[str, object]:
    summary = result.summary
    display_coords = simplify_coords(route.coords)
    return {
        "route_index": index,
        "origin_label": route.origin_label,
        # "provider" = returned by the routing provider; "generated" = found by our fallback.
        "source": "provider" if route.origin_label == PROVIDER_LABEL else "generated",
        "geometry": {
            "coordinates": [[lon, lat] for lat, lon in display_coords],
        },
        "distance_mi": route.distance_m / 1609.344,
        "duration_s": route.duration_s,
        "eta_method": summary.eta_method,
        "checkpoints": [serialize_checkpoint(cp) for cp in result.checkpoints],
        "summary": {
            "miles_by_level": {
                _LEVEL_NAMES[level]: summary.miles_by_level[level] for level in RiskLevel
            },
            "missing_weather_miles": summary.missing_weather_miles,
            "max_level": _LEVEL_NAMES.get(summary.max_level),
            "average_risk": summary.average_risk,
            "duration_s": summary.duration_s,
            "eta_method": summary.eta_method,
            "missing_weather_count": summary.missing_weather_count,
            "unstable_checkpoints": summary.unstable_checkpoints,
            "unknown_stability_checkpoints": summary.unknown_stability_checkpoints,
        },
    }
