"""Geodesic length helpers (pyproj.Geod, WGS84)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from pyproj import Geod

from routing.models import Coord, Route

_GEOD = Geod(ellps="WGS84")

# A polyline's geodesic length is slightly below the road distance; 5% is generous
# for overview=full geometry.
DEFAULT_DISTANCE_TOLERANCE = 0.05


def segment_lengths_m(coords: Sequence[Coord]) -> list[float]:
    """Geodesic length in metres of each consecutive (lat, lon) segment."""
    if len(coords) < 2:
        return []
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    _, _, dist = _GEOD.inv(lons[:-1], lats[:-1], lons[1:], lats[1:])
    return [float(d) for d in dist]


def geodesic_length_m(coords: Sequence[Coord]) -> float:
    return math.fsum(segment_lengths_m(coords))


@dataclass(frozen=True)
class DistanceCheck:
    geodesic_m: float
    provider_m: float
    rel_error: float  # |geodesic - provider| / provider
    tolerance: float
    ok: bool


def check_distance(
    coords: Sequence[Coord],
    provider_distance_m: float,
    tolerance: float = DEFAULT_DISTANCE_TOLERANCE,
) -> DistanceCheck:
    """Compare the geodesic length of `coords` with the provider's distance.

    `ok` is True when the relative error is within `tolerance` (a fraction, 0.05 = 5%).
    """
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError(f"tolerance must be finite and non-negative, got {tolerance!r}")
    if not math.isfinite(provider_distance_m) or provider_distance_m < 0:
        raise ValueError(f"provider distance must be finite and >= 0, got {provider_distance_m!r}")
    geodesic = geodesic_length_m(coords)
    if provider_distance_m > 0:
        rel_error = abs(geodesic - provider_distance_m) / provider_distance_m
    else:
        rel_error = 0.0 if geodesic == 0 else math.inf
    return DistanceCheck(
        geodesic_m=geodesic,
        provider_m=provider_distance_m,
        rel_error=rel_error,
        tolerance=tolerance,
        ok=rel_error <= tolerance,
    )


def check_route_distance(
    route: Route, tolerance: float = DEFAULT_DISTANCE_TOLERANCE
) -> DistanceCheck:
    return check_distance(route.coords, route.distance_m, tolerance)
