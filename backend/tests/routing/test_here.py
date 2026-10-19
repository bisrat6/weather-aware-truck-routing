import copy
import json
import logging
import math

import pytest
import requests

from routing import here
from routing.errors import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderNoRouteError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTimeoutError,
    RoutingError,
)
from routing.geodesy import segment_lengths_m
from routing.here import get_routes, parse_routes
from tests.here_helpers import load_fixture, payload_of, section

KEY = "SECRET-HERE-KEY-123"
ORIGIN, DEST = (40.7608, -111.8910), (40.2338, -111.6585)
# ~1.11 km per step north; real-looking road segments
LINE = [(40.0, -111.0), (40.01, -111.0), (40.02, -111.0), (40.03, -111.0)]


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
    state = {"calls": []}

    def install(response=None, raises=None):
        def _get(url, params=None, timeout=None):
            state["calls"].append({"url": url, "params": params, "timeout": timeout})
            if raises is not None:
                raise raises
            return response

        monkeypatch.setattr(here.requests, "get", _get)
        return state

    return install


def ok_response(payload=None):
    return FakeResponse(200, load_fixture("slc_provo") if payload is None else payload)


# ----------------------------------------------------------------------------- request


def test_request_url_params_and_timeout(fake_get):
    state = fake_get(ok_response())
    get_routes(ORIGIN, DEST, KEY)
    (call,) = state["calls"]
    assert call["url"] == "https://router.hereapi.com/v8/routes"
    assert call["params"] == {
        "transportMode": "truck",
        "origin": "40.760800,-111.891000",
        "destination": "40.233800,-111.658500",
        "alternatives": "2",
        "return": "polyline,summary",
        "spans": "length,duration,speedLimit",
        "apiKey": KEY,
    }
    assert call["timeout"] == here.DEFAULT_TIMEOUT == (3.0, 30.0)


@pytest.mark.parametrize(
    ("origin", "dest", "key"),
    [
        ((91.0, 0.0), DEST, KEY),
        (ORIGIN, (0.0, 181.0), KEY),
        ((math.nan, 0.0), DEST, KEY),
        (ORIGIN, (math.inf, 0.0), KEY),
        ("bad", DEST, KEY),
        (ORIGIN, DEST, ""),
    ],
)
def test_bad_arguments_raise_value_error_before_any_request(fake_get, origin, dest, key):
    state = fake_get(ok_response())
    with pytest.raises(ValueError):
        get_routes(origin, dest, key)
    assert state["calls"] == []


@pytest.mark.parametrize("factor", [0.0, -1.0, math.nan, math.inf])
def test_bad_truck_factor_rejected(fake_get, factor):
    state = fake_get(ok_response())
    with pytest.raises(ValueError):
        get_routes(ORIGIN, DEST, KEY, truck_time_factor=factor)
    assert state["calls"] == []


# ----------------------------------------------------------------------------- parsing


def test_parses_recorded_routes():
    routes = parse_routes(load_fixture("slc_provo"))
    raw = load_fixture("slc_provo")["routes"]
    assert 1 <= len(routes) <= 3
    for route, raw_route in zip(routes, raw, strict=True):
        summary = raw_route["sections"][0]["summary"]
        assert route.origin_label == here.PROVIDER_LABEL == "HERE route"
        assert route.eta_method == "annotations"
        assert len(route.coords) == len(route.cum_dist_m) == len(route.cum_dur_s)
        assert route.distance_m == summary["length"]
        assert route.duration_s == summary["duration"]
        assert route.cum_dist_m[-1] == pytest.approx(summary["length"], rel=1e-9)
        assert route.cum_dur_s[-1] == pytest.approx(summary["duration"], rel=1e-9)


def test_default_truck_factor_is_1_0():
    assert here.DEFAULT_TRUCK_TIME_FACTOR == 1.0
    plain = parse_routes(load_fixture("slc_provo"))[0]
    explicit = parse_routes(load_fixture("slc_provo"), truck_time_factor=1.0)[0]
    assert plain.duration_s == explicit.duration_s


@pytest.mark.parametrize("factor", [1.0, 1.1, 1.5])
def test_truck_factor_scales_durations_not_distances(factor):
    base = parse_routes(load_fixture("slc_provo"))[0]
    scaled = parse_routes(load_fixture("slc_provo"), truck_time_factor=factor)[0]
    assert scaled.duration_s == pytest.approx(base.duration_s * factor)
    assert scaled.cum_dur_s[-1] == pytest.approx(base.cum_dur_s[-1] * factor)
    assert scaled.distance_m == base.distance_m
    assert scaled.cum_dist_m == base.cum_dist_m


