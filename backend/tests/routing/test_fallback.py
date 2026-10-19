import time

import pytest
import requests
from pyproj import Geod

from routing.fallback import (
    BUDGET_S,
    MAX_EXTRA_REQUESTS,
    MAX_OFFSET_MI,
    MIN_OFFSET_MI,
    REQUEST_TIMEOUT_S,
    augment_routes,
    build_candidates,
    grid_cells,
    offset_miles,
    via_points,
)
from routing.mapbox import PROVIDER_LABEL, parse_routes
from routing.models import Route, cumulative
from tests.fallback_support import (
    EXCLUDE,
    MAIN,
    VIA,
    Directions,
    endpoints,
    first_route_only,
    load_payload,
    main_routes,
    scaled,
    via_key,
)

GEOD = Geod(ellps="WGS84")
MILE = 1609.344
FACTOR = 1.1


@pytest.fixture
def run(monkeypatch):
    """run(directions, routes=None, **kw) -> (routes, report); patches requests.get."""

    def go(directions, routes=None, **kwargs):
        monkeypatch.setattr("requests.get", directions)
        origin, dest = endpoints()
        return augment_routes(
            routes if routes is not None else main_routes(),
            origin,
            dest,
            "token",
            truck_time_factor=FACTOR,
            **kwargs,
        )

    return go


def via_keys():
    return [via_key(point) for _, point in via_points(main_routes()[0])]


def line_route(coords):
    """Route along straight geodesic segments with consistent cumulative distances."""
    lats, lons = [c[0] for c in coords], [c[1] for c in coords]
    _, _, seg = GEOD.inv(lons[:-1], lats[:-1], lons[1:], lats[1:])
    cum = cumulative(seg)
    return Route(
        coords,
        cum,
        cumulative([s / 25 for s in seg]),
        cum[-1],
        cum[-1] / 25,
        "annotations",
        PROVIDER_LABEL,
    )


# ----------------------------------------------------------------------------- acceptance


def test_distinct_motorway_avoiding_third_route_is_accepted_and_labelled(run):
    fast_exclude = scaled(load_payload(EXCLUDE), duration=0.5)  # recorded geometry, <1.5x
    routes, report = run(Directions({"exclude": fast_exclude}))
    assert [r.origin_label for r in routes] == [
        PROVIDER_LABEL,
        PROVIDER_LABEL,
        "avoiding motorways",
    ]
    assert (report.attempted, report.provider_count, report.accepted) == (True, 2, 1)


def test_distinct_detour_is_accepted_and_labelled_left_of_the_main_route(run):
    left_key = via_keys()[0]
    detour = scaled(load_payload(VIA), duration=0.6)
    routes, report = run(Directions({left_key: detour}))
    assert routes[2].origin_label == "forced detour, left of the main route"
    assert len(routes) == 3 and report.accepted == 1


def test_candidate_sharing_85_percent_of_cells_is_rejected(run):
    routes, report = run(Directions({}, default=load_payload(MAIN)))
    assert len(routes) == 2
    assert report.accepted == 0 and report.rejected_similar >= 1


def test_recorded_via_route_shares_under_85_percent_of_cells_with_each_provider_route():
    main = main_routes()
    cells = [grid_cells(r.coords) for r in main]
    recorded_via = parse_routes(load_payload(VIA))[0]
    shared = [
        len(grid_cells(recorded_via.coords) & c) / len(grid_cells(recorded_via.coords))
        for c in cells
    ]
    assert max(shared) < 0.85  # so only duration rejects it in the too-long test


def test_candidate_over_1_5x_duration_is_rejected(run):
    # Recorded motorway-exclusion route is 2.1x the best duration, recorded via route 1.65x.
    routes, report = run(
        Directions({"exclude": load_payload(EXCLUDE), via_keys()[0]: load_payload(VIA)})
    )
    assert len(routes) == 2
    assert report.accepted == 0 and report.rejected_too_long == 3  # 2 exclude routes + via


def test_candidate_over_1_5x_distance_is_rejected(run):
    far = scaled(load_payload(VIA), duration=0.6, distance=1.6)
    routes, report = run(Directions({via_keys()[0]: far}))
    assert len(routes) == 2 and report.rejected_too_long == 1


# ----------------------------------------------------------------------------- failures


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("slow"),
        requests.ConnectionError("down"),
        (429, {"message": "slow down"}),
        (500, {}),
        (200, {"code": "NoRoute"}),
        (200, {"code": "Ok", "routes": []}),
    ],
)
def test_every_fallback_failure_still_returns_the_main_routes(run, failure):
    directions = Directions({}, default=failure)
    routes, report = run(directions)
    assert [r.origin_label for r in routes] == [PROVIDER_LABEL, PROVIDER_LABEL]
    assert report.attempted and report.accepted == 0 and report.errors == 5


def test_total_budget_returns_without_waiting_for_slow_requests(run):
    slow = {key: 1.0 for key in ["exclude", *via_keys()]}
    started = time.monotonic()
    routes, report = run(Directions({}, default=load_payload(MAIN), delays=slow), budget_s=0.2)
    assert time.monotonic() - started < 0.9
    assert len(routes) == 2 and report.errors == 5


