import copy
import math

import pytest
import requests
from weather_helpers import FIXTURE_POINTS, FakeResponse, load_fixture, open_meteo_response

from weather import client
from weather.client import (
    HOURLY_VARIABLES,
    MAX_POINTS_PER_REQUEST,
    build_params,
    fetch_hourly,
    parse_payload,
)
from weather.errors import (
    WeatherConnectionError,
    WeatherError,
    WeatherRateLimitError,
    WeatherRequestError,
    WeatherResponseError,
    WeatherServerError,
    WeatherTimeoutError,
)

# ------------------------------------------------------------------ what is requested


def test_units_timezone_and_variables_are_in_the_url(fake_http):
    fetch_hourly([(40.7, -111.9)], forecast_days=2)
    query = fake_http.query()
    assert query["wind_speed_unit"] == "mph"
    assert query["precipitation_unit"] == "inch"
    assert query["timezone"] == "UTC"
    assert query["hourly"].split(",") == ["wind_speed_10m", "rain", "showers", "snowfall"]
    assert query["forecast_days"] == "2"
    # The same facts, spelled out in the raw URL a reviewer would see:
    url = fake_http.full_url()
    assert "wind_speed_unit=mph" in url
    assert "precipitation_unit=inch" in url
    assert "timezone=UTC" in url
    assert url.startswith("https://api.open-meteo.com/v1/forecast?")


def test_default_url_and_proxy_path(fake_http, caplog):
    fetch_hourly([(40.7, -111.9)], forecast_days=1)
    assert fake_http.calls[0]["url"] == "https://api.open-meteo.com/v1/forecast"
    assert "X-Proxy-Token" not in fake_http.calls[0]["headers"]

    token = "secret-proxy-token-xyz"
    fetch_hourly(
        [(40.7, -111.9)],
        forecast_days=1,
        base_url="https://weather-proxy.internal/api/",
        proxy_token=token,
    )
    assert fake_http.calls[1]["url"] == "https://weather-proxy.internal/api/v1/forecast"
    assert fake_http.calls[1]["headers"]["X-Proxy-Token"] == token
    assert token not in caplog.text


def test_gusts_are_not_requested(fake_http):
    fetch_hourly([(40.7, -111.9)], forecast_days=1)
    assert "gust" not in fake_http.full_url()
    assert "wind_gusts_10m" not in HOURLY_VARIABLES


def test_precipitation_total_and_depth_variables_are_not_requested(fake_http):
    # `precipitation` double counts rain+showers+snow; `snow_depth` is a different quantity.
    fetch_hourly([(40.7, -111.9)], forecast_days=1)
    hourly = fake_http.query()["hourly"].split(",")
    assert "precipitation" not in hourly
    assert "snow_depth" not in hourly


def test_many_points_go_in_one_request_in_order(fake_http):
    series = fetch_hourly(FIXTURE_POINTS, forecast_days=2)
    assert fake_http.count == 1
    query = fake_http.query()
    assert query["latitude"] == "40.7,39.7,41.1"
    assert query["longitude"] == "-111.9,-105,-104.8"
    assert len(series) == 3


def test_single_point_response_object_is_accepted(fake_http):
    (only,) = fetch_hourly([(40.7, -111.9)], forecast_days=2)
    assert len(only.times) == 48


def test_timeout_is_passed_to_the_http_call(fake_http):
    fetch_hourly([(40.7, -111.9)], forecast_days=1, timeout=3.5)
    assert fake_http.calls[0]["timeout"] == 3.5


def test_default_timeout_is_set(fake_http):
    fetch_hourly([(40.7, -111.9)], forecast_days=1)
    assert fake_http.calls[0]["timeout"] == client.DEFAULT_TIMEOUT
    assert fake_http.calls[0]["timeout"] is not None


def test_more_than_the_per_request_cap_is_split(fake_http):
    points = [(30.0 + 0.1 * i, -100.0) for i in range(MAX_POINTS_PER_REQUEST + 5)]
    series = fetch_hourly(points, forecast_days=1)
    assert len(series) == len(points)
    assert fake_http.count == 2
    sizes = [len(c["params"]["latitude"].split(",")) for c in fake_http.calls]
    assert sizes == [MAX_POINTS_PER_REQUEST, 5]


def test_exactly_the_cap_is_one_request(fake_http):
    points = [(30.0 + 0.1 * i, -100.0) for i in range(MAX_POINTS_PER_REQUEST)]
    fetch_hourly(points, forecast_days=1)
    assert fake_http.count == 1


