import copy
import json
import logging
import math
from itertools import pairwise
from pathlib import Path

import pytest
import requests
from hypothesis import example, given
from hypothesis import strategies as st

from routing import mapbox
from routing.checkpoints import sample_checkpoints
from routing.errors import (
    MapboxAuthError,
    MapboxConnectionError,
    MapboxError,
    MapboxNoRouteError,
    MapboxRateLimitError,
    MapboxRequestError,
    MapboxResponseError,
    MapboxServerError,
    MapboxTimeoutError,
    RoutingError,
)
from routing.geodesy import segment_lengths_m
from routing.mapbox import get_routes, parse_routes

FIXTURE = Path(__file__).parent.parent / "fixtures" / "mapbox_handbuilt_slc_provo.json"
TOKEN = "pk.SECRET-TOKEN-123"
ORIGIN, DEST = (40.7608, -111.8910), (40.2338, -111.6585)


def load_payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status=200, body=None, text="", headers=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad_json = bad_json
        self.text = text or (json.dumps(body) if body is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


@pytest.fixture
def fake_get(monkeypatch):
    """Install a fake requests.get; returns a dict recording the call."""
    state = {"calls": []}

    def install(response=None, raises=None):
        def _get(url, params=None, timeout=None):
            state["calls"].append({"url": url, "params": params, "timeout": timeout})
            if raises is not None:
                raise raises
            return response

        monkeypatch.setattr(mapbox.requests, "get", _get)
        return state

    return install


# ------------------------------------------------------------------ request building


def test_request_url_params_and_timeout(fake_get):
    state = fake_get(FakeResponse(body=load_payload()))
    get_routes(ORIGIN, DEST, TOKEN, timeout=7.5)
    (call,) = state["calls"]
    # Mapbox wants lon,lat in the path.
    assert call["url"].endswith("/driving/-111.891000,40.760800;-111.658500,40.233800")
    assert call["params"] == {
        "alternatives": "true",
        "overview": "full",
        "geometries": "geojson",
        "annotations": "distance,duration",
        "access_token": TOKEN,
    }
    assert call["timeout"] == 7.5


def test_default_timeout_is_set(fake_get):
    state = fake_get(FakeResponse(body=load_payload()))
    get_routes(ORIGIN, DEST, TOKEN)
    assert state["calls"][0]["timeout"] == mapbox.DEFAULT_TIMEOUT
    assert state["calls"][0]["timeout"] is not None


@pytest.mark.parametrize(
    ("origin", "dest", "token"),
    [
        ((91.0, 0.0), DEST, TOKEN),
        (ORIGIN, (0.0, 181.0), TOKEN),
        ((math.nan, 0.0), DEST, TOKEN),
        (ORIGIN, DEST, ""),
        ("nope", DEST, TOKEN),
    ],
)
def test_bad_arguments_raise_value_error_before_any_request(fake_get, origin, dest, token):
    state = fake_get(FakeResponse(body=load_payload()))
    with pytest.raises(ValueError):
        get_routes(origin, dest, token)
    assert state["calls"] == []


@pytest.mark.parametrize("factor", [0.0, -1.0, math.nan, math.inf])
def test_bad_truck_factor_rejected(fake_get, factor):
    fake_get(FakeResponse(body=load_payload()))
    with pytest.raises(ValueError):
        get_routes(ORIGIN, DEST, TOKEN, truck_time_factor=factor)


# ------------------------------------------------------------------ happy path


def test_parses_fixture_routes(fake_get):
    fake_get(FakeResponse(body=load_payload()))
    routes = get_routes(ORIGIN, DEST, TOKEN)
    raw = load_payload()["routes"]
    assert len(routes) == len(raw) == 2
    for route, r in zip(routes, raw, strict=True):
        raw_coords = r["geometry"]["coordinates"]
        # GeoJSON [lon, lat] becomes (lat, lon).
        assert route.coords[0] == (raw_coords[0][1], raw_coords[0][0])
        assert len(route.coords) == len(raw_coords)
        assert len(route.cum_dist_m) == len(route.cum_dur_s) == len(route.coords)
        assert route.eta_method == "annotations"
        assert route.origin_label == "provider alternative"
        assert route.distance_m == pytest.approx(r["distance"])
        assert route.cum_dist_m[-1] == pytest.approx(r["distance"], rel=1e-3)


def test_default_truck_factor_is_1_1():
    assert mapbox.DEFAULT_TRUCK_TIME_FACTOR == 1.1
    raw = load_payload()
    default = parse_routes(raw)[0]
    explicit = parse_routes(raw, truck_time_factor=1.1)[0]
    assert default.cum_dur_s == explicit.cum_dur_s


@pytest.mark.parametrize("factor", [1.0, 1.1, 1.5])
def test_truck_factor_scales_durations_not_distances(factor):
    raw = load_payload()
    base = parse_routes(raw, truck_time_factor=1.0)[0]
    scaled = parse_routes(raw, truck_time_factor=factor)[0]
    assert scaled.duration_s == pytest.approx(raw["routes"][0]["duration"] * factor)
    assert scaled.cum_dur_s[-1] == pytest.approx(base.cum_dur_s[-1] * factor)
    assert scaled.cum_dist_m == base.cum_dist_m
    assert scaled.distance_m == base.distance_m


# ------------------------------------------------------------------ error mapping


def _status(status, body=None, **kw):
    return FakeResponse(status=status, body=body if body is not None else {}, **kw)


@pytest.mark.parametrize(
    ("response", "raises", "expected"),
    [
        (None, requests.Timeout("slow"), MapboxTimeoutError),
        (None, requests.ConnectTimeout("slow"), MapboxTimeoutError),
        (None, requests.ConnectionError("down"), MapboxConnectionError),
        (None, requests.RequestException("odd"), MapboxConnectionError),
        (_status(401, {"message": "Not Authorized - Invalid Token"}), None, MapboxAuthError),
        (_status(403, {"message": "Forbidden"}), None, MapboxAuthError),
        (
            _status(429, {"message": "Too many requests"}, headers={"Retry-After": "12"}),
            None,
            MapboxRateLimitError,
        ),
        (_status(500, {"message": "boom"}), None, MapboxServerError),
        (_status(503, text="Service Unavailable", body={}), None, MapboxServerError),
        (
            _status(422, {"code": "InvalidInput", "message": "Bad coordinates"}),
            None,
            MapboxRequestError,
        ),
        (_status(404, {"message": "Not Found"}), None, MapboxRequestError),
        (_status(422, {"code": "NoRoute", "message": "No route found"}), None, MapboxNoRouteError),
        (
            FakeResponse(body={"code": "NoRoute", "message": "No route found"}),
            None,
            MapboxNoRouteError,
        ),
        (
            FakeResponse(body={"code": "NoSegment", "message": "Could not snap"}),
            None,
            MapboxNoRouteError,
        ),
        (FakeResponse(body={"code": "Ok", "routes": []}), None, MapboxNoRouteError),
        (FakeResponse(body={"code": "ProfileNotFound"}), None, MapboxRequestError),
        (FakeResponse(body={"routes": []}), None, MapboxRequestError),  # no code at all
        (FakeResponse(bad_json=True, text="<html>"), None, MapboxResponseError),
        (FakeResponse(body=["not", "an", "object"]), None, MapboxResponseError),
    ],
)
def test_error_mapping(fake_get, response, raises, expected):
    fake_get(response, raises)
    with pytest.raises(expected) as info:
        get_routes(ORIGIN, DEST, TOKEN)
    assert isinstance(info.value, MapboxError)
    assert isinstance(info.value, RoutingError)


def test_error_attributes(fake_get):
    fake_get(_status(429, {"message": "slow down"}, headers={"Retry-After": "12"}))
    with pytest.raises(MapboxRateLimitError) as info:
        get_routes(ORIGIN, DEST, TOKEN)
    assert info.value.retry_after == 12.0
    assert info.value.status_code == 429

    fake_get(_status(401, {"message": "bad token"}))
    with pytest.raises(MapboxAuthError) as info:
        get_routes(ORIGIN, DEST, TOKEN)
    assert info.value.status_code == 401

    fake_get(_status(422, {"code": "InvalidInput", "message": "Bad coordinates"}))
    with pytest.raises(MapboxRequestError) as info:
        get_routes(ORIGIN, DEST, TOKEN)
    assert info.value.code == "InvalidInput"
    assert "Bad coordinates" in str(info.value)


def test_rate_limit_without_retry_after(fake_get):
    fake_get(_status(429, {"message": "x"}))
    with pytest.raises(MapboxRateLimitError) as info:
        get_routes(ORIGIN, DEST, TOKEN)
    assert info.value.retry_after is None


@pytest.mark.parametrize(
    "raises",
    [
        requests.Timeout(f"https://api.mapbox.com/x?access_token={TOKEN}"),
        requests.ConnectionError(f"HTTPSConnectionPool ... access_token={TOKEN}"),
    ],
)
def test_token_never_leaks_from_network_errors(fake_get, raises):
    fake_get(None, raises)
    with pytest.raises(MapboxError) as info:
        get_routes(ORIGIN, DEST, TOKEN)
    assert TOKEN not in str(info.value)
    # Not chained: the original exception text contains the token.
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_token_is_redacted_from_api_messages(fake_get):
    fake_get(_status(401, {"message": f"Invalid token {TOKEN}"}))
    with pytest.raises(MapboxAuthError) as info:
        get_routes(ORIGIN, DEST, TOKEN)
    assert TOKEN not in str(info.value)


def test_malformed_route_shapes_map_to_response_error(fake_get):
    broken = load_payload()
    del broken["routes"][0]["geometry"]
    fake_get(FakeResponse(body=broken))
    with pytest.raises(MapboxResponseError):
        get_routes(ORIGIN, DEST, TOKEN)

    broken = load_payload()
    broken["routes"][0]["legs"] = []
    fake_get(FakeResponse(body=broken))
    with pytest.raises(MapboxResponseError):
        get_routes(ORIGIN, DEST, TOKEN)

    broken = load_payload()
    broken["routes"][0]["geometry"]["coordinates"] = [[-111.0, 40.0]]  # single point
    fake_get(FakeResponse(body=broken))
    with pytest.raises(MapboxResponseError):
        get_routes(ORIGIN, DEST, TOKEN)

    broken = load_payload()
    broken["routes"][0]["geometry"]["coordinates"][3] = ["x", "y"]
    fake_get(FakeResponse(body=broken))
    with pytest.raises(MapboxResponseError):
        get_routes(ORIGIN, DEST, TOKEN)


def test_out_of_range_coordinates_in_response_map_to_response_error():
    broken = load_payload()
    broken["routes"][0]["geometry"]["coordinates"][2] = [500.0, 95.0]
    with pytest.raises(MapboxResponseError):
        parse_routes(broken)


# ------------------------------------------------------------------ annotation fallback


def _tamper(mutator, route_index=0):
    raw = load_payload()
    mutator(raw["routes"][route_index]["legs"][0])
    return raw


def _assert_proportional(raw, factor=1.1, route_index=0):
    routes = parse_routes(raw, truck_time_factor=factor)
    route = routes[route_index]
    r = raw["routes"][route_index]
    assert route.eta_method == "proportional"
    assert len(route.coords) == len(route.cum_dist_m) == len(route.cum_dur_s)
    # Totals come from the provider's route-level numbers.
    assert route.cum_dist_m[-1] == pytest.approx(r["distance"])
    assert route.cum_dur_s[-1] == pytest.approx(r["duration"] * factor)
    assert route.distance_m == pytest.approx(r["distance"])
    assert route.duration_s == pytest.approx(r["duration"] * factor)
    assert all(b >= a for a, b in pairwise(route.cum_dist_m))
    assert all(b >= a for a, b in pairwise(route.cum_dur_s))
    return routes


@pytest.mark.parametrize(
    "mutator",
    [
        lambda leg: leg["annotation"]["distance"].pop(),
        lambda leg: leg["annotation"]["duration"].pop(),
        lambda leg: leg["annotation"]["distance"].append(10.0),
        lambda leg: leg["annotation"]["duration"].append(1.0),
        lambda leg: leg["annotation"].update(distance=[], duration=[]),
        lambda leg: leg["annotation"].pop("duration"),
        lambda leg: leg["annotation"].pop("distance"),
        lambda leg: leg.pop("annotation"),
        lambda leg: leg.update(annotation=None),
        lambda leg: leg["annotation"]["duration"].__setitem__(2, -5.0),
        lambda leg: leg["annotation"]["distance"].__setitem__(2, math.nan),
        lambda leg: leg["annotation"]["duration"].__setitem__(2, None),
        lambda leg: leg["annotation"]["distance"].__setitem__(2, "12"),
    ],
    ids=[
        "distance-short",
        "duration-short",
        "distance-long",
        "duration-long",
        "empty",
        "no-duration",
        "no-distance",
        "no-annotation",
        "annotation-null",
        "negative",
        "nan",
        "null-entry",
        "string-entry",
    ],
)
def test_annotation_mismatch_falls_back_to_proportional(mutator, caplog):
    raw = _tamper(mutator)
    with caplog.at_level(logging.WARNING, logger="routing.mapbox"):
        routes = _assert_proportional(raw)
    # The untouched alternative keeps its annotations.
    assert routes[1].eta_method == "annotations"
    assert any("proportional" in rec.getMessage() for rec in caplog.records)


def test_exactly_matching_annotation_count_is_accepted():
    raw = load_payload()
    n = len(raw["routes"][0]["geometry"]["coordinates"])
    assert len(raw["routes"][0]["legs"][0]["annotation"]["distance"]) == n - 1
    assert parse_routes(raw)[0].eta_method == "annotations"


def test_proportional_durations_follow_segment_distance():
    # Two segments along the equator, 1 degree and 3 degrees long: durations split 1:3.
    raw = {
        "code": "Ok",
        "routes": [
            {
                "distance": 4000.0,
                "duration": 400.0,
                "geometry": {"coordinates": [[0.0, 0.0], [1.0, 0.0], [4.0, 0.0]]},
                "legs": [{"distance": 4000.0, "duration": 400.0, "annotation": {"distance": [1]}}],
            }
        ],
    }
    (route,) = parse_routes(raw, truck_time_factor=1.0)
    assert route.eta_method == "proportional"
    seg_dur = [b - a for a, b in pairwise(route.cum_dur_s)]
    seg_dist = [b - a for a, b in pairwise(route.cum_dist_m)]
    assert seg_dur[1] / seg_dur[0] == pytest.approx(3.0, rel=1e-3)
    assert seg_dist[1] / seg_dist[0] == pytest.approx(3.0, rel=1e-3)
    assert route.cum_dur_s[-1] == pytest.approx(400.0)


def test_proportional_with_zero_length_route_splits_evenly():
    raw = {
        "code": "Ok",
        "routes": [
            {
                "distance": 0.0,
                "duration": 60.0,
                "geometry": {"coordinates": [[5.0, 5.0], [5.0, 5.0], [5.0, 5.0]]},
                "legs": [{"distance": 0.0, "duration": 60.0}],
            }
        ],
    }
    (route,) = parse_routes(raw, truck_time_factor=1.0)
    assert route.cum_dur_s == pytest.approx([0.0, 30.0, 60.0])
    assert route.cum_dist_m == [0.0, 0.0, 0.0]


def test_no_annotation_and_no_duration_is_a_response_error():
    raw = {
        "code": "Ok",
        "routes": [
            {
                "geometry": {"coordinates": [[0.0, 0.0], [1.0, 0.0]]},
                "legs": [{"distance": 100.0}],
            }
        ],
    }
    with pytest.raises(MapboxResponseError):
        parse_routes(raw)


# ------------------------------------------------------------------ multi-leg joining


def _two_leg_payload(count_a, count_b, tamper_second=False):
    """One route whose geometry has count_a + count_b segments, in two legs."""
    n_points = count_a + count_b + 1
    coords = [[-100.0 + 0.1 * i, 40.0 + 0.05 * i] for i in range(n_points)]
    legs = [
        {
            "distance": 100.0 * count_a,
            "duration": 10.0 * count_a,
            "annotation": {"distance": [100.0] * count_a, "duration": [10.0] * count_a},
        },
        {
            "distance": 100.0 * count_b,
            "duration": 10.0 * count_b,
            "annotation": {
                "distance": [100.0] * count_b,
                "duration": [10.0] * (count_b - 1 if tamper_second else count_b),
            },
        },
    ]
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": 100.0 * (count_a + count_b),
                "duration": 10.0 * (count_a + count_b),
                "geometry": {"coordinates": coords},
                "legs": legs,
            }
        ],
    }, coords


