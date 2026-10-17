import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from api.heatmap import MAX_HEATMAP_POINTS, sample_corridor

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 10, 17, 10, tzinfo=UTC)


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self.body = body
        self.headers = headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self.body


def request_body(**updates):
    data = {
        "geometry": [[40.7608, -111.8910], [40.2338, -111.6585]],
        "departure_utc": "2026-10-17T12:00:00Z",
        "load_lb": 10_000,
    }
    data.update(updates)
    return data


def weather_response(params, *, status=200, missing=False, wind=40, headers=None):
    template = json.loads((FIXTURES / "open_meteo_recorded.json").read_text(encoding="utf-8"))[0]
    locations = []
    for index, (lat, lon) in enumerate(
        zip(params["latitude"].split(","), params["longitude"].split(","), strict=True)
    ):
        item = copy.deepcopy(template)
        item["latitude"], item["longitude"] = float(lat), float(lon)
        item["hourly"]["wind_speed_10m"] = (
            [None] * len(item["hourly"]["time"])
            if missing and index == 0
            else [wind] * len(item["hourly"]["time"])
        )
        locations.append(item)
    payload = locations if len(locations) > 1 else locations[0]
    return FakeResponse(status, payload, headers)


@pytest.fixture
def client(monkeypatch):
    from weather.service import WeatherService

    monkeypatch.setattr("api.views.timezone.now", lambda: NOW)
    monkeypatch.setattr("api.views._weather_service", WeatherService(now=lambda: NOW))
    with override_settings():
        yield APIClient()


def mock_weather(monkeypatch, *, status=200, missing=False, wind=40, headers=None):
    monkeypatch.setattr(
        "requests.get",
        lambda url, params=None, timeout=None: weather_response(
            params, status=status, missing=missing, wind=wind, headers=headers
        ),
    )


def test_heatmap_shape_and_hour_zero_is_departure_hour(client, monkeypatch):
    mock_weather(monkeypatch)
    response = client.post(
        "/api/heatmap", request_body(departure_utc="2026-10-17T12:29:00Z"), format="json"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["hours"]) == 49
    assert data["hours"][0] == "2026-10-17T12:00:00Z"
    assert data["hours"][-1] == "2026-10-19T12:00:00Z"
    assert data["points"]
    assert all(len(point["risk"]) == 49 for point in data["points"])
    assert all(value in range(5) or value is None for p in data["points"] for value in p["risk"])


def test_heatmap_load_changes_risk_through_shared_engine(client, monkeypatch):
    mock_weather(monkeypatch, wind=40)
    light = client.post("/api/heatmap", request_body(load_lb=10_000), format="json").json()
    heavy = client.post("/api/heatmap", request_body(load_lb=45_000), format="json").json()
    assert all(value == 2 for point in light["points"] for value in point["risk"])
    assert all(value == 3 for point in heavy["points"] for value in point["risk"])


def test_missing_weather_is_marked_null(client, monkeypatch):
    mock_weather(monkeypatch, missing=True)
    response = client.post("/api/heatmap", request_body(), format="json")
    assert response.status_code == 200
    assert response.json()["points"]
    assert all(value is None for value in response.json()["points"][0]["risk"])


def test_corridor_sampling_obeys_point_cap():
    points = sample_corridor([[-80, -170], [80, 170]])
    assert len(points) <= MAX_HEATMAP_POINTS
    assert len(points) == MAX_HEATMAP_POINTS


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"geometry": None}, "geometry"),
        ({"geometry": [[91, 0], [0, 0]]}, "latitude"),
        ({"departure_utc": "yesterday"}, "ISO 8601"),
        ({"departure_utc": "2026-10-16T23:00:00Z"}, "earliest hour"),
        ({"departure_utc": "2026-10-25T10:01:00Z"}, "7 days ahead"),
        ({"load_lb": -1}, "load_lb"),
        ({"load_lb": 100_001}, "load_lb"),
    ],
)
def test_heatmap_validation_errors(client, monkeypatch, updates, message):
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: pytest.fail("HTTP should not run"))
    response = client.post("/api/heatmap", request_body(**updates), format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert message.lower() in response.json()["error"]["message"].lower()


@pytest.mark.parametrize(
    ("upstream_status", "expected_status", "code", "retry"),
    [(503, 502, "weather_unavailable", None), (429, 429, "rate_limited", 23)],
)
def test_heatmap_weather_errors_are_sanitized(
    client, monkeypatch, upstream_status, expected_status, code, retry
):
    secret = "do-not-leak https://api.open-meteo.com/token"
    def get(url, params=None, timeout=None):
        return FakeResponse(
            upstream_status,
            {"reason": secret},
            {"Retry-After": str(retry)} if retry else {},
        )

    monkeypatch.setattr("requests.get", get)
    response = client.post("/api/heatmap", request_body(), format="json")
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == code
    assert secret not in response.content.decode()
    assert "open-meteo.com" not in response.content.decode()
    if retry:
        assert "23 seconds" in response.json()["error"]["message"]
