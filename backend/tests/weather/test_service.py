"""WeatherService end to end with mocked HTTP: cache, ETA-to-hour, nulls, window, errors."""

from datetime import UTC, datetime, timedelta

import pytest
import requests
from weather_helpers import CHEYENNE, DENVER, SLC, FakeResponse, open_meteo_response

from weather.cache import WeatherCache
from weather.errors import (
    MissingWeatherDataError,
    WeatherRateLimitError,
    WeatherTimeoutError,
)
from weather.models import MissingWeather, WeatherSample
from weather.service import WeatherService

TODAY = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)  # the fixture's first hour
NOW = TODAY + timedelta(hours=6, minutes=30)


def at(day=0, hour=0, minute=0):
    return TODAY + timedelta(days=day, hours=hour, minutes=minute)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def service(fake_http, clock):
    return WeatherService(cache=WeatherCache(ttl_seconds=600, clock=clock), now=lambda: NOW)


# ------------------------------------------------------------------ ETA to hour, end to end


def test_eta_selects_the_nearest_forecast_hour(service):
    # SLC wind in the fixture is 5.0 + 0.5 x hour index.
    assert service.weather_at(*SLC, at(hour=10, minute=10)).wind_mph == 10.0
    assert service.weather_at(*SLC, at(hour=10, minute=29)).wind_mph == 10.0
    assert service.weather_at(*SLC, at(hour=10, minute=30)).wind_mph == 10.5
    assert service.weather_at(*SLC, at(hour=10, minute=55)).wind_mph == 10.5


def test_sample_reports_the_hour_used(service):
    sample = service.weather_at(*SLC, at(hour=10, minute=40))
    assert sample.hour == at(hour=11)


def test_rain_and_showers_are_summed_and_snow_passes_through(service):
    sample = service.weather_at(*SLC, at(hour=20))  # rain 0.12 + showers 0.03
    assert sample.rain_in_hr == pytest.approx(0.15)
    assert sample.snow_in_hr == 0.0
    assert service.weather_at(*SLC, at(day=1, hour=6)).snow_in_hr == 0.6  # index 30


def test_severe_values_reach_the_caller_unchanged(service):
    assert service.weather_at(*CHEYENNE, at(hour=12)).wind_mph == 52.3
    assert service.weather_at(*CHEYENNE, at(hour=13)).snow_in_hr == 2.5


def test_eta_in_another_timezone(service):
    from datetime import timezone

    six_west = timezone(timedelta(hours=-6))
    eta = datetime(2026, 10, 17, 4, 10, tzinfo=six_west)  # 10:10 UTC
    assert service.weather_at(*SLC, eta).wind_mph == 10.0


# ------------------------------------------------------------------ cache hit and miss


def test_first_lookup_fetches_the_snapped_cell_centre(service, fake_http):
    service.weather_at(*SLC, at(hour=10))
    assert fake_http.count == 1
    query = fake_http.query()
    assert query["latitude"] == "40.7"  # SLC (40.71, -111.88) snapped to its cell centre
    assert query["longitude"] == "-111.9"
    assert query["wind_speed_unit"] == "mph"
    assert query["precipitation_unit"] == "inch"
    assert query["timezone"] == "UTC"


def test_same_cell_and_hour_is_a_cache_hit(service, fake_http):
    service.weather_at(40.70, -111.90, at(hour=10))
    service.weather_at(40.74, -111.86, at(hour=10))  # different point, same 0.1 degree cell
    assert fake_http.count == 1
    assert service.cache.stats.hits == 1


def test_other_hours_of_a_fetched_cell_are_hits(service, fake_http):
    service.weather_at(*SLC, at(hour=10))
    for hour in (0, 5, 12, 23):  # the same fetch returned the whole day
        service.weather_at(*SLC, at(hour=hour))
    assert fake_http.count == 1


def test_different_cell_is_a_miss(service, fake_http):
    service.weather_at(*SLC, at(hour=10))
    service.weather_at(*DENVER, at(hour=12))  # (Denver hour 10 has a null showers value)
    assert fake_http.count == 2


def test_cache_miss_after_ttl_refetches(service, fake_http, clock):
    service.weather_at(*SLC, at(hour=10))
    clock.advance(599)
    service.weather_at(*SLC, at(hour=10))
    assert fake_http.count == 1  # still fresh
    clock.advance(1)  # age == ttl
    service.weather_at(*SLC, at(hour=10))
    assert fake_http.count == 2  # expired, fetched again


