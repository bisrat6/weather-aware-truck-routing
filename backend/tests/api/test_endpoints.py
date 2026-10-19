import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests
from django.test import override_settings
from rest_framework.test import APIClient

from tests.fallback_support import (
    EXCLUDE,
    MAIN,
    Directions,
    first_route_only,
    load_payload,
    scaled,
    with_routes,
)

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


def recorded_mapbox():
    return json.loads((FIXTURES / "mapbox_slc_provo.json").read_text(encoding="utf-8"))


def recorded_weather():
    return json.loads((FIXTURES / "open_meteo_recorded.json").read_text(encoding="utf-8"))


def route_response(*, status=200, payload=None, headers=None):
    return FakeResponse(status, recorded_mapbox() if payload is None else payload, headers)


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
    with override_settings(MAPBOX_TOKEN=TOKEN):
        yield APIClient()


def mock_both_providers(monkeypatch, *, weather_status=200, missing_cell=False):
    def get(url, params=None, timeout=None):
        if "directions/v5" in url:
            return route_response()
        if "open-meteo.com" in url:
            return weather_response(params, status=weather_status, missing_cell=missing_cell)
        if "geocoding/v5" in url:
            return FakeResponse(
                body={
                    "features": [
                        {"place_name": "Salt Lake City, Utah", "center": [-111.89, 40.76]}
                    ]
                }
            )
        raise AssertionError(f"unexpected upstream {url}")

    monkeypatch.setattr("requests.get", get)


def mock_weather_for_all_route_cells(monkeypatch, *, wind_mph=None):
    def get(url, params=None, timeout=None):
        if "directions/v5" in url:
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
        f"Only {len(data['routes'])} routes available (the provider returned 2; "
        "the detour search found 0 acceptable)"
    ]


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
            503, {"message": f"bad {TOKEN} https://api.mapbox.com?access_token={TOKEN}"}
        ),
    )
    response = client.post("/api/analyze", body(), format="json")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "routing_unavailable"
    assert TOKEN not in response.content.decode()
    assert "api.mapbox.com" not in response.content.decode()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "code": "NoRoute",
            "message": f"No route {TOKEN} https://api.mapbox.com/directions?access_token={TOKEN}",
        },
        {"code": "Ok", "routes": []},
    ],
    ids=["no-route-code", "empty-routes"],
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
    assert "api.mapbox.com" not in response.content.decode()


@pytest.mark.parametrize(
    ("upstream_message", "expected_code", "expected_message"),
    [
        (
            f"Route exceeds maximum distance limitation. {TOKEN} "
            "https://api.mapbox.com/directions?access_token=private",
            "route_too_far",
            "These places are too far apart for a driving route.",
        ),
        (
            f"Unsupported driving profile. provider-private-detail {TOKEN} "
            "https://api.mapbox.com/directions?access_token=private",
            "route_not_supported",
            "A driving route could not be built between these places.",
        ),
    ],
    ids=["maximum-distance", "other-invalid-input"],
)
def test_mapbox_invalid_input_422_is_mapped_and_sanitized(
    client, monkeypatch, upstream_message, expected_code, expected_message
):
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: FakeResponse(
            422, {"code": "InvalidInput", "message": upstream_message}
        ),
    )

    response = client.post("/api/analyze", body(), format="json")

    response_body = response.content.decode()
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": expected_code, "message": expected_message}
    }
    assert upstream_message not in response_body
    assert "provider-private-detail" not in response_body
    assert TOKEN not in response_body
    assert "api.mapbox.com" not in response_body


def test_routing_rate_limit_has_retry_hint_and_no_credentials(client, monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: FakeResponse(
            429,
            {"message": f"{TOKEN} https://api.mapbox.com"},
            {"Retry-After": "17"},
        ),
    )
    response = client.post("/api/analyze", body(), format="json")
    assert response.status_code == 429
    assert "17 seconds" in response.json()["error"]["message"]
    assert TOKEN not in response.content.decode()
    assert "api.mapbox.com" not in response.content.decode()