@pytest.mark.parametrize("days", [0, -1, 17, 100])
def test_forecast_days_out_of_range_rejected_before_any_request(fake_http, days):
    with pytest.raises(ValueError):
        fetch_hourly([(40.7, -111.9)], forecast_days=days)
    assert fake_http.count == 0


@pytest.mark.parametrize("days", [1, 16])
def test_forecast_days_limits_accepted(days):
    assert build_params([(40.7, -111.9)], days)["forecast_days"] == str(days)


@pytest.mark.parametrize(
    "points",
    [
        [],
        [(91.0, 0.0)],
        [(0.0, 181.0)],
        [(math.nan, 0.0)],
        [(0.0, math.inf)],
        [(40.7, -111.9), (95, 0)],
    ],
)
def test_bad_points_rejected_before_any_request(fake_http, points):
    with pytest.raises(ValueError):
        fetch_hourly(points, forecast_days=1)
    assert fake_http.count == 0


def test_coordinate_formatting():
    params = build_params([(0.0, -0.0), (40.123456, -111.98765), (-33.5, 151.0)], 1)
    assert params["latitude"] == "0,40.1235,-33.5"
    assert params["longitude"] == "0,-111.9877,151"


# ------------------------------------------------------------------ parsing


def test_fixture_parses_into_aligned_series(fake_http):
    series = fetch_hourly(FIXTURE_POINTS, forecast_days=2)
    first = series[0]
    assert len(first.times) == len(first.values) == 48
    assert first.times[0].isoformat() == "2026-10-17T00:00:00+00:00"
    assert first.times[-1].isoformat() == "2026-10-18T23:00:00+00:00"
    assert first.values[0].wind_mph == 5.0
    assert first.values[20].rain_in == 0.12
    assert first.values[20].showers_in == 0.03
    assert first.values[30].snow_in == 0.6


def test_times_are_utc_aware(fake_http):
    (only,) = fetch_hourly([(40.7, -111.9)], forecast_days=1)
    assert all(t.utcoffset().total_seconds() == 0 for t in only.times)


def test_nulls_become_none_never_zero():
    series = parse_payload(load_fixture(), 3)
    denver = series[1]
    assert denver.values[5].wind_mph is None
    assert denver.values[10].showers_in is None
    assert denver.values[30].snow_in is None
    # Their neighbours are untouched real numbers.
    assert denver.values[4].wind_mph == pytest.approx(10.4)
    assert denver.values[10].rain_in == 0.0  # a real zero stays a real zero


@pytest.mark.parametrize(
    "bad", [None, "5.0", "", True, False, [], {}, -1.0, -0.0001, math.nan, math.inf]
)
def test_unusable_values_are_missing_not_zero(bad):
    payload = copy.deepcopy(load_fixture())[0]
    payload["hourly"]["wind_speed_10m"][3] = bad
    (series,) = parse_payload(payload, 1)
    assert series.values[3].wind_mph is None
    assert series.values[2].wind_mph == 6.0


def test_integer_values_are_accepted_as_numbers():
    payload = copy.deepcopy(load_fixture())[0]
    payload["hourly"]["wind_speed_10m"][3] = 7
    (series,) = parse_payload(payload, 1)
    assert series.values[3].wind_mph == 7.0


def test_zero_is_a_valid_value():
    payload = copy.deepcopy(load_fixture())[0]
    payload["hourly"]["wind_speed_10m"][3] = 0
    (series,) = parse_payload(payload, 1)
    assert series.values[3].wind_mph == 0.0


# ------------------------------------------------------------------ unit and shape checks


@pytest.mark.parametrize(
    ("variable", "unit"),
    [
        ("wind_speed_10m", "km/h"),
        ("wind_speed_10m", "m/s"),
        ("wind_speed_10m", "kn"),
        ("wind_speed_10m", None),
        ("rain", "mm"),
        ("showers", "mm"),
        ("snowfall", "cm"),
        ("snowfall", "mm"),
        ("rain", None),
    ],
)
def test_wrong_response_units_are_rejected(variable, unit):
    payload = copy.deepcopy(load_fixture())[0]
    payload["hourly_units"][variable] = unit
    with pytest.raises(WeatherResponseError, match=variable):
        parse_payload(payload, 1)


