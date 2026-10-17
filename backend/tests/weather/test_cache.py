import math
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from weather.cache import WeatherCache, cell_center, floor_hour, grid_cell
from weather.models import HourlySeries, HourValues

H0 = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)
V = HourValues(wind_mph=12.0, rain_in=0.0, showers_in=0.0, snow_in=0.0)


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ------------------------------------------------------------------ grid snapping


@pytest.mark.parametrize(
    ("lat", "lon", "cell"),
    [
        (40.7608, -111.8910, (408, -1119)),  # Salt Lake City
        (40.70, -111.90, (407, -1119)),
        (0.0, 0.0, (0, 0)),
        (-33.87, 151.21, (-339, 1512)),
        (90.0, 180.0, (900, 1800)),
        (-90.0, -180.0, (-900, -1800)),
    ],
)
def test_grid_cell_examples(lat, lon, cell):
    assert grid_cell(lat, lon) == cell


def test_points_within_a_cell_share_it_and_neighbours_do_not():
    # Cell (407, -1119) is centred on (40.7, -111.9) and spans +/- 0.05 degrees.
    centre = grid_cell(40.70, -111.90)
    assert grid_cell(40.66, -111.94) == centre
    assert grid_cell(40.74, -111.86) == centre
    assert grid_cell(40.76, -111.90) != centre  # next cell north
    assert grid_cell(40.70, -111.96) != centre  # next cell west


def test_halfway_points_round_up_deterministically():
    assert grid_cell(0.05, 0.0)[0] == 1
    assert grid_cell(0.15, 0.0)[0] == 2
    assert grid_cell(0.0, 0.05)[1] == 1


@pytest.mark.parametrize(
    ("lat", "lon"), [(math.nan, 0.0), (0.0, math.inf), (91.0, 0.0), (0.0, -180.5), (-90.01, 0.0)]
)
def test_grid_cell_rejects_bad_coordinates(lat, lon):
    with pytest.raises(ValueError):
        grid_cell(lat, lon)


def test_cell_center_round_trips():
    assert cell_center((408, -1119)) == (40.8, -111.9)
    assert cell_center((-339, 1512)) == (-33.9, 151.2)
    assert cell_center((0, 0)) == (0.0, 0.0)


@given(st.floats(-90, 90), st.floats(-180, 180))
def test_snapped_point_is_within_half_a_cell_and_in_range(lat, lon):
    clat, clon = cell_center(grid_cell(lat, lon))
    assert abs(clat - lat) <= 0.05 + 1e-9
    assert abs(clon - lon) <= 0.05 + 1e-9
    assert -90 <= clat <= 90
    assert -180 <= clon <= 180
    assert grid_cell(clat, clon) == grid_cell(lat, lon)  # centres map to their own cell


# ------------------------------------------------------------------ hours


def test_floor_hour_converts_to_utc_and_truncates():
    assert floor_hour(datetime(2026, 10, 17, 10, 59, 59, 999, tzinfo=UTC)) == H0
    six_west = timezone(timedelta(hours=-6))
    assert floor_hour(datetime(2026, 10, 17, 4, 15, tzinfo=six_west)) == H0


def test_floor_hour_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        floor_hour(datetime(2026, 10, 17, 10, 0))


# ------------------------------------------------------------------ hit and miss


def test_miss_then_hit():
    cache = WeatherCache()
    cell = grid_cell(40.7, -111.9)
    assert cache.get(cell, H0) is None
    cache.put(cell, H0, V)
    assert cache.get(cell, H0) == V
    assert cache.stats.hits == 1
    assert cache.stats.misses == 1
    assert cache.stats.hit_rate == 0.5


def test_key_is_cell_and_hour():
    cache = WeatherCache()
    a, b = grid_cell(40.7, -111.9), grid_cell(40.9, -111.9)
    cache.put(a, H0, V)
    assert cache.get(b, H0) is None  # other cell
    assert cache.get(a, H0 + timedelta(hours=1)) is None  # other hour
    assert cache.get(a, H0) == V


def test_nearby_points_hit_the_same_entry():
    cache = WeatherCache()
    cache.put(grid_cell(40.70, -111.90), H0, V)
    assert cache.get(grid_cell(40.73, -111.87), H0) == V


def test_hour_in_other_timezone_hits_same_entry():
    cache = WeatherCache()
    cell = grid_cell(40.7, -111.9)
    cache.put(cell, H0, V)
    six_west = timezone(timedelta(hours=-6))
    assert cache.get(cell, datetime(2026, 10, 17, 4, 0, tzinfo=six_west)) == V