def test_multi_leg_joint_point_is_not_duplicated():
    raw, coords = _two_leg_payload(3, 4)
    (route,) = parse_routes(raw, truck_time_factor=1.0)
    assert len(route.coords) == len(coords)  # 8 points, joint point appears once
    assert route.coords == [(lat, lon) for lon, lat in coords]
    assert route.eta_method == "annotations"
    assert route.cum_dist_m == pytest.approx([100.0 * i for i in range(8)])
    assert route.cum_dur_s == pytest.approx([10.0 * i for i in range(8)])


def test_multi_leg_with_one_bad_leg_is_proportional_overall():
    raw, coords = _two_leg_payload(3, 4, tamper_second=True)
    # The second leg's annotation is inconsistent, so leg boundaries are still derivable
    # only from the distance list; the duration list is the one that is wrong.
    (route,) = parse_routes(raw, truck_time_factor=1.0)
    assert route.eta_method == "proportional"
    assert len(route.coords) == len(coords)
    assert route.cum_dur_s[-1] == pytest.approx(70.0)
    assert all(b >= a for a, b in pairwise(route.cum_dur_s))


def test_multi_leg_with_unsplittable_geometry_is_one_proportional_leg():
    raw, coords = _two_leg_payload(3, 4)
    original_count = len(coords)  # `coords` is the payload's own list, so append mutates it
    raw["routes"][0]["geometry"]["coordinates"].append([-90.0, 50.0])  # counts no longer add up
    (route,) = parse_routes(raw, truck_time_factor=1.0)
    assert route.eta_method == "proportional"
    assert len(route.coords) == original_count + 1
    assert route.cum_dur_s[-1] == pytest.approx(70.0)


