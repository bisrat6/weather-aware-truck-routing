"""Runs every Open-Meteo fixture through the parser.

Drop real responses into tests/fixtures/ with scripts/record_weather_fixture.py and they are
picked up here automatically.
"""

import json
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from weather.client import parse_payload

FIXTURES = sorted((Path(__file__).parent.parent / "fixtures").glob("open_meteo_*.json"))


def test_at_least_one_fixture_exists():
    assert FIXTURES


@pytest.fixture(params=FIXTURES, ids=lambda p: p.stem)
def series(request):
    payload = json.loads(request.param.read_text(encoding="utf-8"))
    n = len(payload) if isinstance(payload, list) else 1
    return parse_payload(payload, n)


def test_fixture_parses_with_verified_units(series):
    # parse_payload raises if units are not mph/inch or the response is not UTC.
    assert series


def test_fixture_hours_are_utc_hourly_and_start_at_midnight(series):
    for s in series:
        assert len(s.times) >= 24
        assert s.times[0].hour == 0
        assert all(b - a == timedelta(hours=1) for a, b in pairwise(s.times))
        assert all(t.utcoffset() == timedelta(0) for t in s.times)


def test_every_variable_has_real_values(series):
    """Guards the variable names: a misspelt or unsupported one would come back all null."""
    for s in series:
        for field in ("wind_mph", "rain_in", "showers_in", "snow_in"):
            assert any(getattr(v, field) is not None for v in s.values), field


def test_values_are_plausible_for_the_units(series):
    for s in series:
        for v in s.values:
            if v.wind_mph is not None:
                assert (
                    0 <= v.wind_mph < 200
                )  # mph; km/h data would not trip this, units are checked above
            for amount in (v.rain_in, v.showers_in, v.snow_in):
                if amount is not None:
                    assert 0 <= amount < 20  # inches per hour