def test_spans_set_the_time_profile_along_the_route():
    # Two spans over three segments: the first span (1 segment) takes 100 s, the second
    # (2 segments) 100 s, so a checkpoint at the end of segment 1 is at 100 s, not 33 s.
    spans = [
        {"offset": 0, "length": 1000, "duration": 100},
        {"offset": 1, "length": 2000, "duration": 100},
    ]
    route = parse_routes(payload_of([section(LINE, spans)]))[0]
    assert route.eta_method == "annotations"
    assert route.cum_dur_s[1] == pytest.approx(100)
    assert route.cum_dur_s[3] == pytest.approx(200)
    assert route.cum_dist_m[1] == pytest.approx(1000)
    assert route.cum_dist_m[3] == pytest.approx(3000)


def test_span_totals_are_split_by_segment_length_within_a_span():
    short_then_long = [(40.0, -111.0), (40.001, -111.0), (40.011, -111.0)]
    spans = [{"offset": 0, "length": 1100, "duration": 110}]
    route = parse_routes(payload_of([section(short_then_long, spans)]))[0]
    geodesic = segment_lengths_m(short_then_long)
    assert route.cum_dur_s[1] == pytest.approx(110 * geodesic[0] / sum(geodesic), rel=1e-6)


BAD_SPANS = {
    "missing": lambda s: s.pop("spans"),
    "empty": lambda s: s.update(spans=[]),
    "first-offset-not-zero": lambda s: s["spans"][0].update(offset=1),
    "offsets-not-increasing": lambda s: s["spans"][1].update(offset=0),
    "offset-past-end": lambda s: s["spans"][1].update(offset=99),
    "negative-duration": lambda s: s["spans"][0].update(duration=-1),
    "missing-length": lambda s: s["spans"][0].pop("length"),
    "not-a-list": lambda s: s.update(spans="oops"),
}


@pytest.mark.parametrize("name", BAD_SPANS)
def test_unusable_spans_fall_back_to_proportional(name, caplog):
    spans = [
        {"offset": 0, "length": 1000, "duration": 100},
        {"offset": 1, "length": 2000, "duration": 200},
    ]
    one = section(LINE, spans, length=3000, duration=300)
    BAD_SPANS[name](one)
    with caplog.at_level(logging.WARNING):
        route = parse_routes(payload_of([one]))[0]
    assert route.eta_method == "proportional"
    assert route.cum_dur_s[-1] == pytest.approx(300)
    assert route.cum_dist_m[-1] == pytest.approx(3000)
    assert "proportional" in caplog.text


def test_proportional_durations_follow_segment_distance():
    one = section(LINE, [], length=3000, duration=300)
    route = parse_routes(payload_of([one]))[0]
    geodesic = segment_lengths_m(LINE)
    assert route.cum_dur_s[1] == pytest.approx(300 * geodesic[0] / sum(geodesic))


def test_multi_section_route_joins_without_duplicating_the_joint():
    first = section(LINE[:2], [{"offset": 0, "length": 1000, "duration": 100}])
    second = section(LINE[1:], [{"offset": 0, "length": 2000, "duration": 150}])
    route = parse_routes(payload_of([first, second]))[0]
    assert route.coords == pytest.approx(LINE)
    assert route.distance_m == pytest.approx(3000)
    assert route.duration_s == pytest.approx(250)
    assert route.cum_dur_s[-1] == pytest.approx(250)
    assert route.eta_method == "annotations"


def test_zero_length_route_is_valid():
    point = (40.0, -111.0)
    one = section([point, point], [{"offset": 0, "length": 0, "duration": 0}])
    route = parse_routes(payload_of([one]))[0]
    assert route.distance_m == 0 and route.duration_s == 0
    assert route.cum_dist_m == [0.0, 0.0]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.pop("sections"),
        lambda r: r.update(sections=[]),
        lambda r: r["sections"][0].pop("polyline"),
        lambda r: r["sections"][0].update(polyline="!!!"),
        lambda r: r["sections"][0].update(polyline="BF"),
        lambda r: r["sections"][0].pop("summary"),
        lambda r: r["sections"][0]["summary"].update(duration="soon"),
        lambda r: r["sections"].__setitem__(0, "not a section"),
    ],
)
def test_malformed_route_shapes_map_to_response_error(mutate):
    payload = copy.deepcopy(load_fixture("slc_provo"))
    mutate(payload["routes"][0])
    with pytest.raises(ProviderResponseError):
        parse_routes(payload)