# ------------------------------------------------------------------ properties


@st.composite
def payloads(draw):
    n_points = draw(st.integers(min_value=2, max_value=40))
    coords = [[draw(st.floats(-179, 179)), draw(st.floats(-80, 80))] for _ in range(n_points)]
    n_seg = n_points - 1
    mode = draw(st.sampled_from(["match", "short", "long", "missing"]))
    n_ann = {"match": n_seg, "short": max(0, n_seg - 1), "long": n_seg + 1, "missing": 0}[mode]
    dist = [draw(st.floats(0, 5000)) for _ in range(n_ann)]
    dur = [draw(st.floats(0, 600)) for _ in range(n_ann)]
    leg = {"distance": draw(st.floats(0, 1e6)), "duration": draw(st.floats(0, 1e5))}
    if mode != "missing":
        leg["annotation"] = {"distance": dist, "duration": dur}
    route = {
        "distance": leg["distance"],
        "duration": leg["duration"],
        "geometry": {"coordinates": coords},
        "legs": [leg],
    }
    return {"code": "Ok", "routes": [route]}, mode


@example(
    case=(
        {
            "code": "Ok",
            "routes": [
                {
                    "distance": 5e-324,
                    "duration": 1.5,
                    "geometry": {"coordinates": [[0.0, 0.0], [0.0, 0.0]]},
                    "legs": [
                        {
                            "distance": 5e-324,
                            "duration": 1.5,
                            "annotation": {"distance": [], "duration": []},
                        }
                    ],
                }
            ],
        },
        "short",
    ),
    factor=1.0,
)
@given(payloads(), st.floats(min_value=0.5, max_value=3.0))
def test_parsed_routes_always_aligned_and_monotonic(case, factor):
    payload, mode = case
    (route,) = parse_routes(copy.deepcopy(payload), truck_time_factor=factor)
    n = len(payload["routes"][0]["geometry"]["coordinates"])
    assert len(route.coords) == len(route.cum_dist_m) == len(route.cum_dur_s) == n
    assert all(b >= a for a, b in pairwise(route.cum_dist_m))
    assert all(b >= a for a, b in pairwise(route.cum_dur_s))
    assert route.eta_method == ("annotations" if mode == "match" else "proportional")
    if mode != "match":
        r = payload["routes"][0]
        assert route.cum_dist_m[-1] == pytest.approx(r["distance"], rel=1e-9, abs=1e-6)
        assert route.cum_dur_s[-1] == pytest.approx(r["duration"] * factor, rel=1e-9, abs=1e-6)


