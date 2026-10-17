"""Boundary tests for every threshold in the brief's risk table.

Each case: (factor, edge, level just below, level at the edge, level just above).
The edge value and both adjacent floats (math.nextafter) are tested.
Load is 0 so only the band tables are exercised.
"""

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

# (factor, edge, below, at, above)
EDGES = [
    # Wind: each edge belongs to the higher band (half-open).
    ("wind", 25.0, L, M, M),
    ("wind", 35.0, M, H, H),
    ("wind", 45.0, H, S, S),
    ("wind", 55.0, S, N, N),
    # Rain: 0.25/0.50 belong to the higher band; 1.00 is inclusive Severe.
    ("rain", 0.10, L, M, M),
    ("rain", 0.25, M, H, H),
    ("rain", 0.50, H, S, S),
    ("rain", 1.00, S, S, N),
    # Snow: 1.0/2.0 belong to the higher band; 3.0 is inclusive Severe.
    ("snow", 0.5, L, M, M),
    ("snow", 1.0, M, H, H),
    ("snow", 2.0, H, S, S),
    ("snow", 3.0, S, S, N),
]


def _weather(factor: str, value: float) -> Weather:
    kwargs = {"wind_mph": 0.0, "rain_in_hr": 0.0, "snow_in_hr": 0.0}
    kwargs[f"{factor}_mph" if factor == "wind" else f"{factor}_in_hr"] = value
    return Weather(**kwargs)


def _level(factor: str, value: float) -> RiskLevel:
    result = assess_checkpoint(_weather(factor, value), load_lb=0.0)
    return {
        "wind": result.wind_level,
        "rain": result.rain_level,
        "snow": result.snow_level,
    }[factor]


def _ids(cases):
    return [f"{c[0]}@{c[1]}" for c in cases]


@pytest.mark.parametrize(("factor", "edge", "below", "at", "above"), EDGES, ids=_ids(EDGES))
def test_edge_and_adjacent_floats(factor, edge, below, at, above):
    just_below = math.nextafter(edge, -math.inf)
    just_above = math.nextafter(edge, math.inf)
    assert _level(factor, just_below) == below
    assert _level(factor, edge) == at
    assert _level(factor, just_above) == above


@pytest.mark.parametrize(
    ("factor", "value", "expected"),
    [
        ("wind", 0.0, L),
        ("wind", 30.0, M),
        ("wind", 40.0, H),
        ("wind", 50.0, S),
        ("wind", 120.0, N),
        ("rain", 0.0, L),
        ("rain", 0.2, M),
        ("rain", 0.4, H),
        ("rain", 0.75, S),
        ("rain", 5.0, N),
        ("snow", 0.0, L),
        ("snow", 0.75, M),
        ("snow", 1.5, H),
        ("snow", 2.5, S),
        ("snow", 10.0, N),
    ],
)
def test_band_interiors(factor, value, expected):
    assert _level(factor, value) == expected


@pytest.mark.parametrize("wind", [34.5, 44.5, 54.5])
def test_fractional_wind_in_integer_range_gaps(wind):
    """The brief's ranges (25-34, 35-44, 45-54) leave gaps; fractions are not Low."""
    assert _level("wind", wind) >= M