def test_off_the_hour_key_is_rejected_not_silently_missed():
    cache = WeatherCache()
    cell = grid_cell(40.7, -111.9)
    with pytest.raises(ValueError):
        cache.get(cell, H0 + timedelta(minutes=30))
    with pytest.raises(ValueError):
        cache.put(cell, H0 + timedelta(minutes=30), V)


def test_put_series_stores_every_hour():
    cache = WeatherCache()
    cell = grid_cell(40.7, -111.9)
    hours = tuple(H0 + timedelta(hours=i) for i in range(3))
    values = tuple(HourValues(float(i), 0.0, 0.0, 0.0) for i in range(3))
    cache.put_series(cell, HourlySeries(hours, values))
    assert len(cache) == 3
    assert [cache.get(cell, h).wind_mph for h in hours] == [0.0, 1.0, 2.0]


def test_null_values_are_cached_as_nulls_not_zeros():
    cache = WeatherCache()
    cell = grid_cell(40.7, -111.9)
    cache.put(cell, H0, HourValues(None, 0.0, None, 0.0))
    got = cache.get(cell, H0)
    assert got.wind_mph is None
    assert got.showers_in is None
    assert got.rain_in == 0.0


# ------------------------------------------------------------------ ttl


def test_entry_expires_exactly_at_ttl():
    clock = FakeClock()
    cache = WeatherCache(ttl_seconds=600, clock=clock)
    cell = grid_cell(40.7, -111.9)
    cache.put(cell, H0, V)

    clock.advance(599.9)
    assert cache.get(cell, H0) == V  # still fresh
    clock.advance(0.1)  # age == ttl
    assert cache.get(cell, H0) is None  # expired
    assert len(cache) == 0  # and removed


def test_put_refreshes_the_ttl():
    clock = FakeClock()
    cache = WeatherCache(ttl_seconds=600, clock=clock)
    cell = grid_cell(40.7, -111.9)
    cache.put(cell, H0, V)
    clock.advance(500)
    cache.put(cell, H0, V)
    clock.advance(500)  # 1000 s after the first put, 500 s after the second
    assert cache.get(cell, H0) == V


def test_get_does_not_extend_the_ttl():
    clock = FakeClock()
    cache = WeatherCache(ttl_seconds=600, clock=clock)
    cell = grid_cell(40.7, -111.9)
    cache.put(cell, H0, V)
    clock.advance(400)
    assert cache.get(cell, H0) == V
    clock.advance(300)  # 700 s after the put
    assert cache.get(cell, H0) is None


def test_expired_entry_counts_as_a_miss():
    clock = FakeClock()
    cache = WeatherCache(ttl_seconds=10, clock=clock)
    cell = grid_cell(40.7, -111.9)
    cache.put(cell, H0, V)
    clock.advance(11)
    cache.get(cell, H0)
    assert (cache.stats.hits, cache.stats.misses) == (0, 1)


@pytest.mark.parametrize("ttl", [0, -1, math.nan, math.inf])
def test_bad_ttl_rejected(ttl):
    with pytest.raises(ValueError):
        WeatherCache(ttl_seconds=ttl)


# ------------------------------------------------------------------ size bound


def test_oldest_entries_are_evicted_beyond_max_entries():
    cache = WeatherCache(max_entries=3, clock=FakeClock())
    cell = grid_cell(40.7, -111.9)
    hours = [H0 + timedelta(hours=i) for i in range(5)]
    for h in hours:
        cache.put(cell, h, V)
    assert len(cache) == 3
    assert cache.get(cell, hours[0]) is None
    assert cache.get(cell, hours[1]) is None
    assert cache.get(cell, hours[4]) == V


def test_expired_entries_are_evicted_before_live_ones():
    clock = FakeClock()
    cache = WeatherCache(ttl_seconds=100, max_entries=2, clock=clock)
    cell = grid_cell(40.7, -111.9)
    cache.put(cell, H0, V)  # will expire
    clock.advance(60)
    cache.put(cell, H0 + timedelta(hours=1), V)
    clock.advance(60)  # first entry is now 120 s old, second is 60 s old
    cache.put(cell, H0 + timedelta(hours=2), V)  # over the limit: purge expired first
    assert len(cache) == 2
    assert cache.get(cell, H0 + timedelta(hours=1)) == V  # the live older entry survived


def test_bad_max_entries_rejected():
    with pytest.raises(ValueError):
        WeatherCache(max_entries=0)


def test_clear_empties_cache_and_stats():
    cache = WeatherCache()
    cell = grid_cell(40.7, -111.9)
    cache.put(cell, H0, V)
    cache.get(cell, H0)
    cache.clear()
    assert len(cache) == 0
    assert (cache.stats.hits, cache.stats.misses) == (0, 0)