def test_proportional_split_subnormal_distance_underflow():
    payload = {
        "code": "Ok",
        "routes": [
            {
                "distance": 5e-324,
                "duration": 1.5,
                "geometry": {"coordinates": [[0.0, 0.0], [0.0, 0.0]]},
                "legs": [
                    {
                        "distance": 5e-324,
                        "duration": 1.5,
                        "annotation": {"distance": [], "duration": []},
                    }
                ],
            }
        ],
    }
    (route,) = parse_routes(payload, truck_time_factor=1.0)
    assert route.eta_method == "proportional"
    assert route.cum_dist_m == pytest.approx([0.0, 5e-324], abs=1e-320)
    assert route.cum_dur_s == pytest.approx([0.0, 1.5])
    assert route.cum_dur_s[-1] == pytest.approx(1.5)


def test_proportional_split_tiny_nonzero_distance():
    payload = {
        "code": "Ok",
        "routes": [
            {
                "distance": 1e-9,
                "duration": 2.0,
                "geometry": {"coordinates": [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]},
                "legs": [{"distance": 1e-9, "duration": 2.0}],
            }
        ],
    }
    (route,) = parse_routes(payload, truck_time_factor=1.1)
    assert route.eta_method == "proportional"
    assert len(route.coords) == 3
    assert route.cum_dist_m == pytest.approx([0.0, 5e-10, 1e-9])
    assert route.cum_dur_s == pytest.approx([0.0, 1.1, 2.2])
    assert route.cum_dur_s[-1] == pytest.approx(2.0 * 1.1)



