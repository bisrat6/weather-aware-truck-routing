import math
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st
from routing_helpers import make_route

from routing.checkpoints import METERS_PER_MILE, plan_distances_mi, sample_checkpoints
from routing.models import Route, cumulative

MI = METERS_PER_MILE


def straight_route(miles: float, seconds: float) -> Route:
    """A single straight segment due north, `miles` long, taking `seconds`."""
    return make_route([miles * MI], [seconds], coords=[(40.0, -100.0), (41.0, -100.0)])


# ------------------------------------------------------------------ strategies


# One tuple per segment: (distance m, duration s, end lat, end lon), plus the start point.
_segment = st.tuples(
    st.floats(0, 30_000), st.floats(0, 2_000), st.floats(25, 49), st.floats(-124, -67)
)


@st.composite
def routes(draw, allow_zero_total=True):
    segments = draw(st.lists(_segment, min_size=1, max_size=40))
    start = draw(st.tuples(st.floats(25, 49), st.floats(-124, -67)))
    dist = [s[0] for s in segments]
    if not allow_zero_total and sum(dist) == 0:
        dist[0] = 1.0
    coords = [start] + [(s[2], s[3]) for s in segments]
    return make_route(dist, [s[1] for s in segments], coords=coords)


intervals = st.one_of(st.sampled_from([10.0, 25.0, 50.0]), st.floats(0.5, 200.0))
max_points_st = st.one_of(st.just(120), st.integers(min_value=2, max_value=200))


def total_miles(route: Route) -> float:
    return route.cum_dist_m[-1] / MI


# ------------------------------------------------------------------ properties


@given(routes(), intervals, max_points_st)
def test_first_is_start_and_last_is_destination(route, interval, max_points):
    cps = sample_checkpoints(route, interval, max_points)
    assert (cps[0].lat, cps[0].lon) == route.coords[0]
    assert cps[0].dist_mi == 0.0
    assert cps[0].eta_offset_s == 0.0
    assert (cps[-1].lat, cps[-1].lon) == route.coords[-1]
    assert cps[-1].dist_mi == total_miles(route)
    assert cps[-1].eta_offset_s == route.cum_dur_s[-1]


@given(routes(), intervals, max_points_st)
def test_count_never_exceeds_max_points(route, interval, max_points):
    cps = sample_checkpoints(route, interval, max_points)
    assert 2 <= len(cps) <= max_points
    assert [c.index for c in cps] == list(range(len(cps)))


@given(routes(), intervals, max_points_st)
def test_owned_miles_sum_to_total(route, interval, max_points):
    cps = sample_checkpoints(route, interval, max_points)
    assert all(c.owned_mi >= 0 for c in cps)
    assert math.fsum(c.owned_mi for c in cps) == pytest.approx(
        total_miles(route), rel=1e-9, abs=1e-9
    )


@given(routes(), intervals, max_points_st)
def test_etas_and_distances_are_monotonic(route, interval, max_points):
    cps = sample_checkpoints(route, interval, max_points)
    assert all(b.eta_offset_s >= a.eta_offset_s for a, b in pairwise(cps))
    assert all(b.dist_mi >= a.dist_mi for a, b in pairwise(cps))
    assert all(0 <= c.eta_offset_s <= route.cum_dur_s[-1] for c in cps)


@given(routes(allow_zero_total=False), intervals, max_points_st)
def test_spacing_matches_the_interval(route, interval, max_points):
    total = total_miles(route)
    if total < 1e-6:
        return  # sub-micro-mile routes: spacing is meaningless
    cps = sample_checkpoints(route, interval, max_points)
    gaps = [b.dist_mi - a.dist_mi for a, b in pairwise(cps)]
    # Effective interval: the requested one, or coarser if it would exceed max_points.
    expected = max(interval, total / (max_points - 1))
    for gap in gaps[:-1]:
        assert gap == pytest.approx(expected, rel=1e-6)
    assert 0 <= gaps[-1] <= expected * (1 + 1e-6)


@given(routes(), intervals, max_points_st)
def test_checkpoints_lie_within_route_bounding_box(route, interval, max_points):
    lats = [c[0] for c in route.coords]
    lons = [c[1] for c in route.coords]
    for c in sample_checkpoints(route, interval, max_points):
        assert min(lats) <= c.lat <= max(lats)
        assert min(lons) <= c.lon <= max(lons)


# ------------------------------------------------------------------ concrete cases


def test_straight_route_every_25_miles():
    cps = sample_checkpoints(straight_route(100, 6000), 25)
    assert [round(c.dist_mi, 6) for c in cps] == [0, 25, 50, 75, 100]
    assert [c.lat for c in cps] == pytest.approx([40.0, 40.25, 40.5, 40.75, 41.0])
    assert [c.eta_offset_s for c in cps] == pytest.approx([0, 1500, 3000, 4500, 6000])
    assert all(c.lon == -100.0 for c in cps)


