import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest

from analysis import RouteResult, RouteSummary, analyze_route, rank_routes
from risk import RiskLevel
from routing.checkpoints import sample_checkpoints
from routing.mapbox import parse_routes
from weather.models import MissingWeather, WeatherSample

FIXTURES = Path(__file__).parents[1] / "fixtures"


class StubWeather:
    def __init__(self, samples):
        self.samples = samples
        self.queries = None

    def weather_at_many(self, queries):
        self.queries = list(queries)
        if len(self.samples) * 3 == len(queries):
            return [sample for sample in self.samples for _ in range(3)]
        return self.samples


def result(label, miles, avg=1.0, duration=1000, missing_count=0, missing_miles=0.0):
    by_level = {level: 0.0 for level in RiskLevel}
    by_level.update(miles)
    max_level = max((level for level, value in by_level.items() if value > 0), default=None)
    summary = RouteSummary(
        by_level,
        max_level,
        avg,
        duration,
        "annotations",
        missing_count,
        missing_miles,
    )
    return RouteResult((), summary, label)


def test_checkpoint_miles_sum_and_summary_matches_classified_checkpoints():
    route = parse_routes(json.loads((FIXTURES / "mapbox_slc_provo.json").read_text()))[0]
    cps_count = math.ceil(route.cum_dist_m[-1] / 1609.344 / 25) + 1
    weather = StubWeather(
        [WeatherSample(datetime(2026, 10, 17, tzinfo=UTC), 46.0, 0.0, 0.0)] * cps_count
    )
    scored = analyze_route(route, datetime(2026, 10, 17, tzinfo=UTC), 25, 0, weather)
    assert math.fsum(cp.owned_mi for cp in scored.checkpoints) == pytest.approx(
        route.cum_dist_m[-1] / 1609.344
    )
    assert scored.summary.miles_by_level[RiskLevel.SEVERE] == pytest.approx(
        math.fsum(cp.owned_mi for cp in scored.checkpoints if cp.risk_level == RiskLevel.SEVERE)
    )
    assert all(cp.risk_level == RiskLevel.SEVERE for cp in scored.checkpoints)
    assert scored.summary.eta_method == route.eta_method


def test_summary_matches_each_mixed_risk_bucket():
    route = parse_routes(json.loads((FIXTURES / "mapbox_slc_provo.json").read_text()))[0]
    n = len(sample_checkpoints(route, 5))
    winds = (10.0, 30.0, 40.0, 50.0, 70.0)
    samples = [
        WeatherSample(datetime(2026, 10, 17, tzinfo=UTC), winds[index % len(winds)], 0.0, 0.0)
        for index in range(n)
    ]
    scored = analyze_route(
        route, datetime(2026, 10, 17, tzinfo=UTC), 5, 0, StubWeather(samples)
    )
    assert {checkpoint.risk_level for checkpoint in scored.checkpoints} == set(RiskLevel)
    for level in RiskLevel:
        assert scored.summary.miles_by_level[level] == pytest.approx(
            math.fsum(cp.owned_mi for cp in scored.checkpoints if cp.risk_level == level)
        )


def test_load_changes_risk_only_via_shared_engine():
    route = parse_routes(json.loads((FIXTURES / "mapbox_slc_provo.json").read_text()))[0]
    n = math.ceil(route.cum_dist_m[-1] / 1609.344 / 50) + 1
    samples = [WeatherSample(datetime(2026, 10, 17, tzinfo=UTC), 40.0, 0.0, 0.0)] * n
    depart = datetime(2026, 10, 17, tzinfo=UTC)
    light = analyze_route(route, depart, 50, 10_000, StubWeather(samples))
    heavy = analyze_route(route, depart, 50, 45_000, StubWeather(samples))
    assert {cp.risk_level for cp in light.checkpoints} == {RiskLevel.HIGH}
    assert {cp.risk_level for cp in heavy.checkpoints} == {RiskLevel.SEVERE}
    assert all(any("load" in reason for reason in cp.reasons) for cp in heavy.checkpoints)


def test_missing_weather_is_flagged_counted_and_not_scored_as_low():
    route = parse_routes(json.loads((FIXTURES / "mapbox_slc_provo.json").read_text()))[0]
    n = math.ceil(route.cum_dist_m[-1] / 1609.344 / 50) + 1
    values = [WeatherSample(datetime(2026, 10, 17, tzinfo=UTC), 2, 0, 0)] * n
    values[0] = MissingWeather(None, "fixture missing", ("wind_speed_10m",))
    scored = analyze_route(route, datetime(2026, 10, 17, tzinfo=UTC), 50, 0, StubWeather(values))
    assert scored.checkpoints[0].weather_missing
    assert scored.checkpoints[0].risk_level is None
    assert scored.summary.missing_weather_count == 1
    assert scored.summary.missing_weather_miles == pytest.approx(
        math.fsum(cp.owned_mi for cp in scored.checkpoints if cp.weather_missing)
    )
    assert scored.summary.miles_by_level[RiskLevel.LOW] < math.fsum(
        cp.owned_mi for cp in scored.checkpoints
    )
    risk_and_missing_miles = (
        math.fsum(scored.summary.miles_by_level.values())
        + scored.summary.missing_weather_miles
    )
    assert risk_and_missing_miles == pytest.approx(
        math.fsum(cp.owned_mi for cp in scored.checkpoints)
    )