def test_segment_lengths_helper_is_used_for_proportional_split():
    # Guards the coupling: proportional distances are proportional to geodesic lengths.
    raw = _tamper(lambda leg: leg["annotation"]["distance"].pop())
    route = parse_routes(raw)[0]
    geo = segment_lengths_m(route.coords)
    seg = [b - a for a, b in pairwise(route.cum_dist_m)]
    ratios = [s / g for s, g in zip(seg, geo, strict=True) if g > 0]
    assert max(ratios) == pytest.approx(min(ratios), rel=1e-9)


# ------------------------------------------------------------------ edge-case routes


def _payload(coords_lon_lat, leg):
    """A one-route, one-leg Mapbox payload."""
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": leg["distance"],
                "duration": leg["duration"],
                "geometry": {"coordinates": coords_lon_lat},
                "legs": [leg],
            }
        ],
    }


def test_one_segment_route_with_annotations():
    leg = {
        "distance": 1000.0,
        "duration": 100.0,
        "annotation": {"distance": [1000.0], "duration": [100.0]},
    }
    (route,) = parse_routes(_payload([[-100.0, 40.0], [-99.9, 40.1]], leg), truck_time_factor=1.1)
    assert route.eta_method == "annotations"
    assert route.coords == [(40.0, -100.0), (40.1, -99.9)]
    assert route.cum_dist_m == [0.0, 1000.0]
    assert route.cum_dur_s == pytest.approx([0.0, 110.0])
    cps = sample_checkpoints(route, 10)
    assert len(cps) == 2
    assert (cps[0].lat, cps[0].lon) == (40.0, -100.0)
    assert (cps[1].lat, cps[1].lon) == (40.1, -99.9)