# ----------------------------------------------------------------------------- errors


NO_ROUTE = {
    "notices": [
        {"title": "Route calculation failed: Couldn't find a route.", "code": "noRouteFound"}
    ],
    "routes": [],
}


@pytest.mark.parametrize(
    ("response", "raises", "expected"),
    [
        (None, requests.Timeout("slow"), ProviderTimeoutError),
        (None, requests.ConnectTimeout("slow"), ProviderTimeoutError),
        (None, requests.ConnectionError("down"), ProviderConnectionError),
        (None, requests.RequestException("odd"), ProviderConnectionError),
        (FakeResponse(401, {"error": "Unauthorized"}), None, ProviderAuthError),
        (FakeResponse(403, {"error": "Forbidden"}), None, ProviderAuthError),
        (FakeResponse(429, {"error": "Too many"}), None, ProviderRateLimitError),
        (
            FakeResponse(400, {"title": "Malformed request", "code": "E605001"}),
            None,
            ProviderRequestError,
        ),
        (FakeResponse(404, text="nope"), None, ProviderRequestError),
        (FakeResponse(500, {}), None, ProviderServerError),
        (FakeResponse(503, text="down", bad_json=True), None, ProviderServerError),
        (FakeResponse(200, NO_ROUTE), None, ProviderNoRouteError),
        (FakeResponse(200, {"routes": []}), None, ProviderNoRouteError),
        (FakeResponse(200, {"nothing": 1}), None, ProviderResponseError),
        (FakeResponse(200, ["list"]), None, ProviderResponseError),
        (FakeResponse(200, bad_json=True, text="<html>"), None, ProviderResponseError),
    ],
)
def test_error_mapping(fake_get, response, raises, expected):
    fake_get(response, raises)
    with pytest.raises(expected) as info:
        get_routes(ORIGIN, DEST, KEY)
    assert isinstance(info.value, RoutingError)
    assert isinstance(info.value, ProviderError)


def test_error_attributes(fake_get):
    fake_get(FakeResponse(400, {"title": "Malformed request", "code": "E605001"}))
    with pytest.raises(ProviderRequestError) as info:
        get_routes(ORIGIN, DEST, KEY)
    assert info.value.status_code == 400 and info.value.code == "E605001"

    fake_get(FakeResponse(200, NO_ROUTE))
    with pytest.raises(ProviderNoRouteError) as info:
        get_routes(ORIGIN, DEST, KEY)
    assert info.value.code == "noRouteFound"
    assert "find a route" in str(info.value)


def test_rate_limit_retry_after(fake_get):
    fake_get(FakeResponse(429, {}, headers={"Retry-After": "17"}))
    with pytest.raises(ProviderRateLimitError) as info:
        get_routes(ORIGIN, DEST, KEY)
    assert info.value.retry_after == 17.0

    fake_get(FakeResponse(429, {}))
    with pytest.raises(ProviderRateLimitError) as info:
        get_routes(ORIGIN, DEST, KEY)
    assert info.value.retry_after is None


@pytest.mark.parametrize(
    "raises",
    [
        requests.Timeout(f"https://router.hereapi.com/v8/routes?apiKey={KEY} timed out"),
        requests.ConnectionError(f"HTTPSConnectionPool(host='router.hereapi.com') {KEY}"),
    ],
)
def test_key_never_leaks_from_network_errors(fake_get, raises):
    fake_get(None, raises)
    with pytest.raises(ProviderError) as info:
        get_routes(ORIGIN, DEST, KEY)
    assert KEY not in str(info.value)
    assert "hereapi.com" not in str(info.value)
    assert info.value.__cause__ is None


def test_key_is_redacted_from_api_messages(fake_get):
    fake_get(FakeResponse(401, {"error_description": f"apiKey {KEY} is invalid"}))
    with pytest.raises(ProviderAuthError) as info:
        get_routes(ORIGIN, DEST, KEY)
    assert KEY not in str(info.value)
    assert "***" in str(info.value)

    notice = {"routes": [], "notices": [{"title": f"failed for {KEY}", "code": "x"}]}
    fake_get(FakeResponse(200, notice))
    with pytest.raises(ProviderNoRouteError) as info:
        get_routes(ORIGIN, DEST, KEY)
    assert KEY not in str(info.value)
