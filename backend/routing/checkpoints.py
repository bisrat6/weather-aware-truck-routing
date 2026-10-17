"""Checkpoint sampling along a Route. Pure Python, no Django imports."""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass

from routing.models import Route

METERS_PER_MILE = 1609.344
DEFAULT_MAX_POINTS = 120


@dataclass(frozen=True)
class Checkpoint:
    index: int
    lat: float
    lon: float
    dist_mi: float  # distance from the start
    eta_offset_s: float  # drive time from the start (departure time + this = ETA)
    owned_mi: float  # miles of route this checkpoint is responsible for


def plan_distances_mi(total_mi: float, interval_miles: float, max_points: int) -> list[float]:
    """Distances (miles from start) at which to place checkpoints.

    Points sit at 0, interval, 2*interval, ... and the last one is exactly `total_mi`
    (the destination). If that would be more than `max_points` points, the interval is
    coarsened to total_mi / (max_points - 1), giving exactly `max_points` evenly spaced
    points. All gaps equal the (effective) interval except the last, which is shorter.
    """
    if not math.isfinite(interval_miles) or interval_miles <= 0:
        raise ValueError(f"interval_miles must be finite and > 0, got {interval_miles!r}")
    if max_points < 2:
        raise ValueError(f"max_points must be at least 2, got {max_points!r}")
    if not math.isfinite(total_mi) or total_mi < 0:
        raise ValueError(f"total_mi must be finite and >= 0, got {total_mi!r}")

    # The tiny epsilon stops float noise (e.g. 50.00000000001 mi) adding a sliver segment.
    intervals = max(1, math.ceil(total_mi / interval_miles - 1e-9))
    step = interval_miles
    if intervals + 1 > max_points:
        intervals = max_points - 1
        step = total_mi / intervals
    return [i * step for i in range(intervals)] + [total_mi]


def _owned_miles(dist_mi: list[float]) -> list[float]:
    """Each point owns half of the gap to each neighbour; the ends have one neighbour."""
    n = len(dist_mi)
    owned = []
    for i in range(n):
        left = dist_mi[i] - dist_mi[i - 1] if i > 0 else 0.0
        right = dist_mi[i + 1] - dist_mi[i] if i < n - 1 else 0.0
        owned.append((left + right) / 2)
    return owned


def _interpolate(route: Route, dist_m: float) -> tuple[float, float, float]:
    """(lat, lon, eta_offset_s) at `dist_m` metres from the start, by linear interpolation."""
    cum = route.cum_dist_m
    i = min(max(bisect_right(cum, dist_m) - 1, 0), len(cum) - 2)
    span = cum[i + 1] - cum[i]
    frac = min(max((dist_m - cum[i]) / span, 0.0), 1.0) if span > 0 else 0.0

    def lerp(a: float, b: float) -> float:
        value = a + frac * (b - a)
        return min(max(value, min(a, b)), max(a, b))  # guard against rounding overshoot

    (lat0, lon0), (lat1, lon1) = route.coords[i], route.coords[i + 1]
    return (
        lerp(lat0, lat1),
        lerp(lon0, lon1),
        lerp(route.cum_dur_s[i], route.cum_dur_s[i + 1]),
    )


def sample_checkpoints(
    route: Route, interval_miles: float, max_points: int = DEFAULT_MAX_POINTS
) -> list[Checkpoint]:
    """Place checkpoints along `route` every `interval_miles` (10, 25 or 50 in the brief).

    Placement is by cumulative distance with linear interpolation of position and ETA.
    The first checkpoint is the start and the last is the destination. If the interval
    would give more than `max_points`, it is coarsened (see `plan_distances_mi`).

    All distances come from `route.cum_dist_m`, so `sum(owned_mi)` equals the route's
    total miles (to floating-point precision).
    """
    total_m = route.cum_dist_m[-1]
    distances_mi = plan_distances_mi(total_m / METERS_PER_MILE, interval_miles, max_points)
    last = len(distances_mi) - 1
    owned = _owned_miles(distances_mi)

    checkpoints: list[Checkpoint] = []
    previous_eta = 0.0
    for i, dist_mi in enumerate(distances_mi):
        if i == 0:
            lat, lon = route.coords[0]
            eta = 0.0
        elif i == last:
            lat, lon = route.coords[-1]
            eta = route.cum_dur_s[-1]
        else:
            lat, lon, eta = _interpolate(route, min(dist_mi * METERS_PER_MILE, total_m))
        eta = max(eta, previous_eta)  # never step backwards because of rounding
        previous_eta = eta
        checkpoints.append(
            Checkpoint(
                index=i,
                lat=lat,
                lon=lon,
                dist_mi=dist_mi,
                eta_offset_s=eta,
                owned_mi=owned[i],
            )
        )
    return checkpoints