def test_one_segment_route_without_annotations_is_proportional():
    leg = {"distance": 1000.0, "duration": 100.0}
    (route,) = parse_routes(_payload([[-100.0, 40.0], [-99.9, 40.1]], leg), truck_time_factor=1.1)
    assert route.eta_method == "proportional"
    assert route.cum_dist_m == pytest.approx([0.0, 1000.0])
    assert route.cum_dur_s == pytest.approx([0.0, 110.0])


def test_origin_equals_destination_zero_length_route(fake_get):
    # Assumes Mapbox answers with a zero-length route; it may instead return NoRoute.
    point = [-111.891, 40.7608]
    leg = {
        "distance": 0.0,
        "duration": 0.0,
        "annotation": {"distance": [0.0], "duration": [0.0]},
    }
    fake_get(FakeResponse(body=_payload([point, point], leg)))
    (route,) = get_routes((40.7608, -111.891), (40.7608, -111.891), TOKEN)
    assert route.eta_method == "annotations"
    assert route.cum_dist_m == [0.0, 0.0]
    assert route.cum_dur_s == [0.0, 0.0]
    cps = sample_checkpoints(route, 25)
    assert len(cps) == 2
    assert all((c.lat, c.lon) == (40.7608, -111.891) for c in cps)
    assert all(c.dist_mi == 0 and c.owned_mi == 0 for c in cps)


