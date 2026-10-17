import math

import pytest
from routing_helpers import make_route

from routing.geodesy import (
    check_distance,
    check_route_distance,
    geodesic_length_m,
    segment_lengths_m,
)

# Reference values for WGS84: one degree of longitude on the equator is 111,319.49 m,
# one degree of latitude from the equator is 110,574.39 m.
EQUATOR_DEGREE_M = 111_319.49
LAT_DEGREE_M = 110_574.39


def test_known_lengths():
    assert geodesic_length_m([(0.0, 0.0), (0.0, 1.0)]) == pytest.approx(EQUATOR_DEGREE_M, rel=1e-4)
    assert geodesic_length_m([(0.0, 0.0), (1.0, 0.0)]) == pytest.approx(LAT_DEGREE_M, rel=1e-4)


def test_length_is_sum_of_segments_and_symmetric():
    coords = [(40.0, -100.0), (40.5, -99.0), (41.0, -98.5)]
    assert geodesic_length_m(coords) == pytest.approx(sum(segment_lengths_m(coords)))
    assert geodesic_length_m(coords) == pytest.approx(geodesic_length_m(coords[::-1]))


def test_degenerate_inputs():
    assert segment_lengths_m([]) == []
    assert segment_lengths_m([(1.0, 1.0)]) == []
    assert geodesic_length_m([(5.0, 5.0), (5.0, 5.0)]) == 0.0


def test_check_distance_within_and_outside_tolerance():
    coords = [(0.0, 0.0), (0.0, 1.0)]
    exact = geodesic_length_m(coords)
    assert check_distance(coords, exact).ok
    assert check_distance(coords, exact * 1.04, tolerance=0.05).ok
    bad = check_distance(coords, exact * 1.10, tolerance=0.05)
    assert not bad.ok
    assert bad.rel_error == pytest.approx(0.0909, abs=1e-3)
    assert bad.geodesic_m == pytest.approx(exact)
    assert bad.provider_m == pytest.approx(exact * 1.10)


def test_tolerance_parameter_controls_result():
    coords = [(0.0, 0.0), (0.0, 1.0)]
    provider = geodesic_length_m(coords) * 1.03
    assert check_distance(coords, provider, tolerance=0.05).ok
    assert not check_distance(coords, provider, tolerance=0.01).ok


def test_zero_provider_distance():
    assert check_distance([(1.0, 1.0), (1.0, 1.0)], 0.0).ok
    assert not check_distance([(0.0, 0.0), (0.0, 1.0)], 0.0).ok


@pytest.mark.parametrize("tolerance", [-0.1, math.nan, math.inf])
def test_bad_tolerance_rejected(tolerance):
    with pytest.raises(ValueError):
        check_distance([(0.0, 0.0), (0.0, 1.0)], 1000.0, tolerance=tolerance)


@pytest.mark.parametrize("provider", [-1.0, math.nan, math.inf])
def test_bad_provider_distance_rejected(provider):
    with pytest.raises(ValueError):
        check_distance([(0.0, 0.0), (0.0, 1.0)], provider)


def test_check_route_distance_uses_route_distance():
    coords = [(0.0, 0.0), (0.0, 1.0)]
    geodesic = geodesic_length_m(coords)
    route = make_route([geodesic], [100.0], coords=coords)
    assert check_route_distance(route).ok
    assert not check_route_distance(make_route([geodesic * 2], [100.0], coords=coords)).ok
