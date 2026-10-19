"""Best-effort search for a third route. Pure Python, no Django imports.

Runs only when the provider returned fewer than 3 routes. Candidates are real Mapbox
responses (never synthesised): the main request with exclude=motorway, and origin -> via ->
destination detours with the via point offset left/right of the main route's midpoint.
`augment_routes` never raises; on any failure it returns the provider routes unchanged.
"""

from __future__ import annotations

import bisect
import logging
import math
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, replace

from pyproj import Geod

from routing.errors import MapboxError
from routing.mapbox import fetch_directions, parse_routes
from routing.models import Coord, Route

logger = logging.getLogger(__name__)

TARGET_ROUTES = 3
MAX_EXTRA_REQUESTS = 5  # hard cap per analyze call
MAX_THREADS = 4
REQUEST_TIMEOUT_S = 6.0
BUDGET_S = 8.0  # total wall-clock budget for the whole fallback
SIMILARITY_LIMIT = 0.85  # reject at or above this share of cells already covered
MAX_RATIO = 1.5  # reject when duration or distance exceeds this x the best route
CELL_DEG = 0.01
METERS_PER_MILE = 1609.344
MIN_OFFSET_MI = 1.5
MAX_OFFSET_MI = 35.0
TANGENT_HALF_SPAN_M = 3000.0

# Fixed evaluation order after the motorway candidate: (side, fraction of route length).
DETOURS: tuple[tuple[str, float], ...] = (
    ("left", 0.08),
    ("right", 0.08),
    ("left", 0.16),
    ("right", 0.16),
)
LABEL_MOTORWAY = "avoiding motorways"
LABEL_DETOUR = "forced detour, {side} of the main route"

_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class Candidate:
    label: str
    via: Coord | None = None
    exclude: str | None = None
    alternatives: bool = False


@dataclass
class FallbackReport:
    provider_count: int
    attempted: bool = False
    tried: int = 0
    accepted: int = 0
    rejected_similar: int = 0
    rejected_too_long: int = 0
    errors: int = 0
    elapsed_s: float = 0.0


# --------------------------------------------------------------------------- geometry


def _point_at(route: Route, target_m: float) -> Coord:
    """Point at `target_m` of cumulative distance, interpolated within its segment."""
    cum = route.cum_dist_m
    i = min(max(bisect.bisect_right(cum, target_m) - 1, 0), len(cum) - 2)
    span = cum[i + 1] - cum[i]
    t = min(max((target_m - cum[i]) / span, 0.0), 1.0) if span > 0 else 0.0
    (lat_a, lon_a), (lat_b, lon_b) = route.coords[i], route.coords[i + 1]
    return lat_a + (lat_b - lat_a) * t, lon_a + (lon_b - lon_a) * t


def offset_miles(fraction: float, length_m: float) -> float:
    return min(max(fraction * length_m / METERS_PER_MILE, MIN_OFFSET_MI), MAX_OFFSET_MI)


def via_points(route: Route) -> list[tuple[str, Coord]]:
    """(side, via point) for each detour, in DETOURS order.

    The point sits perpendicular to the route's local direction at its midpoint by
    cumulative distance. Empty for a route with no usable length or direction.
    """
    length = route.cum_dist_m[-1]
    if length <= 0:
        return []
    mid = length / 2
    half = min(TANGENT_HALF_SPAN_M, length / 4)
    before, after = _point_at(route, mid - half), _point_at(route, mid + half)
    if before == after:
        return []
    azimuth, _, _ = _GEOD.inv(before[1], before[0], after[1], after[0])
    mid_lat, mid_lon = _point_at(route, mid)
    points = []
    for side, fraction in DETOURS:
        bearing = azimuth - 90.0 if side == "left" else azimuth + 90.0
        distance_m = offset_miles(fraction, length) * METERS_PER_MILE
        lon, lat, _ = _GEOD.fwd(mid_lon, mid_lat, bearing, distance_m)
        points.append((side, (float(lat), float(lon))))
    return points


def grid_cells(coords: Sequence[Coord]) -> frozenset[tuple[int, int]]:
    """0.01-degree cells touched by a polyline (long segments are densified)."""
    cells: set[tuple[int, int]] = set()
    previous: Coord | None = None
    for lat, lon in coords:
        if previous is not None:
            steps = math.ceil(max(abs(lat - previous[0]), abs(lon - previous[1])) / (CELL_DEG / 2))
            for k in range(1, steps):
                t = k / steps
                cells.add(
                    (
                        math.floor((previous[0] + (lat - previous[0]) * t) / CELL_DEG),
                        math.floor((previous[1] + (lon - previous[1]) * t) / CELL_DEG),
                    )
                )
        cells.add((math.floor(lat / CELL_DEG), math.floor(lon / CELL_DEG)))
        previous = (lat, lon)
    return frozenset(cells)


