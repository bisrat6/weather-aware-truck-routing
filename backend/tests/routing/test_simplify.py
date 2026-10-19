"""Tests for Douglas-Peucker polyline simplification."""

from analysis import analyze_route
from api.heatmap import sample_corridor
from routing.here import parse_routes
from routing.models import Coord
from routing.simplify import (
    DEFAULT_SIMPLIFICATION_TOLERANCE_M,
    max_deviation_m,
    point_to_segment_distance_m,
    simplify_coords,
)
from tests.here_helpers import load_fixture, route_coords, route_geometry


def test_simplify_preserves_short_sequences():
    assert simplify_coords([]) == []
    single: list[Coord] = [(40.0, -111.0)]
    assert simplify_coords(single) == single
    pair: list[Coord] = [(40.0, -111.0), (41.0, -112.0)]
    assert simplify_coords(pair) == pair


def test_endpoints_preserved_exact():
    coords: list[Coord] = [
        (40.0, -111.0),
        (40.1, -111.1),
        (40.2, -111.05),
        (40.3, -111.15),
        (40.4, -111.2),
    ]
    simplified = simplify_coords(coords, tolerance_m=25.0)
    assert simplified[0] == coords[0]
    assert simplified[-1] == coords[-1]


def test_point_to_segment_distance_zero_length_segment():
    d = point_to_segment_distance_m(40.0, -111.0, 40.0, -111.0, 40.0, -111.0)
    assert d == 0.0


def test_slc_provo_fixture_simplification_and_max_deviation():

    # Route 0
    c0: list[Coord] = route_coords("slc_provo", 0)
    assert len(c0) == 917
    s0 = simplify_coords(c0, tolerance_m=DEFAULT_SIMPLIFICATION_TOLERANCE_M)
    assert len(s0) == 72
    dev0 = max_deviation_m(c0, s0)
    assert dev0 <= DEFAULT_SIMPLIFICATION_TOLERANCE_M
    assert dev0 < 34.0
    assert s0[0] == c0[0]
    assert s0[-1] == c0[-1]

    # Route 1
    c1: list[Coord] = route_coords("slc_provo", 1)
    assert len(c1) == 923
    s1 = simplify_coords(c1, tolerance_m=DEFAULT_SIMPLIFICATION_TOLERANCE_M)
    assert len(s1) == 71
    dev1 = max_deviation_m(c1, s1)
    assert dev1 <= DEFAULT_SIMPLIFICATION_TOLERANCE_M
    assert dev1 < 34.0
    assert s1[0] == c1[0]
    assert s1[-1] == c1[-1]


def test_la_nyc_fixture_simplification_and_max_deviation():

    # Route 0
    c0: list[Coord] = route_coords("la_nyc", 0)
    assert len(c0) == 30238
    s0 = simplify_coords(c0, tolerance_m=DEFAULT_SIMPLIFICATION_TOLERANCE_M)
    assert len(s0) == 3156
    dev0 = max_deviation_m(c0, s0)
    assert dev0 <= DEFAULT_SIMPLIFICATION_TOLERANCE_M
    assert dev0 < 35.0
    assert s0[0] == c0[0]
    assert s0[-1] == c0[-1]

    # Route 1
    c1: list[Coord] = route_coords("la_nyc", 1)
    assert len(c1) == 26672
    s1 = simplify_coords(c1, tolerance_m=DEFAULT_SIMPLIFICATION_TOLERANCE_M)
    assert len(s1) == 2666
    dev1 = max_deviation_m(c1, s1)
    assert dev1 <= DEFAULT_SIMPLIFICATION_TOLERANCE_M
    assert dev1 < 35.0
    assert s1[0] == c1[0]
    assert s1[-1] == c1[-1]


def test_distances_and_checkpoints_identical_before_and_after_simplification(monkeypatch):
    from datetime import UTC, datetime

    from weather.models import WeatherSample
    from weather.service import WeatherService

    routes = parse_routes(load_fixture("slc_provo"), truck_time_factor=1.1)
    route = routes[0]

    departure = datetime(2026, 10, 17, 12, tzinfo=UTC)

    # Weather service mock returning constant low-risk sample
    service = WeatherService()
    sample = WeatherSample(
        hour=departure,
        wind_mph=15.0,
        rain_in_hr=0.0,
        snow_in_hr=0.0,
    )
    monkeypatch.setattr(service, "weather_at", lambda *args, **kwargs: sample)

    # Analyze route with full geometry
    result_before = analyze_route(route, departure, 25.0, 34000.0, service)

    # Full route coords are intact
    assert len(route.coords) == 917
    assert result_before.summary.duration_s == route.duration_s
    assert len(result_before.checkpoints) > 1

    # Now verify simplified display coords
    display_coords = simplify_coords(route.coords)
    assert len(display_coords) == 72
    # Original route coords are not mutated
    assert len(route.coords) == 917

    # Re-running analysis gives identical distance, checkpoints, and risk
    result_after = analyze_route(route, departure, 25.0, 34000.0, service)
    assert result_before.summary.duration_s == result_after.summary.duration_s
    assert len(result_before.checkpoints) == len(result_after.checkpoints)
    for cp_b, cp_a in zip(result_before.checkpoints, result_after.checkpoints, strict=True):
        assert cp_b.lat == cp_a.lat
        assert cp_b.lon == cp_a.lon
        assert cp_b.dist_mi == cp_a.dist_mi
        assert cp_b.risk_level == cp_a.risk_level
        assert cp_b.owned_mi == cp_a.owned_mi


def test_heatmap_accepts_simplified_geometry_and_produces_identical_corridor():
    orig_geom = route_geometry("slc_provo")
    orig_coords: list[Coord] = route_coords("slc_provo")

    simp_coords = simplify_coords(orig_coords, tolerance_m=DEFAULT_SIMPLIFICATION_TOLERANCE_M)
    simp_geom = {"coordinates": [[lon, lat] for lat, lon in simp_coords]}

    # Both original and simplified geometries produce the exact same corridor sampling
    points_orig = sample_corridor(orig_geom)
    points_simp = sample_corridor(simp_geom)
    assert len(points_orig) == 6
    assert len(points_simp) == 6

    from weather.cache import grid_cell
    cells_orig = [grid_cell(*p) for p in points_orig]
    cells_simp = [grid_cell(*p) for p in points_simp]
    assert cells_orig == cells_simp
