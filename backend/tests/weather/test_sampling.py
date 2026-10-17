from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from weather.models import HourValues, MissingWeather, WeatherSample
from weather.sampling import sample_hour, select_hour

HOUR = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


# ------------------------------------------------------------------ ETA to hour


@pytest.mark.parametrize(
    ("eta", "expected"),
    [
        (utc(2026, 10, 17, 10, 0, 0), utc(2026, 10, 17, 10)),  # on the hour
        (utc(2026, 10, 17, 10, 0, 1), utc(2026, 10, 17, 10)),
        (utc(2026, 10, 17, 10, 29, 59), utc(2026, 10, 17, 10)),  # just under half
        (utc(2026, 10, 17, 10, 30, 0), utc(2026, 10, 17, 11)),  # exactly half rounds up
        (utc(2026, 10, 17, 10, 30, 1), utc(2026, 10, 17, 11)),
        (utc(2026, 10, 17, 10, 59, 59), utc(2026, 10, 17, 11)),
        (utc(2026, 10, 17, 23, 45), utc(2026, 10, 18, 0)),  # rolls over midnight
        (utc(2026, 12, 31, 23, 31), utc(2027, 1, 1, 0)),  # rolls over the year
        (datetime(2026, 10, 17, 10, 29, 59, 999999, tzinfo=UTC), utc(2026, 10, 17, 10)),
    ],
)
def test_nearest_hour(eta, expected):
    assert select_hour(eta) == expected


def test_non_utc_eta_is_converted_before_rounding():
    six_west = timezone(timedelta(hours=-6))
    # 04:10 at UTC-6 is 10:10 UTC -> 10:00 UTC; 04:40 at UTC-6 is 10:40 UTC -> 11:00 UTC.
    assert select_hour(datetime(2026, 10, 17, 4, 10, tzinfo=six_west)) == utc(2026, 10, 17, 10)
    assert select_hour(datetime(2026, 10, 17, 4, 40, tzinfo=six_west)) == utc(2026, 10, 17, 11)


def test_half_hour_offset_timezone():
    india = timezone(timedelta(hours=5, minutes=30))
    # 15:40 at UTC+5:30 is 10:10 UTC.
    assert select_hour(datetime(2026, 10, 17, 15, 40, tzinfo=india)) == utc(2026, 10, 17, 10)


def test_naive_eta_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        select_hour(datetime(2026, 10, 17, 10, 0))


def test_result_is_utc_and_on_the_hour():
    result = select_hour(datetime(2026, 10, 17, 4, 40, tzinfo=timezone(timedelta(hours=-6))))
    assert result.utcoffset() == timedelta(0)
    assert (result.minute, result.second, result.microsecond) == (0, 0, 0)


_etas = st.datetimes(
    min_value=datetime(2026, 1, 1), max_value=datetime(2027, 12, 31), timezones=st.just(UTC)
)


@given(_etas)
def test_selected_hour_is_never_more_than_half_an_hour_away(eta):
    chosen = select_hour(eta)
    assert chosen.minute == chosen.second == chosen.microsecond == 0
    assert abs(chosen - eta) <= timedelta(minutes=30)


@given(_etas, st.integers(min_value=0, max_value=100_000))
def test_selection_never_goes_backwards_as_eta_increases(eta, extra_seconds):
    assert select_hour(eta) <= select_hour(eta + timedelta(seconds=extra_seconds))


@given(_etas)
def test_an_eta_on_the_hour_selects_itself(eta):
    exact = eta.replace(minute=0, second=0, microsecond=0)
    assert select_hour(exact) == exact


# ------------------------------------------------------------------ values to sample


def values(wind=10.0, rain=0.0, showers=0.0, snow=0.0):
    return HourValues(wind_mph=wind, rain_in=rain, showers_in=showers, snow_in=snow)


