import math
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st
from routing_helpers import make_route

from routing.models import Route, join_routes


def test_valid_route_roundtrip():
    r = make_route([100.0, 200.0], [10.0, 20.0])
    assert len(r.coords) == len(r.cum_dist_m) == len(r.cum_dur_s) == 3
    assert r.cum_dist_m == [0.0, 100.0, 300.0]
    assert r.cum_dur_s == [0.0, 10.0, 30.0]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"coords": [(40.0, -100.0)], "cum_dist_m": [0.0], "cum_dur_s": [0.0]},  # < 2 points
        {"cum_dist_m": [0.0, 1.0]},  # misaligned
        {"cum_dur_s": [0.0, 1.0, 2.0, 3.0]},  # misaligned
        {"cum_dist_m": [1.0, 2.0, 3.0]},  # does not start at 0
        {"cum_dist_m": [0.0, 5.0, 4.0]},  # decreasing
        {"cum_dur_s": [0.0, math.nan, 2.0]},
        {"cum_dur_s": [0.0, 1.0, math.inf]},
        {"coords": [(91.0, 0.0), (0.0, 0.0), (0.0, 1.0)]},
        {"coords": [(0.0, 181.0), (0.0, 0.0), (0.0, 1.0)]},
        {"coords": [(math.nan, 0.0), (0.0, 0.0), (0.0, 1.0)]},
        {"distance_m": -1.0},
        {"duration_s": math.nan},
        {"eta_method": "guess"},
        {"origin_label": ""},
    ],
)
def test_invalid_routes_rejected(kwargs):
    base = {
        "coords": [(40.0, -100.0), (40.1, -100.1), (40.2, -100.2)],
        "cum_dist_m": [0.0, 1.0, 2.0],
        "cum_dur_s": [0.0, 1.0, 2.0],
        "distance_m": 2.0,
        "duration_s": 2.0,
        "eta_method": "annotations",
        "origin_label": "provider alternative",
    }
    with pytest.raises(ValueError):
        Route(**{**base, **kwargs})


def test_join_drops_duplicated_joint_point():
    a = make_route([100.0, 100.0], [10.0, 10.0], coords=[(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
    b = make_route([50.0], [5.0], coords=[(3.0, 3.0), (4.0, 4.0)])
    joined = join_routes([a, b], "forced detour via Springfield")
    assert joined.coords == [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0)]
    assert joined.cum_dist_m == [0.0, 100.0, 200.0, 250.0]
    assert joined.cum_dur_s == [0.0, 10.0, 20.0, 25.0]
    assert joined.distance_m == 250.0
    assert joined.duration_s == 25.0
    assert joined.origin_label == "forced detour via Springfield"


def test_join_single_route_relabels():
    a = make_route([100.0], [10.0])
    assert join_routes([a], "x").coords == a.coords


def test_join_empty_rejected():
    with pytest.raises(ValueError):
        join_routes([], "x")


def test_join_eta_method_is_annotations_only_if_all_pieces_are():
    a = make_route([1.0], [1.0], eta_method="annotations")
    b = make_route([1.0], [1.0], eta_method="proportional")
    assert join_routes([a, a], "x").eta_method == "annotations"
    assert join_routes([a, b], "x").eta_method == "proportional"
    assert join_routes([b, b], "x").eta_method == "proportional"


segments = st.lists(
    st.floats(min_value=0, max_value=5000, allow_nan=False), min_size=1, max_size=20
)


@given(st.lists(segments, min_size=1, max_size=5))
def test_join_keeps_cumulative_arrays_monotonic_and_aligned(pieces):
    routes = [make_route(s, s) for s in pieces]
    joined = join_routes(routes, "x")
    n_segments = sum(len(s) for s in pieces)
    assert len(joined.coords) == len(joined.cum_dist_m) == len(joined.cum_dur_s) == n_segments + 1
    assert all(b >= a for a, b in pairwise(joined.cum_dist_m))
    assert all(b >= a for a, b in pairwise(joined.cum_dur_s))
    assert math.isclose(
        joined.cum_dist_m[-1], sum(sum(s) for s in pieces), rel_tol=1e-9, abs_tol=1e-6
    )