@pytest.mark.parametrize("wind_unit", ["mp/h", "mph"])
def test_both_spellings_of_mph_are_accepted(wind_unit):
    payload = copy.deepcopy(load_fixture())[0]
    payload["hourly_units"]["wind_speed_10m"] = wind_unit
    assert parse_payload(payload, 1)


def test_missing_units_are_rejected():
    payload = copy.deepcopy(load_fixture())[0]
    del payload["hourly_units"]
    with pytest.raises(WeatherResponseError, match="units"):
        parse_payload(payload, 1)


def test_non_utc_response_is_rejected():
    payload = copy.deepcopy(load_fixture())[0]
    payload["utc_offset_seconds"] = -21600
    with pytest.raises(WeatherResponseError, match="UTC"):
        parse_payload(payload, 1)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("hourly"),
        lambda p: p["hourly"].pop("time"),
        lambda p: p["hourly"].update(time=[]),
        lambda p: p["hourly"].pop("snowfall"),
        lambda p: p["hourly"].update(rain=p["hourly"]["rain"][:-1]),
        lambda p: p["hourly"].update(showers="not a list"),
        lambda p: p["hourly"]["time"].__setitem__(3, "not a time"),
        lambda p: p["hourly"]["time"].__setitem__(3, "2026-10-17T03:30"),
        lambda p: p["hourly"]["time"].__setitem__(3, p["hourly"]["time"][2]),
    ],
    ids=[
        "no-hourly",
        "no-time",
        "empty-time",
        "no-snowfall",
        "short-rain",
        "not-a-list",
        "bad-time",
        "half-hour-time",
        "duplicate-time",
    ],
)
def test_malformed_payloads_raise_response_error(mutate):
    payload = copy.deepcopy(load_fixture())[0]
    mutate(payload)
    with pytest.raises(WeatherResponseError):
        parse_payload(payload, 1)


def test_wrong_number_of_locations_is_rejected():
    with pytest.raises(WeatherResponseError, match="expected 2"):
        parse_payload(load_fixture(), 2)
    with pytest.raises(WeatherResponseError, match="expected 3"):
        parse_payload(load_fixture()[:2], 3)


@pytest.mark.parametrize("payload", ["text", 5, None, [None], [[]]])
def test_non_object_payloads_are_rejected(payload):
    with pytest.raises(WeatherResponseError):
        parse_payload(payload, 1)


# ------------------------------------------------------------------ error mapping


@pytest.mark.parametrize(
    ("response", "raises", "expected"),
    [
        (None, requests.Timeout("slow"), WeatherTimeoutError),
        (None, requests.ConnectTimeout("slow"), WeatherTimeoutError),
        (None, requests.ReadTimeout("slow"), WeatherTimeoutError),
        (None, requests.ConnectionError("down"), WeatherConnectionError),
        (None, requests.RequestException("odd"), WeatherConnectionError),
        (
            FakeResponse(429, {"error": True, "reason": "Too many requests"}),
            None,
            WeatherRateLimitError,
        ),
        (
            FakeResponse(400, {"error": True, "reason": "Cannot initialize WeatherVariable"}),
            None,
            WeatherRequestError,
        ),
        (FakeResponse(404, {"error": True, "reason": "Not found"}), None, WeatherRequestError),
        (FakeResponse(403, text="Forbidden"), None, WeatherRequestError),
        (FakeResponse(500, {"error": True, "reason": "boom"}), None, WeatherServerError),
        (FakeResponse(502, text="Bad Gateway"), None, WeatherServerError),
        (FakeResponse(503, text="", bad_json=True), None, WeatherServerError),
        (FakeResponse(200, {"error": True, "reason": "odd"}), None, WeatherRequestError),
        (FakeResponse(200, bad_json=True, text="<html>"), None, WeatherResponseError),
    ],
)
def test_error_mapping(fake_http, response, raises, expected):
    fake_http.raises = raises
    if response is not None:
        fake_http.respond_with(response)
    with pytest.raises(expected) as info:
        fetch_hourly([(40.7, -111.9)], forecast_days=1)
    assert isinstance(info.value, WeatherError)


def test_rate_limit_retry_after_is_parsed(fake_http):
    fake_http.respond_with(
        FakeResponse(429, {"error": True, "reason": "slow"}, headers={"Retry-After": "30"})
    )
    with pytest.raises(WeatherRateLimitError) as info:
        fetch_hourly([(40.7, -111.9)], forecast_days=1)
    assert info.value.retry_after == 30.0