def test_owned_miles_halves_the_gaps():
    cps = sample_checkpoints(straight_route(100, 6000), 25)
    assert [c.owned_mi for c in cps] == pytest.approx([12.5, 25, 25, 25, 12.5])
    assert sum(c.owned_mi for c in cps) == pytest.approx(100.0)


def test_partial_last_gap_is_split_between_neighbours():
    cps = sample_checkpoints(straight_route(60, 3600), 25)
    assert [round(c.dist_mi, 6) for c in cps] == [0, 25, 50, 60]
    # Gaps are 25, 25, 10 -> owned: 12.5, 25, 17.5, 5.
    assert [c.owned_mi for c in cps] == pytest.approx([12.5, 25, 17.5, 5])


def test_exact_multiple_has_no_sliver_checkpoint():
    assert len(sample_checkpoints(straight_route(100, 1), 25)) == 5
    # Float noise just above a multiple must not create a near-duplicate point.
    assert len(sample_checkpoints(straight_route(100.0000000001, 1), 25)) == 5


def test_route_shorter_than_interval_has_start_and_destination():
    cps = sample_checkpoints(straight_route(4, 240), 25)
    assert [round(c.dist_mi, 6) for c in cps] == [0, 4]
    assert [c.owned_mi for c in cps] == pytest.approx([2, 2])


def test_zero_length_route():
    route = make_route([0.0, 0.0], [5.0, 5.0], coords=[(40.0, -100.0)] * 3)
    cps = sample_checkpoints(route, 10)
    assert len(cps) == 2
    assert all(c.dist_mi == 0 and c.owned_mi == 0 for c in cps)
    assert cps[-1].eta_offset_s == 10.0


def test_interval_is_coarsened_when_too_many_points():
    route = straight_route(1000, 36_000)
    assert len(sample_checkpoints(route, 10)) == 101  # fits under 120, untouched
    cps = sample_checkpoints(route, 5)  # 201 points would exceed 120
    assert len(cps) == 120
    gap = 1000 / 119
    assert [b.dist_mi - a.dist_mi for a, b in pairwise(cps)] == pytest.approx([gap] * 119)
    assert cps[-1].dist_mi == pytest.approx(1000.0)


def test_max_points_two_gives_start_and_destination_only():
    cps = sample_checkpoints(straight_route(500, 1000), 10, max_points=2)
    assert len(cps) == 2
    assert [c.owned_mi for c in cps] == pytest.approx([250, 250])


def test_exactly_max_points_is_not_coarsened():
    route = straight_route(100, 6000)  # interval 10 -> 11 points
    cps = sample_checkpoints(route, 10, max_points=11)
    assert len(cps) == 11
    assert [b.dist_mi - a.dist_mi for a, b in pairwise(cps)] == pytest.approx([10.0] * 10)


def test_eta_is_interpolated_by_distance_not_by_index():
    # 10 miles in 100 s, then 10 miles in 1000 s.
    route = make_route(
        [10 * MI, 10 * MI], [100.0, 1000.0], coords=[(40.0, -100.0), (40.5, -100.0), (41.0, -100.0)]
    )
    cps = sample_checkpoints(route, 5)
    assert [round(c.dist_mi, 6) for c in cps] == [0, 5, 10, 15, 20]
    assert [c.eta_offset_s for c in cps] == pytest.approx([0, 50, 100, 600, 1100])
    assert [c.lat for c in cps] == pytest.approx([40.0, 40.25, 40.5, 40.75, 41.0])


def test_zero_length_segments_do_not_break_interpolation():
    route = make_route(
        [10 * MI, 0.0, 0.0, 10 * MI],
        [100.0, 7.0, 7.0, 100.0],
        coords=[(40.0, -100.0), (40.5, -100.0), (40.5, -100.0), (40.5, -100.0), (41.0, -100.0)],
    )
    cps = sample_checkpoints(route, 5)
    etas = [c.eta_offset_s for c in cps]
    assert all(b >= a for a, b in pairwise(etas))
    assert etas[-1] == route.cum_dur_s[-1]
    assert all(math.isfinite(c.lat) and math.isfinite(c.lon) for c in cps)


def test_plan_distances_examples():
    assert plan_distances_mi(100, 25, 120) == [0, 25, 50, 75, 100]
    assert plan_distances_mi(0, 25, 120) == [0, 0]
    assert plan_distances_mi(10, 25, 120) == [0, 10]


@pytest.mark.parametrize("interval", [0.0, -5.0, math.nan, math.inf])
def test_bad_interval_rejected(interval):
    with pytest.raises(ValueError):
        sample_checkpoints(straight_route(10, 1), interval)


@pytest.mark.parametrize("max_points", [1, 0, -3])
def test_bad_max_points_rejected(max_points):
    with pytest.raises(ValueError):
        sample_checkpoints(straight_route(10, 1), 10, max_points=max_points)


def test_cumulative_helper_matches_manual_sum():
    assert cumulative([1.0, 2.0, 3.0]) == [0.0, 1.0, 3.0, 6.0]
