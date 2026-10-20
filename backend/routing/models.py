"""Route model. Pure Python, no Django imports."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import accumulate, pairwise
from typing import Literal

Coord = tuple[float, float]  # (lat, lon)
EtaMethod = Literal["annotations", "proportional"]
ETA_METHODS: tuple[str, ...] = ("annotations", "proportional")


def _check_cumulative(name: str, values: Sequence[float]) -> None:
    if not all(math.isfinite(v) for v in values):
        raise ValueError(f"{name} contains a non-finite value")
    if values[0] != 0:
        raise ValueError(f"{name} must start at 0, got {values[0]!r}")
    if any(b < a for a, b in pairwise(values)):
        raise ValueError(f"{name} must be non-decreasing")


@dataclass(frozen=True)
class Route:
    """One drivable route.

    `coords` is a list of (lat, lon). `cum_dist_m` and `cum_dur_s` are aligned to
    `coords`: entry i is the distance / drive time from the start to coords[i].
    `distance_m` and `duration_s` are the provider's totals (duration already
    includes the truck time factor). They can differ from `cum_*[-1]` by the
    provider's rounding; anything that walks the route (checkpoints) uses the
    cumulative arrays only, so it stays self-consistent.
    """

    coords: list[Coord]
    cum_dist_m: list[float]
    cum_dur_s: list[float]
    distance_m: float
    duration_s: float
    eta_method: EtaMethod
    # "HERE route"
    origin_label: str

    def __post_init__(self) -> None:
        n = len(self.coords)
        if n < 2:
            raise ValueError("a route needs at least 2 coordinates")
        if len(self.cum_dist_m) != n or len(self.cum_dur_s) != n:
            raise ValueError("cum_dist_m and cum_dur_s must be aligned to coords")
        for lat, lon in self.coords:
            if not (math.isfinite(lat) and math.isfinite(lon)):
                raise ValueError("coordinates must be finite")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"coordinate out of range: ({lat!r}, {lon!r})")
        _check_cumulative("cum_dist_m", self.cum_dist_m)
        _check_cumulative("cum_dur_s", self.cum_dur_s)
        for name, value in (("distance_m", self.distance_m), ("duration_s", self.duration_s)):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative, got {value!r}")
        if self.eta_method not in ETA_METHODS:
            raise ValueError(f"eta_method must be one of {ETA_METHODS}, got {self.eta_method!r}")
        if not self.origin_label:
            raise ValueError("origin_label must not be empty")


def join_routes(routes: Sequence[Route], origin_label: str) -> Route:
    """Join consecutive routes/legs end to start into one Route.

    The last point of each piece is assumed to be the first point of the next, so the
    duplicated joint point is dropped (the next piece's first coordinate is discarded).
    The result's eta_method is "annotations" only if every piece used annotations.
    """
    if not routes:
        raise ValueError("join_routes needs at least one route")
    coords = list(routes[0].coords)
    cum_dist = list(routes[0].cum_dist_m)
    cum_dur = list(routes[0].cum_dur_s)
    for piece in routes[1:]:
        # Capture the offsets first: extend() would otherwise see the list growing.
        dist_offset, dur_offset = cum_dist[-1], cum_dur[-1]
        coords.extend(piece.coords[1:])
        cum_dist.extend([dist_offset + d for d in piece.cum_dist_m[1:]])
        cum_dur.extend([dur_offset + t for t in piece.cum_dur_s[1:]])
    all_annotated = all(r.eta_method == "annotations" for r in routes)
    return Route(
        coords=coords,
        cum_dist_m=cum_dist,
        cum_dur_s=cum_dur,
        distance_m=math.fsum(r.distance_m for r in routes),
        duration_s=math.fsum(r.duration_s for r in routes),
        eta_method="annotations" if all_annotated else "proportional",
        origin_label=origin_label,
    )


def cumulative(segments: Sequence[float]) -> list[float]:
    """Cumulative sums starting at 0.0 (length len(segments) + 1)."""
    return list(accumulate(segments, initial=0.0))