def test_rate_limit_without_retry_after(fake_http):
    fake_http.respond_with(FakeResponse(429, {"error": True, "reason": "slow"}))
    with pytest.raises(WeatherRateLimitError) as info:
        fetch_hourly([(40.7, -111.9)], forecast_days=1)
    assert info.value.retry_after is None


def test_rate_limit_retries_then_succeeds(fake_http, monkeypatch):
    responses = iter(
        [
            FakeResponse(429, {"error": True, "reason": "busy"}),
            FakeResponse(429, {"error": True, "reason": "still busy"}),
            FakeResponse(200, load_fixture()[0]),
        ]
    )
    fake_http.responder = lambda params: next(responses)
    delays = []
    monkeypatch.setattr(client.time, "sleep", delays.append)

    (series,) = fetch_hourly([(40.7, -111.9)], forecast_days=1)

    assert len(series.times) == 48
    assert fake_http.count == 3
    assert delays == [1.0, 2.0]


def test_rate_limit_honours_retry_after_when_it_fits_budget(fake_http, monkeypatch):
    responses = iter(
        [
            FakeResponse(429, {"error": True, "reason": "busy"}, headers={"Retry-After": "4"}),
            FakeResponse(200, load_fixture()[0]),
        ]
    )
    fake_http.responder = lambda params: next(responses)
    delays = []
    monkeypatch.setattr(client.time, "sleep", delays.append)

    fetch_hourly([(40.7, -111.9)], forecast_days=1)

    assert fake_http.count == 2
    assert delays == [4.0]


def test_rate_limit_exhaustion_keeps_retry_hint_and_sanitizes_logs(fake_http, monkeypatch, caplog):
    sensitive_reason = "https://weather.example/v1?latitude=40.7&longitude=-111.9&token=secret"
    responses = iter(
        [
            FakeResponse(
                429,
                {"error": True, "reason": sensitive_reason},
                headers={"Retry-After": "2"},
            )
            for _ in range(3)
        ]
    )
    fake_http.responder = lambda params: next(responses)
    delays = []
    monkeypatch.setattr(client.time, "sleep", delays.append)

    with pytest.raises(WeatherRateLimitError) as info:
        fetch_hourly([(40.7, -111.9)], forecast_days=1)

    assert fake_http.count == 3
    assert delays == [2.0, 2.0]
    assert info.value.retry_after == 2.0
    combined = caplog.text + str(info.value)
    assert "weather.example" not in combined
    assert "40.7" not in combined
    assert "-111.9" not in combined
    assert "secret" not in combined
    assert "status=429" in caplog.text
    assert "provider=Open-Meteo" in caplog.text


@pytest.mark.parametrize(
    "reason",
    [
        "Daily API request limit exceeded",
        "daily limit reached",
        "Open-Meteo DAILY quota exhausted",
    ],
)
def test_rate_limit_daily_fails_fast_without_retries(fake_http, monkeypatch, caplog, reason):
    fake_http.respond_with(
        FakeResponse(
            429,
            {"error": True, "reason": reason},
            headers={"Retry-After": "30"},
        )
    )
    delays = []
    monkeypatch.setattr(client.time, "sleep", delays.append)

    with pytest.raises(WeatherRateLimitError) as info:
        fetch_hourly([(40.7, -111.9)], forecast_days=1)

    assert fake_http.count == 1
    assert delays == []
    assert info.value.retry_after == 30.0
    assert reason in str(info.value)
    assert "status=429" in caplog.text


def test_proxy_token_is_never_logged_on_rate_limit(fake_http, caplog):
    token = "super-secret-proxy-token-429"
    fake_http.respond_with(
        FakeResponse(
            429,
            {"error": True, "reason": "Daily limit exceeded"},
            headers={"Retry-After": "10"},
        )
    )
    with pytest.raises(WeatherRateLimitError):
        fetch_hourly(
            [(40.7, -111.9)],
            forecast_days=1,
            base_url="https://weather-proxy.internal",
            proxy_token=token,
        )
    assert token not in caplog.text


@pytest.mark.parametrize(
    ("header", "expected"),
    [("0", 0.0), ("2.5", 2.5), ("-1", None), ("not-a-delay", None)],
)
def test_retry_after_header_parsing(header, expected):
    assert client._retry_after_seconds(header) == expected