def test_incomplete_route_with_fewer_severe_miles_ranks_below_complete_route():
    complete = result("complete", {RiskLevel.SEVERE: 5, RiskLevel.LOW: 95}, avg=0.5)
    incomplete = result(
        "incomplete", {RiskLevel.LOW: 100}, avg=0.0, missing_count=1, missing_miles=10
    )
    ranked = rank_routes([incomplete, complete])
    assert ranked.recommended == complete
    assert "incomplete weather data" in ranked.reason
    assert "not recommended because" in ranked.reason
    assert "10.0 miles" in ranked.reason
    assert "1 checkpoints" in ranked.reason


def test_two_incomplete_routes_use_the_existing_risk_order():
    fewer_severe = result(
        "fewer severe", {RiskLevel.SEVERE: 1, RiskLevel.LOW: 90}, missing_count=2, missing_miles=9
    )
    more_severe = result(
        "more severe", {RiskLevel.SEVERE: 2, RiskLevel.LOW: 90}, missing_count=1, missing_miles=8
    )
    assert rank_routes([more_severe, fewer_severe]).recommended == fewer_severe


def test_entirely_missing_route_has_all_miles_unclassified_and_reason_discloses_it():
    route = parse_routes(json.loads((FIXTURES / "mapbox_slc_provo.json").read_text()))[0]
    n = math.ceil(route.cum_dist_m[-1] / 1609.344 / 50) + 1
    missing = [MissingWeather(None, "fixture missing")] * n
    scored = analyze_route(
        route,
        datetime(2026, 10, 17, tzinfo=UTC),
        50,
        0,
        StubWeather(missing),
    )
    total = math.fsum(cp.owned_mi for cp in scored.checkpoints)
    assert scored.summary.missing_weather_count == n
    assert scored.summary.missing_weather_miles == pytest.approx(total)
    assert math.fsum(scored.summary.miles_by_level.values()) == pytest.approx(0)
    assert scored.summary.average_risk is None
    ranked = rank_routes([scored])
    assert "incomplete weather data" in ranked.reason
    assert f"{total:.1f} miles" in ranked.reason
    assert f"{n} checkpoints" in ranked.reason


def test_rank_no_travel_last_then_no_travel_miles_and_criteria_ties():
    safest = result("safe", {RiskLevel.LOW: 100}, avg=0.0)
    blocked_fewer = result("blocked-fewer", {RiskLevel.NO_TRAVEL: 2, RiskLevel.LOW: 98})
    blocked_more = result("blocked-more", {RiskLevel.NO_TRAVEL: 8, RiskLevel.LOW: 92})
    ranked = rank_routes([blocked_more, blocked_fewer, safest])
    assert [r.origin_label for r in ranked.ranked_results] == [
        "safe", "blocked-fewer", "blocked-more"
    ]
    assert "No Travel" in rank_routes([blocked_more, blocked_fewer]).reason
    tied_a = result("first", {RiskLevel.SEVERE: 1, RiskLevel.LOW: 99}, avg=1.0, duration=100)
    tied_b = result("second", {RiskLevel.SEVERE: 1, RiskLevel.LOW: 99}, avg=1.0, duration=100)
    assert rank_routes([tied_a, tied_b]).ranked_results == (tied_a, tied_b)


def test_ranking_breaks_severe_high_average_then_duration_ties():
    baseline = result("base", {RiskLevel.SEVERE: 4, RiskLevel.HIGH: 8, RiskLevel.LOW: 88}, 2.0, 900)
    severe = result("severe", {RiskLevel.SEVERE: 5, RiskLevel.HIGH: 0, RiskLevel.LOW: 95}, 0.0, 800)
    high = result("high", {RiskLevel.SEVERE: 4, RiskLevel.HIGH: 9, RiskLevel.LOW: 87}, 1.0, 800)
    average = result(
        "average", {RiskLevel.SEVERE: 4, RiskLevel.HIGH: 8, RiskLevel.LOW: 88}, 2.1, 800
    )
    duration = result(
        "duration", {RiskLevel.SEVERE: 4, RiskLevel.HIGH: 8, RiskLevel.LOW: 88}, 2.0, 901
    )
    assert rank_routes([severe, baseline, high, average, duration]).recommended == baseline
    assert rank_routes([duration, average, baseline]).recommended == baseline


def test_full_run_uses_recorded_route_and_weather_fixture_with_mocked_http(monkeypatch):
    from weather.service import WeatherService

    payload = json.loads((FIXTURES / "open_meteo_recorded.json").read_text())

    class Response:
        status_code = 200

        def __init__(self, body):
            self.body = body
            self.text = json.dumps(body)
            self.headers = {}

        def json(self):
            return self.body

    calls = []

    def get(url, params, timeout):
        calls.append(params)
        lats = params["latitude"].split(",")
        lons = params["longitude"].split(",")
        locations = []
        for i, (lat, lon) in enumerate(zip(lats, lons, strict=True)):
            item = dict(payload[0])
            item["latitude"], item["longitude"] = float(lat), float(lon)
            if i:
                item["location_id"] = i
            locations.append(item)
        return Response(locations if len(locations) > 1 else locations[0])

    monkeypatch.setattr("weather.client.requests.get", get)
    route = parse_routes(json.loads((FIXTURES / "mapbox_slc_provo.json").read_text()))[0]
    service = WeatherService(now=lambda: datetime(2026, 10, 17, 1, tzinfo=UTC))
    scored = analyze_route(route, datetime(2026, 10, 17, 2, tzinfo=UTC), 25, 0, service)
    assert len(scored.checkpoints) >= 2
    assert scored.summary.missing_weather_count == 0
    assert math.fsum(scored.summary.miles_by_level.values()) == pytest.approx(
        route.cum_dist_m[-1] / 1609.344
    )
    assert calls  # proves the complete flow exercised the mocked HTTP boundary
    assert len(calls) == 1  # stability used the same batched request; it made no extra fetch