def test_hour_beyond_what_was_fetched_triggers_a_new_request(service, fake_http):
    service.weather_at(*SLC, at(hour=10))  # fetched 1 day
    assert fake_http.query()["forecast_days"] == "1"
    service.weather_at(*SLC, at(day=1, hour=10))  # tomorrow is not cached
    assert fake_http.count == 2
    assert fake_http.query()["forecast_days"] == "2"


# ------------------------------------------------------------------ one request for many


def test_many_cells_are_fetched_in_one_request_in_first_seen_order(service, fake_http):
    results = service.weather_at_many(
        [
            (*SLC, at(hour=10)),
            (*DENVER, at(hour=10)),
            (*CHEYENNE, at(hour=12)),
            (*SLC, at(hour=11)),  # same cell as the first: not repeated
        ]
    )
    assert fake_http.count == 1
    query = fake_http.query()
    assert query["latitude"] == "40.7,39.7,41.1"
    assert query["longitude"] == "-111.9,-105,-104.8"
    assert len(results) == 4


def test_results_come_back_in_query_order(service):
    results = service.weather_at_many(
        [(*CHEYENNE, at(hour=12)), (*SLC, at(hour=10)), (*SLC, at(hour=11))]
    )
    assert [r.wind_mph for r in results] == [52.3, 10.0, 10.5]


def test_only_uncached_cells_are_requested(service, fake_http):
    service.weather_at(*SLC, at(hour=10))
    service.weather_at_many([(*SLC, at(hour=10)), (*DENVER, at(hour=10))])
    assert fake_http.count == 2
    assert fake_http.query()["latitude"] == "39.7"  # SLC was cached, only Denver asked for


def test_fully_cached_batch_makes_no_request(service, fake_http):
    service.weather_at_many([(*SLC, at(hour=10)), (*DENVER, at(hour=10))])
    before = fake_http.count
    service.weather_at_many([(*SLC, at(hour=12)), (*DENVER, at(hour=14))])
    assert fake_http.count == before


def test_forecast_days_sized_to_the_latest_needed_hour(service, fake_http):
    service.weather_at_many([(*SLC, at(hour=3)), (*DENVER, at(day=2, hour=5))])
    assert fake_http.query()["forecast_days"] == "3"  # day 2 needs days 0, 1 and 2


@pytest.mark.parametrize(
    ("day", "hour", "expected_days"),
    [(0, 0, "1"), (0, 23, "1"), (1, 0, "2"), (1, 13, "2"), (15, 12, "16"), (15, 23, "16")],
)
def test_forecast_days_boundaries(service, fake_http, day, hour, expected_days):
    service.weather_at(*SLC, at(day=day, hour=hour))
    assert fake_http.query()["forecast_days"] == expected_days


def test_empty_batch_returns_nothing_and_makes_no_request(service, fake_http):
    assert service.weather_at_many([]) == []
    assert fake_http.count == 0


# ------------------------------------------------------------------ missing data is flagged


def test_null_wind_is_flagged_not_zero(service):
    result = service.weather_at_many([(*DENVER, at(hour=5))])[0]  # Denver wind is null at 5
    assert isinstance(result, MissingWeather)
    assert result.variables == ("wind_speed_10m",)
    assert result.hour == at(hour=5)


def test_null_showers_is_flagged(service):
    result = service.weather_at_many([(*DENVER, at(hour=10))])[0]
    assert isinstance(result, MissingWeather)
    assert result.variables == ("showers",)


def test_null_snowfall_is_flagged(service):
    result = service.weather_at_many([(*DENVER, at(day=1, hour=6))])[0]  # index 30
    assert isinstance(result, MissingWeather)
    assert result.variables == ("snowfall",)


def test_a_null_does_not_hide_the_good_checkpoints_around_it(service):
    results = service.weather_at_many(
        [(*DENVER, at(hour=4)), (*DENVER, at(hour=5)), (*DENVER, at(hour=6))]
    )
    assert isinstance(results[0], WeatherSample)
    assert isinstance(results[1], MissingWeather)
    assert isinstance(results[2], WeatherSample)
    assert results[0].wind_mph == pytest.approx(10.4)
    assert results[2].wind_mph == pytest.approx(10.6)


def test_weather_at_raises_on_a_null_instead_of_returning_zeros(service):
    with pytest.raises(MissingWeatherDataError) as info:
        service.weather_at(*DENVER, at(hour=5))
    assert isinstance(info.value.missing, MissingWeather)
    assert "wind_speed_10m" in str(info.value)


def test_a_cached_null_stays_flagged_without_a_new_request(service, fake_http):
    service.weather_at_many([(*DENVER, at(hour=5))])
    before = fake_http.count
    again = service.weather_at_many([(*DENVER, at(hour=5))])[0]
    assert isinstance(again, MissingWeather)
    assert fake_http.count == before


