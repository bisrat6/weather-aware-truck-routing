"""Property tests. They state invariants of the brief, not an alternative implementation."""

import math

from hypothesis import given
from hypothesis import strategies as st

from risk import RiskLevel, Weather, assess_checkpoint

# Interesting values from the brief, plus their neighbouring floats, so that
# hypothesis lands on boundaries often.
_EDGES = [25, 35, 45, 55, 0.10, 0.25, 0.50, 1.00, 0.5, 1.0, 2.0, 3.0, 30_000, 40_000]
_NEAR_EDGES = [
    f(float(e), d) for e in _EDGES for f in (math.nextafter,) for d in (-math.inf, math.inf)
] + [float(e) for e in _EDGES]


def _quantity(max_value: float):
    return st.one_of(
        st.floats(min_value=0.0, max_value=max_value, allow_nan=False, allow_infinity=False),
        st.sampled_from([v for v in _NEAR_EDGES if v <= max_value]),
    )


winds = _quantity(120.0)
rains = _quantity(5.0)
snows = _quantity(10.0)
loads = _quantity(100_000.0)

LABELS = {
    RiskLevel.MODERATE: "Moderate",
    RiskLevel.HIGH: "High",
    RiskLevel.SEVERE: "Severe",
    RiskLevel.NO_TRAVEL: "No Travel",
}


@given(winds, rains, snows, loads)
def test_a_valid_level_equal_to_max_of_parts(wind, rain, snow, load):
    r = assess_checkpoint(Weather(wind, rain, snow), load)
    assert isinstance(r.level, RiskLevel)
    for part in (r.wind_level, r.rain_level, r.snow_level):
        assert isinstance(part, RiskLevel)
    assert r.level == max(r.wind_level, r.rain_level, r.snow_level)


@given(winds, winds, rains, snows, loads)
def test_b_monotonic_in_wind(w1, w2, rain, snow, load):
    lo, hi = sorted((w1, w2))
    a = assess_checkpoint(Weather(lo, rain, snow), load)
    b = assess_checkpoint(Weather(hi, rain, snow), load)
    assert a.level <= b.level


@given(winds, rains, rains, snows, loads)
def test_b_monotonic_in_rain(wind, r1, r2, snow, load):
    lo, hi = sorted((r1, r2))
    a = assess_checkpoint(Weather(wind, lo, snow), load)
    b = assess_checkpoint(Weather(wind, hi, snow), load)
    assert a.level <= b.level


@given(winds, rains, snows, snows, loads)
def test_b_monotonic_in_snow(wind, rain, s1, s2, load):
    lo, hi = sorted((s1, s2))
    a = assess_checkpoint(Weather(wind, rain, lo), load)
    b = assess_checkpoint(Weather(wind, rain, hi), load)
    assert a.level <= b.level


@given(winds, rains, snows, loads, loads)
def test_c_monotonic_in_load(wind, rain, snow, l1, l2):
    lo, hi = sorted((l1, l2))
    a = assess_checkpoint(Weather(wind, rain, snow), lo)
    b = assess_checkpoint(Weather(wind, rain, snow), hi)
    assert a.level <= b.level


@given(winds, rains, snows, loads)
def test_d_load_never_lowers_level_versus_empty_truck(wind, rain, snow, load):
    weather = Weather(wind, rain, snow)
    assert assess_checkpoint(weather, load).level >= assess_checkpoint(weather, 0.0).level


@given(winds, rains, snows, loads)
def test_e_non_low_has_reason_naming_driving_factor(wind, rain, snow, load):
    r = assess_checkpoint(Weather(wind, rain, snow), load)
    if r.level == RiskLevel.LOW:
        return
    assert r.reasons
    driving = [
        name
        for name, part in (("wind", r.wind_level), ("rain", r.rain_level), ("snow", r.snow_level))
        if part == r.level
    ]
    assert driving
    label = LABELS[r.level]
    assert any(
        any(factor in reason.lower() and label in reason for reason in r.reasons)
        for factor in driving
    )


@given(winds, rains, snows, loads)
def test_every_factor_above_low_is_explained(wind, rain, snow, load):
    r = assess_checkpoint(Weather(wind, rain, snow), load)
    for name, part in (("wind", r.wind_level), ("rain", r.rain_level), ("snow", r.snow_level)):
        if part > RiskLevel.LOW:
            assert any(name in reason.lower() for reason in r.reasons)
