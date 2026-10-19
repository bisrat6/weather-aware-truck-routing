import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from tests.here_helpers import load_fixture

FIXTURES = Path(__file__).parents[1] / "fixtures"
TOKEN = "test-token-do-not-leak"
NOW = datetime(2026, 10, 17, 10, tzinfo=UTC)


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code = status
        self.body = body
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        return self.body


def body(**updates):
    data = {
        "origin": {"lat": 40.7608, "lon": -111.8910},
        "destination": {"lat": 40.2338, "lon": -111.6585},
        "departure_utc": "2026-10-17T12:00:00Z",
        "load_lb": 34_000,
        "interval_miles": 25,
    }
    data.update(updates)
    return data


def recorded_route():
    return load_fixture("slc_provo")


def recorded_weather():
    return json.loads((FIXTURES / "open_meteo_recorded.json").read_text(encoding="utf-8"))


def route_response(*, status=200, payload=None, headers=None):
    return FakeResponse(status, recorded_route() if payload is None else payload, headers)


def weather_response(params, *, status=200, missing_cell=False, headers=None):
    payload = recorded_weather()[0]
    locations = []
    for index, (lat, lon) in enumerate(
        zip(params["latitude"].split(","), params["longitude"].split(","), strict=True)
    ):
        item = copy.deepcopy(payload)
        item["latitude"], item["longitude"] = float(lat), float(lon)
        if index:
            item["location_id"] = index
        if missing_cell and index == 1:
            item["hourly"]["wind_speed_10m"] = [None] * len(item["hourly"]["time"])
        locations.append(item)
    return FakeResponse(status, locations if len(locations) > 1 else locations[0], headers)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("api.views.timezone.now", lambda: NOW)
    from weather.service import WeatherService

    monkeypatch.setattr("api.views._weather_service", WeatherService(now=lambda: NOW))
    with override_settings(HERE_API_KEY=TOKEN):
        yield APIClient()


def mock_both_providers(monkeypatch, *, weather_status=200, missing_cell=False):
    def get(url, params=None, timeout=None):
        if "router.hereapi.com" in url:
            return route_response()
        if "open-meteo.com" in url:
            return weather_response(params, status=weather_status, missing_cell=missing_cell)
        if "autosuggest.search.hereapi.com" in url:
            return FakeResponse(
                body={
                    "items": [
                        {
                            "resultType": "locality",
                            "title": "Salt Lake City, UT, United States",
                            "position": {"lat": 40.76, "lng": -111.89},
                        },
                        {"resultType": "chainQuery", "title": "Salt Lake Coffee"},
                    ]
                }
            )
        raise AssertionError(f"unexpected upstream {url}")

    monkeypatch.setattr("requests.get", get)


def mock_weather_for_all_route_cells(monkeypatch, *, wind_mph=None):
    def get(url, params=None, timeout=None):
        if "router.hereapi.com" in url:
            return route_response()
        if "open-meteo.com" in url:
            template = recorded_weather()[0]
            locations = []
            for index, (lat, lon) in enumerate(
                zip(params["latitude"].split(","), params["longitude"].split(","), strict=True)
            ):
                item = copy.deepcopy(template)
                item["latitude"], item["longitude"] = float(lat), float(lon)
                if index:
                    item["location_id"] = index
                item["hourly"]["wind_speed_10m"] = (
                    [None] * len(item["hourly"]["time"])
                    if wind_mph is None
                    else [wind_mph] * len(item["hourly"]["time"])
                )
                locations.append(item)
            return FakeResponse(body=locations if len(locations) > 1 else locations[0])
        raise AssertionError(f"unexpected upstream {url}")

    monkeypatch.setattr("requests.get", get)


def test_analyze_happy_path_uses_recorded_fixtures_and_provider_notice(client, monkeypatch):
    mock_both_providers(monkeypatch)
    response = client.post("/api/analyze", body(), format="json")
    assert response.status_code == 200
    data = response.json()
    assert data["routes"]
    assert sorted(data["ranking"]) == list(range(len(data["routes"])))
    assert data["recommended"] == data["ranking"][0]
    assert data["routes"][0]["checkpoints"]
    checkpoint = data["routes"][0]["checkpoints"][0]
    levels = {"Low", "Moderate", "High", "Severe", "No Travel", None}
    assert checkpoint["risk_level_minus_2h"] in levels
    assert checkpoint["risk_level_plus_2h"] in levels
    assert checkpoint["unstable"] in {True, False, None}
    assert "unstable_checkpoints" in data["routes"][0]["summary"]
    assert "unknown_stability_checkpoints" in data["routes"][0]["summary"]
    assert data["notices"] == [
        f"Only {len(data['routes'])} routes available from the routing provider"
    ]
    assert {r["origin_label"] for r in data["routes"]} == {"HERE route"}


def test_analyze_notice_discloses_recommended_blocked_route(client, monkeypatch):
    mock_weather_for_all_route_cells(monkeypatch, wind_mph=50)
    response = client.post("/api/analyze", body(load_lb=34_000), format="json")
    assert response.status_code == 200
    data = response.json()
    assert data["routes"][data["recommended"]]["summary"]["max_level"] == "No Travel"
    assert any("blocked" in notice.lower() for notice in data["notices"])


