"""Load-rule tests. Load comparisons are strict (>)."""

import math

import pytest

from risk import RiskLevel, Weather, assess_checkpoint

L, M, H, S, N = (
    RiskLevel.LOW,
    RiskLevel.MODERATE,
    RiskLevel.HIGH,
    RiskLevel.SEVERE,
    RiskLevel.NO_TRAVEL,
)

CALM = {"rain_in_hr": 0.0, "snow_in_hr": 0.0}


def _risk(wind: float, load: float, **extra: float):
    return assess_checkpoint(Weather(wind_mph=wind, **{**CALM, **extra}), load_lb=load)


# Wind speeds inside 45 <= wind < 55
SEVERE_WINDS = [45.0, 50.0, math.nextafter(55.0, -math.inf)]
# Wind speeds inside 35 <= wind < 45
HIGH_WINDS = [35.0, 40.0, math.nextafter(45.0, -math.inf)]


@pytest.mark.parametrize("wind", SEVERE_WINDS)
@pytest.mark.parametrize(
    ("load", "expected"),
    [
        (0.0, S),
        (30_000.0, S),  # not strictly greater than 30,000
        (30_000.01, N),
        (30_001.0, N),
        (200_000.0, N),
    ],
)
def test_severe_wind_with_load(wind, load, expected):
    assert _risk(wind, load).level == expected


@pytest.mark.parametrize("wind", HIGH_WINDS)
@pytest.mark.parametrize(
    ("load", "expected"),
    [
        (0.0, H),
        (30_000.01, H),  # the 30,000 rule only applies at 45+ mph
        (40_000.0, H),  # not strictly greater than 40,000
        (40_000.01, S),
        (40_001.0, S),
        (10_000_000.0, S),  # heavy load escalates High to Severe, never No Travel
    ],
)
def test_high_wind_with_load(wind, load, expected):
    assert _risk(wind, load).level == expected


@pytest.mark.parametrize("load", [0.0, 30_000.01, 40_000.01, 1_000_000.0])
def test_wind_55_and_up_is_no_travel_for_any_load(load):
    assert _risk(55.0, load).level == N
    assert _risk(80.0, load).level == N


@pytest.mark.parametrize("wind", [0.0, 24.9, 25.0, 34.9, math.nextafter(35.0, -math.inf)])
def test_load_rules_do_not_apply_below_35_mph(wind):
    heavy = _risk(wind, 500_000.0)
    light = _risk(wind, 0.0)
    assert heavy.level == light.level


def test_boundary_between_load_rules_at_45_mph():
    just_below = math.nextafter(45.0, -math.inf)
    # 40,000.01 lb: heavy enough for the 40k rule, and also for the 30k rule at 45+.
    assert _risk(just_below, 40_000.01).level == S
    assert _risk(45.0, 40_000.01).level == N


def test_load_rules_never_lower_other_factors():
    # Rain at No Travel stays No Travel even though the load rule only asks for Severe.
    assert _risk(40.0, 50_000.0, rain_in_hr=1.5).level == N
    assert _risk(40.0, 50_000.0, snow_in_hr=3.5).level == N
    # Rain Severe, wind High: escalated wind is Severe; result stays Severe.
    assert _risk(40.0, 50_000.0, rain_in_hr=0.75).level == S


def test_load_escalation_raises_level_above_other_factors():
    # Moderate rain + High wind + heavy load -> Severe, from the load rule alone.
    result = _risk(40.0, 50_000.0, rain_in_hr=0.15)
    assert result.level == S
    assert result.rain_level == M


def test_all_conditions_are_evaluated_together():
    # Wind drives No Travel via load, while rain and snow are independently Severe.
    result = _risk(50.0, 34_000.0, rain_in_hr=0.75, snow_in_hr=2.5)
    assert result.level == N
    assert result.rain_level == S
    assert result.snow_level == S


def test_load_escalation_reason_names_trigger():
    result = _risk(45.0, 34_000.0)
    assert result.level == N
    matching = [r for r in result.reasons if "load" in r.lower()]
    assert matching, result.reasons
    text = matching[0]
    assert "wind" in text.lower()
    assert "45.0" in text
    assert "34,000" in text
    assert "No Travel" in text


def test_high_wind_load_escalation_reason_names_trigger():
    result = _risk(40.0, 41_000.0)
    assert result.level == S
    matching = [r for r in result.reasons if "load" in r.lower()]
    assert matching, result.reasons
    assert "41,000" in matching[0]
    assert "Severe" in matching[0]


def test_no_load_reason_when_load_rule_does_not_fire():
    result = _risk(45.0, 30_000.0)
    assert all("load" not in r.lower() for r in result.reasons)
