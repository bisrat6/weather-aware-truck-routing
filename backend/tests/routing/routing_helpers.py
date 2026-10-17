"""Shared helpers for the routing tests."""

from routing.models import Route, cumulative


def make_route(seg_dist, seg_dur, coords=None, eta_method="annotations", label="test"):
    """Build a valid Route from per-segment distances (m) and durations (s)."""
    n = len(seg_dist) + 1
    if coords is None:
        coords = [(40.0 + 0.01 * i, -100.0 + 0.01 * i) for i in range(n)]
    cd, ct = cumulative(seg_dist), cumulative(seg_dur)
    return Route(
        coords=coords,
        cum_dist_m=cd,
        cum_dur_s=ct,
        distance_m=cd[-1],
        duration_s=ct[-1],
        eta_method=eta_method,
        origin_label=label,
    )