def test_weather_down_returns_safe_502(client, monkeypatch):
    def get(url, params=None, timeout=None):
        if "directions/v5" in url:
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
        if "directions/v5" in url:
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
        if "directions/v5" in url:
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
        "results": [{"label": "Salt Lake City, Utah", "lat": 40.76, "lon": -111.89}]
    }


def test_geocode_upstream_error_is_safe(client, monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: FakeResponse(
            503, {"message": f"{TOKEN} https://api.mapbox.com/geocoding"}
        ),
    )
    response = client.get("/api/geocode", {"q": "Salt Lake"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "geocoding_unavailable"
    assert TOKEN not in response.content.decode()
    assert "api.mapbox.com" not in response.content.decode()


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



# ------------------------------------------------------------------ third-route fallback


def mock_directions_and_weather(monkeypatch, directions):
    def get(url, params=None, timeout=None):
        if "directions/v5" in url:
            return directions(url, params, timeout)
        if "open-meteo.com" in url:
            return weather_response(params)
        raise AssertionError(f"unexpected upstream {url}")

    monkeypatch.setattr("requests.get", get)


def test_analyze_returns_fallback_route_with_its_origin_label(client, monkeypatch):
    directions = Directions(
        {"main": load_payload(MAIN), "exclude": scaled(load_payload(EXCLUDE), duration=0.5)},
        default=(500, {}),
    )
    mock_directions_and_weather(monkeypatch, directions)
    data = client.post("/api/analyze", body(), format="json").json()
    assert [r["origin_label"] for r in data["routes"]] == [
        "provider alternative",
        "provider alternative",
        "avoiding motorways",
    ]
    assert sorted(data["ranking"]) == [0, 1, 2]
    assert data["notices"] == []


@pytest.mark.parametrize(
    "failure", [requests.Timeout("slow"), (429, {"message": "limit"}), (500, {})]
)
def test_analyze_still_returns_main_result_when_every_fallback_request_fails(
    client, monkeypatch, failure
):
    directions = Directions({"main": load_payload(MAIN)}, default=failure)
    mock_directions_and_weather(monkeypatch, directions)
    response = client.post("/api/analyze", body(), format="json")
    assert response.status_code == 200
    data = response.json()
    assert len(data["routes"]) == 2
    assert data["notices"] == [
        "Only 2 routes available (the provider returned 2; the detour search found 0 acceptable)"
    ]
    assert len(directions.extra_calls) <= 5


def test_analyze_notice_counts_provider_and_accepted_routes(client, monkeypatch):
    directions = Directions(
        {
            "main": first_route_only(load_payload(MAIN)),
            "exclude": first_route_only(scaled(load_payload(EXCLUDE), duration=0.5)),
        },
        default=(500, {}),
    )
    mock_directions_and_weather(monkeypatch, directions)
    data = client.post("/api/analyze", body(), format="json").json()
    assert [r["origin_label"] for r in data["routes"]] == [
        "provider alternative",
        "avoiding motorways",
    ]
    assert data["notices"] == [
        "Only 2 routes available (the provider returned 1; the detour search found 1 acceptable)"
    ]


def test_analyze_makes_no_fallback_requests_when_provider_returns_three(client, monkeypatch):
    directions = Directions({"main": with_routes(load_payload(MAIN), 3)})
    mock_directions_and_weather(monkeypatch, directions)
    data = client.post("/api/analyze", body(), format="json").json()
    assert len(data["routes"]) == 3
    assert directions.calls == ["main"]
    assert data["notices"] == []


def test_failed_fallback_does_not_change_ranking(client, monkeypatch):
    def analyze(directions, patch_identity=False):
        mock_directions_and_weather(monkeypatch, directions)
        if patch_identity:
            from routing.fallback import FallbackReport

            monkeypatch.setattr(
                "api.views.augment_routes",
                lambda routes, *a, **k: (list(routes), FallbackReport(len(routes))),
            )
        data = client.post("/api/analyze", body(), format="json").json()
        return {k: data[k] for k in ("routes", "ranking", "recommended", "recommendation_reason")}

    with_failed_fallback = analyze(Directions({"main": load_payload(MAIN)}, default=(500, {})))
    without_fallback = analyze(Directions({"main": load_payload(MAIN)}), patch_identity=True)
    assert with_failed_fallback == without_fallback