def test_analyze_notice_discloses_recommended_incomplete_route(client, monkeypatch):
    mock_weather_for_all_route_cells(monkeypatch)
    response = client.post("/api/analyze", body(), format="json")
    assert response.status_code == 200
    data = response.json()
    summary = data["routes"][data["recommended"]]["summary"]
    assert summary["missing_weather_count"] > 0
    assert any("incomplete weather data" in notice.lower() for notice in data["notices"])


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "destination": {"lat": 1, "lon": 2},
                "departure_utc": "2026-10-17T12:00:00Z",
                "load_lb": 0,
            },
            "origin",
        ),
        (
            {"origin": body()["origin"], "departure_utc": "2026-10-17T12:00:00Z", "load_lb": 0},
            "destination",
        ),
        (
            {"origin": body()["origin"], "destination": body()["destination"], "load_lb": 0},
            "departure_utc",
        ),
        (
            {
                "origin": body()["origin"],
                "destination": body()["destination"],
                "departure_utc": "2026-10-17T12:00:00Z",
            },
            "load_lb",
        ),
        (body(departure_utc="tomorrow"), "ISO 8601"),
        (body(load_lb=-1), "load_lb"),
        (body(load_lb=100_001), "load_lb"),
        (body(origin=body()["destination"]), "differ from origin"),
        (body(origin="40.7,-111.9"), "origin"),
        (body(departure_utc="2026-10-16T23:59:00Z"), "earliest hour"),
        (body(departure_utc="2026-10-25T10:00:00Z"), "7 days ahead"),
        (body(interval_miles=15), "interval_miles"),
    ],
)
def test_analyze_validation_errors_are_clear_400s(client, monkeypatch, payload, message):
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: pytest.fail("HTTP should not run"))
    response = client.post("/api/analyze", payload, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert message.lower() in response.json()["error"]["message"].lower()


def test_routing_down_returns_safe_502(client, monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: FakeResponse(
            503, {"message": f"bad {TOKEN} https://router.hereapi.com/v8/routes?apiKey={TOKEN}"}
        ),
    )
    response = client.post("/api/analyze", body(), format="json")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "routing_unavailable"
    assert TOKEN not in response.content.decode()
    assert "hereapi.com" not in response.content.decode()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "routes": [],
            "notices": [
                {
                    "title": f"No route {TOKEN} https://router.hereapi.com/v8/routes?apiKey={TOKEN}",
                    "code": "noRouteFound",
                    "severity": "critical",
                }
            ],
        },
        {"routes": []},
    ],
    ids=["no-route-notice", "empty-routes"],
)
def test_no_driving_route_returns_sanitized_422(client, monkeypatch, payload):
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse(200, payload))

    response = client.post("/api/analyze", body(), format="json")

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "no_route_found",
            "message": "No driving route found between these places.",
        }
    }
    assert TOKEN not in response.content.decode()
    assert "hereapi.com" not in response.content.decode()


@pytest.mark.parametrize("status", [400, 422])
def test_provider_rejecting_the_request_is_mapped_and_sanitized(client, monkeypatch, status):
    upstream_cause = (
        f"provider-private-detail {TOKEN} https://router.hereapi.com/v8/routes?apiKey={TOKEN}"
    )
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: FakeResponse(
            status, {"title": "Malformed request", "code": "E605001", "cause": upstream_cause}
        ),
    )

    response = client.post("/api/analyze", body(), format="json")

    response_body = response.content.decode()
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "route_not_supported",
            "message": "A driving route could not be built between these places.",
        }
    }
    assert "provider-private-detail" not in response_body
    assert TOKEN not in response_body
    assert "hereapi.com" not in response_body


def test_provider_auth_failure_is_a_safe_502(client, monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: FakeResponse(
            401, {"error": "Unauthorized", "error_description": f"apiKey invalid {TOKEN}"}
        ),
    )
    response = client.post("/api/analyze", body(), format="json")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "routing_unavailable"
    assert TOKEN not in response.content.decode()


def test_routing_rate_limit_has_retry_hint_and_no_credentials(client, monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: FakeResponse(
            429,
            {"message": f"{TOKEN} https://router.hereapi.com"},
            {"Retry-After": "17"},
        ),
    )
    response = client.post("/api/analyze", body(), format="json")
    assert response.status_code == 429
    assert "17 seconds" in response.json()["error"]["message"]
    assert TOKEN not in response.content.decode()
    assert "hereapi.com" not in response.content.decode()


def test_weather_down_returns_safe_502(client, monkeypatch):
    def get(url, params=None, timeout=None):
        if "router.hereapi.com" in url:
            return route_response()
        return FakeResponse(
            503,
            {"reason": f"{TOKEN} https://api.open-meteo.com"},
            text=f"{TOKEN} https://api.open-meteo.com",
        )

    monkeypatch.setattr("requests.get", get)
    response = client.post("/api/analyze", body(), format="json")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "weather_unavailable"
    assert TOKEN not in response.content.decode()
    assert "open-meteo.com" not in response.content.decode()