def test_duplicate_consecutive_coordinates_with_annotations():
    coords = [[0.0, 0.0], [1.0, 0.0], [1.0, 0.0], [3.0, 0.0]]  # segment 2 has zero length
    leg = {
        "distance": 3000.0,
        "duration": 300.0,
        "annotation": {"distance": [1000.0, 0.0, 2000.0], "duration": [100.0, 0.0, 200.0]},
    }
    (route,) = parse_routes(_payload(coords, leg), truck_time_factor=1.0)
    assert route.eta_method == "annotations"
    assert len(route.coords) == len(route.cum_dist_m) == len(route.cum_dur_s) == 4
    assert route.cum_dist_m == [0.0, 1000.0, 1000.0, 3000.0]
    assert route.cum_dur_s == [0.0, 100.0, 100.0, 300.0]


def test_duplicate_consecutive_coordinates_proportional():
    coords = [[0.0, 0.0], [1.0, 0.0], [1.0, 0.0], [3.0, 0.0]]  # 1 deg, 0, 2 deg along the equator
    leg = {"distance": 3000.0, "duration": 300.0}  # no annotation -> proportional
    (route,) = parse_routes(_payload(coords, leg), truck_time_factor=1.0)
    assert route.eta_method == "proportional"
    assert route.cum_dist_m == pytest.approx([0.0, 1000.0, 1000.0, 3000.0], rel=1e-3)
    assert route.cum_dur_s == pytest.approx([0.0, 100.0, 100.0, 300.0], rel=1e-3)