def build_candidates(main: Route) -> list[Candidate]:
    """Fixed order: motorway exclusion, then left 8%, right 8%, left 16%, right 16%."""
    candidates = [
        Candidate(LABEL_MOTORWAY, exclude="motorway", alternatives=True),
    ]
    candidates += [
        Candidate(LABEL_DETOUR.format(side=side), via=point) for side, point in via_points(main)
    ]
    return candidates[:MAX_EXTRA_REQUESTS]


# --------------------------------------------------------------------------- search


@dataclass
class _Outcome:
    routes: list[Route]
    error: str | None = None


def _fetch(
    candidate: Candidate,
    origin: Coord,
    dest: Coord,
    token: str,
    factor: float,
    timeout_s: float,
) -> _Outcome:
    try:
        payload = fetch_directions(
            origin,
            dest,
            token,
            timeout=timeout_s,
            via=candidate.via,
            exclude=candidate.exclude,
            alternatives=candidate.alternatives,
        )
        return _Outcome(parse_routes(payload, truck_time_factor=factor))
    except MapboxError as exc:
        return _Outcome([], f"status={exc.status_code} reason={type(exc).__name__}")
    except Exception as exc:  # worker threads must never leak an exception
        return _Outcome([], f"status=None reason={type(exc).__name__}")


def _verdict(
    route: Route,
    cells: frozenset[tuple[int, int]],
    kept: Sequence[Route],
    kept_cells: Sequence[frozenset[tuple[int, int]]],
) -> str:
    """ "ok", "similar" or "too_long"."""
    if any(len(cells & other) / len(cells) >= SIMILARITY_LIMIT for other in kept_cells):
        return "similar"
    best = min(kept, key=lambda r: r.duration_s)
    if route.duration_s > MAX_RATIO * best.duration_s or (
        route.distance_m > MAX_RATIO * best.distance_m
    ):
        return "too_long"
    return "ok"


def _search(
    routes: Sequence[Route],
    origin: Coord,
    dest: Coord,
    token: str,
    factor: float,
    budget_s: float,
    timeout_s: float,
    report: FallbackReport,
    start: float,
) -> list[Route]:
    kept = list(routes)
    kept_cells = [grid_cells(r.coords) for r in kept]
    candidates = build_candidates(routes[0])
    report.attempted = True
    pool = ThreadPoolExecutor(max_workers=MAX_THREADS, thread_name_prefix="route-fallback")
    try:
        futures = [
            pool.submit(_fetch, c, origin, dest, token, factor, timeout_s) for c in candidates
        ]
        deadline = start + budget_s
        for candidate, future in zip(candidates, futures, strict=True):
            if len(kept) >= TARGET_ROUTES:
                break
            report.tried += 1
            try:
                outcome = future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeoutError:
                outcome = _Outcome([], "status=None reason=FallbackBudgetExceeded")
            if outcome.error is not None or not outcome.routes:
                report.errors += 1
                logger.warning(
                    "route fallback candidate skipped: provider=mapbox %s",
                    outcome.error or "status=200 reason=EmptyResponse",
                )
                continue
            for route in outcome.routes:
                if len(kept) >= TARGET_ROUTES:
                    break
                cells = grid_cells(route.coords)
                verdict = _verdict(route, cells, kept, kept_cells)
                if verdict == "similar":
                    report.rejected_similar += 1
                elif verdict == "too_long":
                    report.rejected_too_long += 1
                else:
                    kept.append(replace(route, origin_label=candidate.label))
                    kept_cells.append(cells)
                    report.accepted += 1
    finally:
        # Do not wait for stragglers: the main result must return within the budget.
        pool.shutdown(wait=False, cancel_futures=True)
    return kept


def augment_routes(
    routes: Sequence[Route],
    origin: Coord,
    dest: Coord,
    token: str,
    *,
    truck_time_factor: float,
    budget_s: float = BUDGET_S,
    request_timeout_s: float = REQUEST_TIMEOUT_S,
) -> tuple[list[Route], FallbackReport]:
    """Return (routes, report); `routes` starts with the provider routes, unchanged.

    Never raises. Does nothing when the provider already returned 3 or more routes.
    """
    report = FallbackReport(provider_count=len(routes))
    result = list(routes)
    start = time.monotonic()
    if routes and len(routes) < TARGET_ROUTES:
        try:
            result = _search(
                routes,
                origin,
                dest,
                token,
                truck_time_factor,
                budget_s,
                request_timeout_s,
                report,
                start,
            )
        except Exception as exc:
            report.errors += 1
            result = list(routes)
            report.accepted = 0
            logger.warning("route fallback aborted: reason=%s", type(exc).__name__)
        report.elapsed_s = time.monotonic() - start
    logger.info(
        "route fallback: attempted=%s provider_routes=%d tried=%d accepted=%d "
        "rejected_similar=%d rejected_too_long=%d errors=%d elapsed_s=%.2f",
        report.attempted,
        report.provider_count,
        report.tried,
        report.accepted,
        report.rejected_similar,
        report.rejected_too_long,
        report.errors,
        report.elapsed_s,
    )
    return result, report
