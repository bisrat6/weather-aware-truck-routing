import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from analysis import analyze_route
from risk import RiskLevel
from routing.checkpoints import sample_checkpoints
from routing.mapbox import parse_routes
from weather.models import MissingWeather, WeatherSample
from weather.service import WeatherService

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 10, 17, tzinfo=UTC)


def recorded_route():
    payload = json.loads((FIXTURES / "mapbox_slc_provo.json").read_text(encoding="utf-8"))
    return parse_routes(payload)[0]


class TripletWeather:
    def __init__(self, values):
        self.values = values
        self.queries = None

    def weather_at_many(self, queries):
        self.queries = list(queries)
        return self.values


def sample(wind):
    return WeatherSample(NOW, wind, 0.0, 0.0)


def test_stability_is_false_when_both_shifted_levels_match():
    route = recorded_route()
    n = len(sample_checkpoints(route, 1000))
    scored = analyze_route(route, NOW, 1000, 0, TripletWeather([sample(20)] * (n * 3)))
    assert all(cp.risk_level == RiskLevel.LOW for cp in scored.checkpoints)
    assert all(cp.risk_level_minus_2h == RiskLevel.LOW for cp in scored.checkpoints)
    assert all(cp.risk_level_plus_2h == RiskLevel.LOW for cp in scored.checkpoints)
    assert all(cp.unstable is False for cp in scored.checkpoints)
    assert scored.summary.unstable_checkpoints == 0
    assert scored.summary.unknown_stability_checkpoints == 0


@pytest.mark.parametrize(
    ("changed_index", "expected"),
    [(0, "minus"), (2, "plus")],
)
def test_stability_detects_flips_at_either_shift(changed_index, expected):
    route = recorded_route()
    n = len(sample_checkpoints(route, 1000))
    values = [sample(20)] * (n * 3)
    values[changed_index] = sample(40)
    scored = analyze_route(route, NOW, 1000, 0, TripletWeather(values))
    checkpoint = scored.checkpoints[0]
    assert checkpoint.unstable is True
    assert scored.summary.unstable_checkpoints == 1
    if expected == "minus":
        assert checkpoint.risk_level_minus_2h == RiskLevel.HIGH
        assert checkpoint.risk_level_plus_2h == RiskLevel.LOW
    else:
        assert checkpoint.risk_level_minus_2h == RiskLevel.LOW
        assert checkpoint.risk_level_plus_2h == RiskLevel.HIGH


def test_stability_flip_uses_the_load_rule():
    route = recorded_route()
    n = len(sample_checkpoints(route, 1000))
    values = [sample(20)] * (n * 3)
    values[0] = sample(35.0)
    scored = analyze_route(route, NOW, 1000, 45_000, TripletWeather(values))
    assert scored.checkpoints[0].risk_level == RiskLevel.LOW
    assert scored.checkpoints[0].risk_level_minus_2h == RiskLevel.SEVERE
    assert scored.checkpoints[0].unstable is True


def test_plus_two_hour_stability_uses_load_rule():
    route = recorded_route()
    n = len(sample_checkpoints(route, 1000))
    values = []
    for index in range(n):
        values.extend(
            [sample(20), sample(34.9), sample(35.0)]
            if index == 0
            else [sample(20)] * 3
        )
    scored = analyze_route(route, NOW, 1000, 45_000, TripletWeather(values))
    checkpoint = scored.checkpoints[0]
    assert checkpoint.risk_level == RiskLevel.MODERATE
    assert checkpoint.risk_level_plus_2h == RiskLevel.SEVERE
    assert checkpoint.unstable is True


def test_missing_shifted_weather_is_unknown_and_counted_separately():
    route = recorded_route()
    n = len(sample_checkpoints(route, 1000))
    values = [sample(20)] * (n * 3)
    values[0] = MissingWeather(None, "fixture missing")
    scored = analyze_route(route, NOW, 1000, 0, TripletWeather(values))
    assert scored.checkpoints[0].risk_level_minus_2h is None
    assert scored.checkpoints[0].unstable is None
    assert scored.summary.unstable_checkpoints == 0
    assert scored.summary.unknown_stability_checkpoints == 1


def _mock_weather(monkeypatch, *, start: datetime, hours: int):
    payload = json.loads((FIXTURES / "open_meteo_recorded.json").read_text(encoding="utf-8"))[0]
    calls = []

    class Response:
        status_code = 200

        def __init__(self, body):
            self.body = body
            self.text = json.dumps(body)
            self.headers = {}

        def json(self):
            return self.body

    def get(url, params, timeout):
        calls.append(params)
        times = [start + timedelta(hours=i) for i in range(hours)]
        locations = []
        for index, (lat, lon) in enumerate(
            zip(params["latitude"].split(","), params["longitude"].split(","), strict=True)
        ):
            item = json.loads(json.dumps(payload))
            item["latitude"], item["longitude"] = float(lat), float(lon)
            if index:
                item["location_id"] = index
            item["hourly"]["time"] = [moment.strftime("%Y-%m-%dT%H:%M") for moment in times]
            for variable in ("wind_speed_10m", "rain", "showers", "snowfall"):
                item["hourly"][variable] = [item["hourly"][variable][0]] * hours
            locations.append(item)
        return Response(locations if len(locations) > 1 else locations[0])

    monkeypatch.setattr("weather.client.requests.get", get)
    return calls