def test_hour_missing_from_the_response_is_flagged(service, fake_http):
    short = open_meteo_response({"latitude": "40.7", "longitude": "-111.9", "forecast_days": "1"})
    for key in short["hourly"]:
        short["hourly"][key] = short["hourly"][key][:10]  # only hours 0-9 come back
    fake_http.respond_with(FakeResponse(body=short))
    result = service.weather_at_many([(*SLC, at(hour=20))])[0]
    assert isinstance(result, MissingWeather)
    assert "not present" in result.reason


# ------------------------------------------------------------------ forecast window


def test_hour_before_today_is_flagged_without_a_request(service, fake_http):
    result = service.weather_at_many([(*SLC, at(hour=-1))])[0]
    assert isinstance(result, MissingWeather)
    assert fake_http.count == 0


def test_first_hour_of_today_is_in_the_window(service):
    assert isinstance(service.weather_at(*SLC, at(hour=0)), WeatherSample)


def test_last_hour_of_the_window_is_in_and_the_next_is_out(service, fake_http):
    assert isinstance(service.weather_at(*SLC, at(day=15, hour=23)), WeatherSample)
    before = fake_http.count
    outside = service.weather_at_many([(*SLC, at(day=16, hour=0))])[0]
    assert isinstance(outside, MissingWeather)
    assert "window" in outside.reason
    assert fake_http.count == before


def test_rounding_up_across_the_window_edge_is_out(service):
    # 23:40 on the last day rounds to 00:00 on day 16, which is outside.
    result = service.weather_at_many([(*SLC, at(day=15, hour=23, minute=40))])[0]
    assert isinstance(result, MissingWeather)


def test_out_of_window_queries_do_not_stop_the_rest(service, fake_http):
    results = service.weather_at_many(
        [(*SLC, at(day=30)), (*SLC, at(hour=10)), (*SLC, at(hour=-5))]
    )
    assert isinstance(results[0], MissingWeather)
    assert isinstance(results[1], WeatherSample)
    assert isinstance(results[2], MissingWeather)
    assert fake_http.count == 1
    assert fake_http.query()["forecast_days"] == "1"  # sized by the in-window query only


def test_window_follows_the_injected_clock(fake_http):
    tomorrow = WeatherService(now=lambda: NOW + timedelta(days=1))
    result = tomorrow.weather_at_many([(*SLC, at(hour=10))])[0]  # now "yesterday"
    assert isinstance(result, MissingWeather)
    assert fake_http.count == 0


# ------------------------------------------------------------------ errors and bad input


def test_timeout_raises_and_nothing_is_cached(service, fake_http):
    fake_http.raises = requests.Timeout("slow")
    with pytest.raises(WeatherTimeoutError):
        service.weather_at(*SLC, at(hour=10))
    assert len(service.cache) == 0
    fake_http.raises = None
    assert isinstance(service.weather_at(*SLC, at(hour=10)), WeatherSample)  # recovers


def test_rate_limit_propagates(service, fake_http):
    fake_http.respond_with(FakeResponse(429, {"error": True, "reason": "Too many"}))
    with pytest.raises(WeatherRateLimitError):
        service.weather_at(*SLC, at(hour=10))
    assert len(service.cache) == 0


def test_naive_eta_rejected_before_any_request(service, fake_http):
    with pytest.raises(ValueError, match="timezone-aware"):
        service.weather_at(*SLC, datetime(2026, 10, 17, 10, 0))
    assert fake_http.count == 0


@pytest.mark.parametrize("point", [(91.0, 0.0), (0.0, 200.0), (float("nan"), 0.0)])
def test_bad_coordinates_rejected_before_any_request(service, fake_http, point):
    with pytest.raises(ValueError):
        service.weather_at(*point, at(hour=10))
    assert fake_http.count == 0


def test_default_service_builds_its_own_cache(fake_http):
    svc = WeatherService(now=lambda: NOW)
    svc.weather_at(*SLC, at(hour=10))
    assert len(svc.cache) > 0


def test_service_forwards_base_url_and_proxy_token(fake_http):
    token = "service-proxy-token"
    svc = WeatherService(
        base_url="https://proxy.service.example/",
        proxy_token=token,
        now=lambda: NOW,
    )
    svc.weather_at(*SLC, at(hour=10))
    assert fake_http.calls[0]["url"] == "https://proxy.service.example/v1/forecast"
    assert fake_http.calls[0]["headers"]["X-Proxy-Token"] == token
