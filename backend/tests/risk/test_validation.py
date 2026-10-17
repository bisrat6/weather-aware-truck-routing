"""Input validation: reject NaN, infinity and negative values; zero is valid."""

import math

import pytest

from risk import RiskLevel, Weather, assess_checkpoint

BAD_VALUES = [math.nan, math.inf, -math.inf, -1.0, -1e-9, -5e-324]
FIELDS = ["wind_mph", "rain_in_hr", "snow_in_hr", "load_lb"]


def _call(field: str, value: float):
    args = {"wind_mph": 0.0, "rain_in_hr": 0.0, "snow_in_hr": 0.0, "load_lb": 0.0}
    args[field] = value
    load = args.pop("load_lb")
    return assess_checkpoint(Weather(**args), load_lb=load)


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", BAD_VALUES, ids=repr)
def test_invalid_input_raises_value_error(field, value):
    with pytest.raises(ValueError):
        _call(field, value)


@pytest.mark.parametrize("field", FIELDS)
def test_zero_is_valid(field):
    assert _call(field, 0.0).level == RiskLevel.LOW


def test_all_zero_is_low_with_no_reasons():
    result = assess_checkpoint(Weather(0.0, 0.0, 0.0), load_lb=0.0)
    assert result.level == RiskLevel.LOW
    assert result.reasons == ()


def test_integer_inputs_are_accepted():
    result = assess_checkpoint(Weather(50, 0, 0), load_lb=34_000)  # type: ignore[arg-type]
    assert result.level == RiskLevel.NO_TRAVEL