def test_first_forecast_hour_has_unknown_earlier_stability_from_same_fetch(monkeypatch):
    calls = _mock_weather(monkeypatch, start=NOW, hours=16 * 24)
    service = WeatherService(now=lambda: NOW)
    route = recorded_route()
    scored = analyze_route(route, NOW, 1000, 0, service)
    assert scored.checkpoints[0].risk_level_minus_2h is None
    assert scored.checkpoints[0].unstable is None
    assert len(calls) == 1


def test_last_available_forecast_hour_has_unknown_later_stability(monkeypatch):
    window_end = NOW + timedelta(days=16)
    calls = _mock_weather(monkeypatch, start=NOW, hours=16 * 24)
    service = WeatherService(now=lambda: NOW)
    route = recorded_route()
    checkpoints = sample_checkpoints(route, 1000)
    departure = window_end - timedelta(hours=1, seconds=checkpoints[-1].eta_offset_s)
    scored = analyze_route(route, departure, 1000, 0, service)
    last = scored.checkpoints[-1]
    assert last.risk_level_plus_2h is None
    assert last.unstable is None
    assert scored.summary.unknown_stability_checkpoints >= 1
    assert len(calls) == 1


# --- stability rule: True if any assessed shift differs; None only if no flip is known and a
# --- shifted level is missing; False only if both shifted levels match.


def _first_checkpoint(values):
    route = recorded_route()
    scored = analyze_route(route, NOW, 1000, 0, TripletWeather(values))
    return scored, scored.checkpoints[0]


def _triplets(n):
    return [sample(20)] * (n * 3)  # every checkpoint LOW at -2h, base and +2h


def test_known_flip_at_minus_2h_is_unstable_even_if_plus_2h_is_missing():
    n = len(sample_checkpoints(recorded_route(), 1000))
    values = _triplets(n)
    values[0] = sample(40)  # -2h: HIGH, base is LOW -> a flip is already proven
    values[2] = MissingWeather(None, "fixture missing")  # +2h unknown
    scored, first = _first_checkpoint(values)
    assert first.risk_level == RiskLevel.LOW
    assert first.risk_level_minus_2h == RiskLevel.HIGH
    assert first.risk_level_plus_2h is None
    assert first.unstable is True
    assert scored.summary.unstable_checkpoints == 1
    assert scored.summary.unknown_stability_checkpoints == 0


def test_known_flip_at_plus_2h_is_unstable_even_if_minus_2h_is_missing():
    n = len(sample_checkpoints(recorded_route(), 1000))
    values = _triplets(n)
    values[0] = MissingWeather(None, "fixture missing")  # -2h unknown
    values[2] = sample(40)  # +2h: HIGH, base is LOW
    scored, first = _first_checkpoint(values)
    assert first.risk_level_minus_2h is None
    assert first.risk_level_plus_2h == RiskLevel.HIGH
    assert first.unstable is True
    assert scored.summary.unstable_checkpoints == 1
    assert scored.summary.unknown_stability_checkpoints == 0


@pytest.mark.parametrize("missing_slot", [0, 2])
def test_no_known_flip_and_one_shift_missing_is_unknown(missing_slot):
    n = len(sample_checkpoints(recorded_route(), 1000))
    values = _triplets(n)
    values[missing_slot] = MissingWeather(None, "fixture missing")
    scored, first = _first_checkpoint(values)
    assert first.unstable is None
    assert scored.summary.unstable_checkpoints == 0
    assert scored.summary.unknown_stability_checkpoints == 1


def test_both_shifts_missing_is_unknown():
    n = len(sample_checkpoints(recorded_route(), 1000))
    values = _triplets(n)
    values[0] = values[2] = MissingWeather(None, "fixture missing")
    scored, first = _first_checkpoint(values)
    assert first.unstable is None
    assert scored.summary.unknown_stability_checkpoints == 1


def test_stable_requires_both_shifted_levels_to_be_assessed_and_equal():
    n = len(sample_checkpoints(recorded_route(), 1000))
    scored, first = _first_checkpoint(_triplets(n))
    assert first.unstable is False
    assert scored.summary.unstable_checkpoints == 0
    assert scored.summary.unknown_stability_checkpoints == 0


def test_missing_base_weather_is_unknown_even_when_shifted_levels_differ():
    n = len(sample_checkpoints(recorded_route(), 1000))
    values = _triplets(n)
    values[0] = sample(40)  # -2h HIGH
    values[1] = MissingWeather(None, "fixture missing")  # the base itself is missing
    scored, first = _first_checkpoint(values)
    assert first.weather_missing
    assert first.risk_level is None
    assert first.unstable is None  # there is no base level to compare against
    assert scored.summary.unknown_stability_checkpoints == 1