def test_a_fast_candidate_is_used_even_when_another_exceeds_the_budget(run):
    left_key = via_keys()[0]
    directions = Directions(
        {left_key: scaled(load_payload(VIA), duration=0.6)}, delays={"exclude": 1.0}
    )
    routes, report = run(directions, budget_s=0.4)
    assert [r.origin_label for r in routes][-1] == "forced detour, left of the main route"
    assert report.errors >= 1


def test_no_provider_routes_means_no_fallback_and_no_error(run):
    routes, report = run(Directions({}), routes=[])
    assert routes == [] and not report.attempted


# ----------------------------------------------------------------------------- request caps


def test_at_most_five_extra_requests_and_none_when_the_provider_returned_three(run):
    directions = Directions({}, default=(500, {}))
    run(directions)
    assert len(directions.extra_calls) == MAX_EXTRA_REQUESTS == 5
    assert sorted(directions.extra_calls) == sorted(["exclude", *via_keys()])

    three = [*main_routes(), main_routes()[0]]
    directions = Directions({}, default=load_payload(MAIN))
    routes, report = run(directions, routes=three)
    assert directions.calls == [] and not report.attempted and len(routes) == 3


def test_a_single_provider_route_can_gain_two(run):
    one = first_route_only(load_payload(MAIN))
    directions = Directions(
        {"exclude": scaled(load_payload(EXCLUDE), duration=0.5)}, default=(500, {})
    )
    routes, report = run(directions, routes=parse_routes(one))
    assert len(routes) == 3 and report.provider_count == 1 and report.accepted == 2
    assert [r.origin_label for r in routes[1:]] == ["avoiding motorways"] * 2


def test_request_timeout_and_budget_constants():
    assert REQUEST_TIMEOUT_S == 6.0 and BUDGET_S == 8.0


# ----------------------------------------------------------------------------- determinism


def test_accepted_order_follows_the_fixed_candidate_order_not_completion_order(run):
    left_key, right_key = via_keys()[:2]
    responses = {
        left_key: scaled(load_payload(VIA), duration=0.6),
        right_key: scaled(load_payload(EXCLUDE), duration=0.5),
        "exclude": (500, {}),
    }
    one = parse_routes(first_route_only(load_payload(MAIN)))
    for delays in ({left_key: 0.3}, {right_key: 0.3}):
        routes, _ = run(Directions(responses, default=(500, {}), delays=delays), routes=one)
        assert [r.origin_label for r in routes[1:]] == [
            "forced detour, left of the main route",
            "forced detour, right of the main route",
        ]


def test_candidate_order_labels_and_offsets():
    main = main_routes()[0]
    candidates = build_candidates(main)
    assert [c.label for c in candidates] == [
        "avoiding motorways",
        "forced detour, left of the main route",
        "forced detour, right of the main route",
        "forced detour, left of the main route",
        "forced detour, right of the main route",
    ]
    assert candidates[0].exclude == "motorway" and candidates[0].alternatives is True
    assert all(c.via is None or c.alternatives is False for c in candidates)


# ----------------------------------------------------------------------------- geometry


def test_via_points_are_perpendicular_to_the_midpoint_by_cumulative_distance():
    # 10 mi north, then 30 mi east: the distance midpoint (20 mi) is 10 mi along the east leg.
    corner_lat = 40.0 + 10 * MILE / 111_000
    lon_east = -111.0 + 0.566
    route = line_route([(40.0, -111.0), (corner_lat, -111.0), (corner_lat, lon_east)])
    length = route.cum_dist_m[-1]
    assert route.cum_dist_m[1] / length == pytest.approx(0.25, abs=0.01)
    mid_lon = -111.0 + 0.566 / 3  # 10 mi of the 30-mi east leg
    points = via_points(route)
    assert [side for side, _ in points] == ["left", "right", "left", "right"]
    for (side, (lat, lon)), fraction in zip(points, (0.08, 0.08, 0.16, 0.16), strict=True):
        distance = GEOD.inv(mid_lon, corner_lat, lon, lat)[2] / MILE
        assert distance == pytest.approx(offset_miles(fraction, length), rel=0.03)
        assert (lat > corner_lat) == (side == "left")  # heading east: left is north
        assert lon == pytest.approx(mid_lon, abs=0.01)


def test_offsets_are_8_and_16_percent_clamped_to_1_5_to_35_miles():
    assert MIN_OFFSET_MI == 1.5 and MAX_OFFSET_MI == 35.0
    assert offset_miles(0.08, 10 * MILE) == 1.5  # 0.8 mi clamps up
    assert offset_miles(0.08, 45.6 * MILE) == pytest.approx(3.648)
    assert offset_miles(0.16, 45.6 * MILE) == pytest.approx(7.296)
    assert offset_miles(0.16, 1000 * MILE) == 35.0  # 160 mi clamps down


def test_degenerate_route_gets_only_the_motorway_candidate():
    route = line_route([(40.0, -111.0), (40.0, -111.0)])
    assert via_points(route) == []
    assert [c.label for c in build_candidates(route)] == ["avoiding motorways"]


def test_grid_cells_densify_long_segments():
    cells = grid_cells([(40.0, -111.0), (40.5, -111.0)])
    assert len({lat for lat, _ in cells}) >= 50  # 0.5 degrees spans 50 one-hundredth cells