def test_weather_rate_limit_has_retry_hint_and_no_credentials(client, monkeypatch):
    def get(url, params=None, timeout=None):
        if "router.hereapi.com" in url:
            return route_response()
        return weather_response(params, status=429, headers={"Retry-After": "23"})

    monkeypatch.setattr("requests.get", get)
    response = client.post("/api/analyze", body(), format="json")
    assert response.status_code == 429
    assert "23 seconds" in response.json()["error"]["message"]
    assert TOKEN not in response.content.decode()
    assert "open-meteo.com" not in response.content.decode()


def test_weather_daily_rate_limit_fails_fast_with_429_json(client, monkeypatch):
    def get(url, params=None, timeout=None):
        if "router.hereapi.com" in url:
            return route_response()
        return FakeResponse(
            429,
            {"error": True, "reason": "Daily API request limit reached"},
            headers={"Retry-After": "60"},
        )

    monkeypatch.setattr("requests.get", get)
    response = client.post("/api/analyze", body(), format="json")
    assert response.status_code == 429
    data = response.json()
    assert data["error"]["code"] == "rate_limited"
    assert "Weather service rate limit exceeded." in data["error"]["message"]
    assert "Retry after 60 seconds." in data["error"]["message"]


def test_partial_weather_nulls_stay_checkpoint_missing(client, monkeypatch):
    mock_both_providers(monkeypatch, missing_cell=True)
    response = client.post("/api/analyze", body(interval_miles=10), format="json")
    assert response.status_code == 200
    checkpoints = response.json()["routes"][0]["checkpoints"]
    missing = [cp for cp in checkpoints if cp["weather_missing"]]
    assert missing
    assert all(cp["risk_level"] is None and cp["weather"] is None for cp in missing)


def test_geocode_proxy_returns_limited_shape(client, monkeypatch):
    mock_both_providers(monkeypatch)
    response = client.get("/api/geocode", {"q": "Salt Lake City"})
    assert response.status_code == 200
    assert response.json() == {
        "results": [
            {"label": "Salt Lake City, UT, United States", "lat": 40.76, "lon": -111.89}
        ]
    }


def test_geocode_upstream_error_is_safe(client, monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: FakeResponse(
            503, {"message": f"{TOKEN} https://autosuggest.search.hereapi.com/v1/autosuggest?apiKey={TOKEN}"}
        ),
    )
    response = client.get("/api/geocode", {"q": "Salt Lake"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "geocoding_unavailable"
    assert TOKEN not in response.content.decode()
    assert "hereapi.com" not in response.content.decode()


@pytest.mark.parametrize("query", [None, "", "x", "  "])
def test_geocode_rejects_empty_or_short_query(client, monkeypatch, query):
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: pytest.fail("HTTP should not run"))
    response = client.get("/api/geocode", {} if query is None else {"q": query})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_cors_allows_only_configured_frontend_origin(client, monkeypatch):
    with override_settings(FRONTEND_ORIGIN="https://frontend.example"):
        allowed = client.get("/api/health", HTTP_ORIGIN="https://frontend.example")
        denied = client.get("/api/health", HTTP_ORIGIN="https://other.example")
    assert allowed.headers["Access-Control-Allow-Origin"] == "https://frontend.example"
    assert "Access-Control-Allow-Origin" not in denied.headers


def test_gzip_compression_returns_identical_json_payload(client, monkeypatch):
    import gzip

    mock_both_providers(monkeypatch)
    uncompressed = client.post("/api/analyze", body(), format="json")
    assert uncompressed.status_code == 200
    assert "gzip" not in uncompressed.get("Content-Encoding", "")

    compressed = client.post("/api/analyze", body(), format="json", HTTP_ACCEPT_ENCODING="gzip")
    assert compressed.status_code == 200
    assert compressed["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in compressed["Vary"]

    decompressed_bytes = gzip.decompress(compressed.content)
    assert json.loads(decompressed_bytes.decode("utf-8")) == uncompressed.json()


def three_route_payload():
    payload = recorded_route()
    while len(payload["routes"]) < 3:
        payload["routes"].append(copy.deepcopy(payload["routes"][0]))
    return payload


def test_analyze_returns_three_routes_when_provider_returns_three(client, monkeypatch):
    def get(url, params=None, timeout=None):
        if "router.hereapi.com" in url:
            return route_response(payload=three_route_payload())
        return weather_response(params)

    monkeypatch.setattr("requests.get", get)
    data = client.post("/api/analyze", body(), format="json").json()
    assert len(data["routes"]) == 3
    assert sorted(data["ranking"]) == [0, 1, 2]
    assert data["notices"] == []


def test_analyze_asks_here_for_truck_routes_with_two_alternatives(client, monkeypatch):
    seen = {}

    def get(url, params=None, timeout=None):
        if "router.hereapi.com" in url:
            seen.update(params)
            return route_response()
        return weather_response(params)

    monkeypatch.setattr("requests.get", get)
    client.post("/api/analyze", body(), format="json")
    assert seen["transportMode"] == "truck" and seen["alternatives"] == "2"
    assert seen["apiKey"] == TOKEN