def test_request_error_carries_status_and_provider_reason(fake_http):
    fake_http.respond_with(
        FakeResponse(400, {"error": True, "reason": "forecast_days out of range"})
    )
    with pytest.raises(WeatherRequestError) as info:
        fetch_hourly([(40.7, -111.9)], forecast_days=1)
    assert info.value.status_code == 400
    assert "forecast_days out of range" in str(info.value)


def test_server_error_carries_status(fake_http):
    fake_http.respond_with(FakeResponse(503, text="down"))
    with pytest.raises(WeatherServerError) as info:
        fetch_hourly([(40.7, -111.9)], forecast_days=1)
    assert info.value.status_code == 503


@pytest.mark.parametrize(
    "raises",
    [
        requests.Timeout("https://api.open-meteo.com/v1/forecast?latitude=40.7 timed out"),
        requests.ConnectionError("HTTPSConnectionPool(host='api.open-meteo.com') failed"),
    ],
)
def test_network_error_messages_do_not_leak_urls_or_chain(fake_http, raises):
    fake_http.raises = raises
    with pytest.raises(WeatherError) as info:
        fetch_hourly([(40.7, -111.9)], forecast_days=1)
    assert "api.open-meteo.com" not in str(info.value)
    assert "latitude" not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_long_provider_reasons_are_truncated(fake_http):
    fake_http.respond_with(FakeResponse(400, {"error": True, "reason": "x" * 5000}))
    with pytest.raises(WeatherRequestError) as info:
        fetch_hourly([(40.7, -111.9)], forecast_days=1)
    assert len(str(info.value)) < 500


# ------------------------------------------------------------------ transient failures

OVERLOADED = {"reason": "The service is overloaded", "error": True}
POINT = [(40.7, -111.9)]


def _sequence(fake_http, responses):
    iterator = iter(responses)
    fake_http.responder = lambda params: next(iterator)


@pytest.mark.parametrize("status", [503, 500, 400, 200])
def test_overloaded_reply_is_retried_then_succeeds(fake_http, monkeypatch, status):
    _sequence(
        fake_http,
        [FakeResponse(status, OVERLOADED), FakeResponse(200, load_fixture()[0])],
    )
    delays = []
    monkeypatch.setattr(client.time, "sleep", delays.append)

    (series,) = fetch_hourly(POINT, forecast_days=1)

    assert len(series.times) == 48
    assert fake_http.count == 2
    assert delays == [1.0]


def test_5xx_without_a_reason_is_retried_with_1s_then_2s_backoff(fake_http, monkeypatch):
    _sequence(
        fake_http,
        [
            FakeResponse(502, text="Bad Gateway"),
            FakeResponse(503, text="", bad_json=True),
            FakeResponse(200, load_fixture()[0]),
        ],
    )
    delays = []
    monkeypatch.setattr(client.time, "sleep", delays.append)
    fetch_hourly(POINT, forecast_days=1)
    assert fake_http.count == 3
    assert delays == [1.0, 2.0]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(503, WeatherServerError), (200, WeatherRequestError), (400, WeatherRequestError)],
)
def test_overloaded_retries_exhausted_fail_as_before(fake_http, monkeypatch, status, expected):
    fake_http.respond_with(FakeResponse(status, OVERLOADED))
    delays = []
    monkeypatch.setattr(client.time, "sleep", delays.append)

    with pytest.raises(expected) as info:
        fetch_hourly(POINT, forecast_days=1)

    assert fake_http.count == 3  # first try + 2 retries
    assert delays == [1.0, 2.0]
    assert "overloaded" in str(info.value)


def test_retry_stops_when_the_shared_budget_is_spent(fake_http, monkeypatch):
    fake_http.respond_with(FakeResponse(503, OVERLOADED))
    now = [0.0]
    monkeypatch.setattr(client.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(client.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + 10))
    with pytest.raises(WeatherServerError):
        fetch_hourly(POINT, forecast_days=1)
    assert fake_http.count == 2  # one retry, then the 6 s budget is gone


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(400, {"error": True, "reason": "Cannot initialize WeatherVariable"}),
        FakeResponse(404, {"error": True, "reason": "Not found"}),
        FakeResponse(200, {"error": True, "reason": "odd"}),
    ],
)
def test_other_errors_are_not_retried(fake_http, response):
    fake_http.respond_with(response)
    with pytest.raises(WeatherRequestError):
        fetch_hourly(POINT, forecast_days=1)
    assert fake_http.count == 1


