"""Polyline simplification for client display geometry (iterative Douglas-Peucker)."""

from __future__ import annotations

import math
from collections.abc import Sequence

from routing.models import Coord

# 35 metres is centered in the 25-50 m requirement; gives >90% coordinate reduction
# with unnoticeable visual deviation and preserved curve fidelity.
DEFAULT_SIMPLIFICATION_TOLERANCE_M = 35.0
_EARTH_RADIUS_M = 6_371_000.0


def point_to_segment_distance_m(
    lat: float,
    lon: float,
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Perpendicular distance in metres from (lat, lon) to segment (lat1, lon1)-(lat2, lon2).

    Uses a local equirectangular projection centered at the segment midpoint.
    """
    rad = math.radians
    mid_lat = rad((lat1 + lat2) * 0.5)
    cos_lat = math.cos(mid_lat)

    dx = rad(lon2 - lon1) * cos_lat * _EARTH_RADIUS_M
    dy = rad(lat2 - lat1) * _EARTH_RADIUS_M
    px = rad(lon - lon1) * cos_lat * _EARTH_RADIUS_M
    py = rad(lat - lat1) * _EARTH_RADIUS_M

    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq <= 0.0:
        return math.hypot(px, py)

    t = (px * dx + py * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    proj_x = t * dx
    proj_y = t * dy
    return math.hypot(px - proj_x, py - proj_y)


def simplify_coords(
    coords: Sequence[Coord],
    tolerance_m: float = DEFAULT_SIMPLIFICATION_TOLERANCE_M,
) -> list[Coord]:
    """Simplify an ordered sequence of (latitude, longitude) coordinates.

    Uses an iterative Ramer-Douglas-Peucker algorithm.
    Endpoints (coords[0] and coords[-1]) are always preserved exactly.
    """
    n = len(coords)
    if n <= 2:
        return list(coords)
    if tolerance_m <= 0.0:
        return list(coords)

    keep = [False] * n
    keep[0] = True
    keep[n - 1] = True

    # Use an explicit stack to avoid recursion limit on 35,000+ points
    stack: list[tuple[int, int]] = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        lat1, lon1 = coords[start]
        lat2, lon2 = coords[end]
        max_dist = -1.0
        max_idx = start
        for i in range(start + 1, end):
            d = point_to_segment_distance_m(
                coords[i][0], coords[i][1], lat1, lon1, lat2, lon2
            )
            if d > max_dist:
                max_dist = d
                max_idx = i
        if max_dist > tolerance_m:
            keep[max_idx] = True
            stack.append((start, max_idx))
            stack.append((max_idx, end))

    return [coord for coord, is_kept in zip(coords, keep, strict=True) if is_kept]


def max_deviation_m(original: Sequence[Coord], simplified: Sequence[Coord]) -> float:
    """Compute the maximum distance in metres from any original point to the simplified line."""
    if len(original) <= 2 or len(simplified) <= 1:
        return 0.0
    simp_idx = 0
    max_dev = 0.0
    for pt in original:
        while simp_idx < len(simplified) - 2 and pt == simplified[simp_idx + 1]:
            simp_idx += 1
        d = point_to_segment_distance_m(
            pt[0],
            pt[1],
            simplified[simp_idx][0],
            simplified[simp_idx][1],
            simplified[simp_idx + 1][0],
            simplified[simp_idx + 1][1],
        )
        if d > max_dev:
            max_dev = d
    return max_dev