def test_sample_maps_values_for_the_risk_engine():
    sample = sample_hour(HOUR, values(wind=45.5, rain=0.2, showers=0.1, snow=1.5))
    assert isinstance(sample, WeatherSample)
    assert sample.hour == HOUR
    assert sample.wind_mph == 45.5  # mph straight through, no conversion
    assert sample.rain_in_hr == pytest.approx(0.3)  # rain + showers
    assert sample.snow_in_hr == 1.5  # snowfall inches straight through


def test_rain_is_rain_plus_showers_not_either_alone():
    assert sample_hour(HOUR, values(rain=0.2, showers=0.0)).rain_in_hr == 0.2
    assert sample_hour(HOUR, values(rain=0.0, showers=0.2)).rain_in_hr == 0.2
    assert sample_hour(HOUR, values(rain=0.2, showers=0.2)).rain_in_hr == 0.4


def test_float_noise_in_the_rain_sum_cannot_drop_a_value_below_a_band_edge():
    # 0.01 + 0.09 is 0.09999999999999999 in floating point: just under the 0.10 edge.
    assert 0.01 + 0.09 < 0.10
    assert sample_hour(HOUR, values(rain=0.01, showers=0.09)).rain_in_hr == 0.10
    assert sample_hour(HOUR, values(rain=0.09, showers=0.01)).rain_in_hr == 0.10


@pytest.mark.parametrize(
    ("rain", "showers", "expected"),
    [(0.1, 0.15, 0.25), (0.25, 0.25, 0.50), (0.5, 0.5, 1.0), (0.0, 0.0, 0.0)],
)
def test_rain_sums_land_exactly_on_band_edges(rain, showers, expected):
    assert sample_hour(HOUR, values(rain=rain, showers=showers)).rain_in_hr == expected


@pytest.mark.parametrize(
    ("kwargs", "names"),
    [
        ({"wind": None}, ("wind_speed_10m",)),
        ({"rain": None}, ("rain",)),
        ({"showers": None}, ("showers",)),
        ({"snow": None}, ("snowfall",)),
        ({"wind": None, "snow": None}, ("wind_speed_10m", "snowfall")),
        (
            {"wind": None, "rain": None, "showers": None, "snow": None},
            ("wind_speed_10m", "rain", "showers", "snowfall"),
        ),
    ],
)
def test_any_null_is_flagged_with_its_variable_name(kwargs, names):
    result = sample_hour(HOUR, values(**kwargs))
    assert isinstance(result, MissingWeather)
    assert result.variables == names
    assert result.hour == HOUR
    for name in names:
        assert name in str(result)


def test_null_showers_does_not_fall_back_to_rain_alone():
    result = sample_hour(HOUR, values(rain=0.4, showers=None))
    assert isinstance(result, MissingWeather)  # not a sample with rain 0.4, not one with 0


def test_real_zeros_are_not_mistaken_for_missing():
    sample = sample_hour(HOUR, values(wind=0.0, rain=0.0, showers=0.0, snow=0.0))
    assert isinstance(sample, WeatherSample)
    assert (sample.wind_mph, sample.rain_in_hr, sample.snow_in_hr) == (0.0, 0.0, 0.0)


_amounts = st.floats(0, 10, allow_nan=False)


@given(_amounts, _amounts, _amounts, _amounts)
def test_complete_values_always_give_a_sample_with_consistent_numbers(wind, rain, showers, snow):
    sample = sample_hour(HOUR, values(wind, rain, showers, snow))
    assert isinstance(sample, WeatherSample)
    assert sample.wind_mph == wind
    assert sample.snow_in_hr == snow
    assert sample.rain_in_hr == pytest.approx(rain + showers, abs=1e-6)
    assert sample.rain_in_hr >= 0


@given(st.sets(st.sampled_from(["wind", "rain", "showers", "snow"]), min_size=1))
def test_any_nonempty_set_of_nulls_is_flagged_never_a_sample(null_fields):
    kwargs = {name: None for name in null_fields}
    assert isinstance(sample_hour(HOUR, values(**kwargs)), MissingWeather)