@pytest.mark.parametrize("raises", [requests.Timeout("slow"), requests.ConnectionError("down")])
def test_timeouts_and_connection_errors_are_not_retried(fake_http, raises):
    fake_http.raises = raises
    with pytest.raises(WeatherError):
        fetch_hourly(POINT, forecast_days=1)
    assert fake_http.count == 1


# ------------------------------------------------------------------ failure logging


def _failure_lines(caplog):
    return [r.getMessage() for r in caplog.records if "weather request failed" in r.getMessage()]


def test_each_failed_attempt_is_logged_with_all_fields(fake_http, monkeypatch, caplog):
    fake_http.respond_with(FakeResponse(503, OVERLOADED))
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)
    with pytest.raises(WeatherServerError):
        fetch_hourly(POINT, forecast_days=1)

    lines = _failure_lines(caplog)
    assert len(lines) == 3
    for number, line in enumerate(lines, start=1):
        assert "provider=Open-Meteo" in line
        assert "exception=WeatherServerError" in line
        assert "status=503" in line
        assert f"attempt={number}/3" in line
        assert "elapsed_s=" in line
        assert "locations=1" in line
        assert "chunk=1/1" in line
        assert "other_requests_in_flight=0" in line
        assert "reason=The service is overloaded" in line
    assert [("retry=True" in line) for line in lines] == [True, True, False]


@pytest.mark.parametrize(
    ("raises", "exception", "reason"),
    [
        (requests.ReadTimeout("https://x/?latitude=1"), "WeatherTimeoutError", "ReadTimeout"),
        (requests.ConnectionError("down"), "WeatherConnectionError", "ConnectionError"),
    ],
)
def test_network_failures_are_logged_with_the_exception_type_only(
    fake_http, caplog, raises, exception, reason
):
    fake_http.raises = raises
    with pytest.raises(WeatherError):
        fetch_hourly(POINT, forecast_days=1)
    (line,) = _failure_lines(caplog)
    assert f"exception={exception}" in line and "status=None" in line
    assert line.endswith(f"reason={reason}")
    assert "https" not in line


def test_log_reason_is_truncated_and_sanitized(fake_http, monkeypatch, caplog):
    token = "secret-proxy-token-5xx"
    reason = (
        "overloaded see https://api.open-meteo.com/v1/forecast?latitude=40.7&longitude=-111.9 "
        "latitude=40.7 longitude=-111.9 token=abc123 " + "y" * 500
    )
    fake_http.respond_with(FakeResponse(503, {"error": True, "reason": reason}))
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)
    with pytest.raises(WeatherServerError):
        fetch_hourly(
            POINT, forecast_days=1, base_url="https://proxy.internal", proxy_token=token
        )
    for line in _failure_lines(caplog):
        logged_reason = line.split("reason=", 1)[1]
        assert len(logged_reason) <= 100
        for secret in ("api.open-meteo.com", "40.7", "-111.9", "abc123", token, "proxy.internal"):
            assert secret not in line
    assert token not in caplog.text and "proxy.internal" not in caplog.text


def test_failing_chunk_and_location_count_are_logged(fake_http, monkeypatch, caplog):
    points = [(round(i / 10, 1), -100.0) for i in range(client.MAX_POINTS_PER_REQUEST + 1)]
    fake_http.responder = lambda params: (
        FakeResponse(body=open_meteo_response(params))
        if fake_http.count == 1
        else FakeResponse(503, OVERLOADED)
    )
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)
    with pytest.raises(WeatherServerError):
        fetch_hourly(points, forecast_days=1)
    lines = _failure_lines(caplog)
    assert len(lines) == 3
    assert all("chunk=2/2" in line and "locations=1 " in line for line in lines)


def test_unparseable_payload_is_logged(fake_http, caplog):
    fake_http.respond_with(FakeResponse(200, {"hourly": {}}))
    with pytest.raises(WeatherResponseError):
        fetch_hourly(POINT, forecast_days=1)
    (line,) = _failure_lines(caplog)
    assert "exception=WeatherResponseError" in line and "status=200" in line


def test_invalid_json_body_is_logged(fake_http, caplog):
    fake_http.respond_with(FakeResponse(200, bad_json=True, text="<html>"))
    with pytest.raises(WeatherResponseError):
        fetch_hourly(POINT, forecast_days=1)
    (line,) = _failure_lines(caplog)
    assert "exception=WeatherResponseError" in line and "not valid JSON" in line
