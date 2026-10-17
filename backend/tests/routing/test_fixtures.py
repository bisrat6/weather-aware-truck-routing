"""Runs every recorded/hand-built Mapbox fixture through parsing, checks and checkpoints.

Drop real responses into tests/fixtures/ with scripts/record_fixture.py and they are
picked up here automatically.
"""

import json
import math
from itertools import pairwise
from pathlib import Path

import pytest

from routing.checkpoints import METERS_PER_MILE, sample_checkpoints
from routing.geodesy import check_route_distance
from routing.mapbox import parse_routes

FIXTURES = sorted((Path(__file__).parent.parent / "fixtures").glob("mapbox_*.json"))


def test_at_least_one_fixture_exists():
    assert FIXTURES


@pytest.fixture(params=FIXTURES, ids=lambda p: p.stem)
def routes(request):
    payload = json.loads(request.param.read_text(encoding="utf-8"))
    assert payload["code"] == "Ok"
    return parse_routes(payload)


def test_fixture_routes_are_well_formed(routes):
    assert 1 <= len(routes) <= 3
    for r in routes:
        assert len(r.coords) == len(r.cum_dist_m) == len(r.cum_dur_s)
        assert all(b >= a for a, b in pairwise(r.cum_dist_m))
        assert all(b >= a for a, b in pairwise(r.cum_dur_s))
        assert r.origin_label == "provider alternative"


def test_fixture_annotations_are_usable(routes):
    # Real Mapbox responses should satisfy annotation count == coordinates - 1.
    # If a recorded fixture fails this, the proportional fallback is firing in production.
    assert all(r.eta_method == "annotations" for r in routes)


def test_fixture_geometry_agrees_with_provider_distance(routes):
    for r in routes:
        check = check_route_distance(r, tolerance=0.05)
        assert check.ok, f"geodesic {check.geodesic_m:.0f} m vs provider {check.provider_m:.0f} m"


@pytest.mark.parametrize("interval", [10, 25, 50])
def test_fixture_checkpoints(routes, interval):
    for r in routes:
        cps = sample_checkpoints(r, interval)
        assert len(cps) <= 120
        assert (cps[-1].lat, cps[-1].lon) == r.coords[-1]
        total_mi = r.cum_dist_m[-1] / METERS_PER_MILE
        assert math.fsum(c.owned_mi for c in cps) == pytest.approx(total_mi, rel=1e-9)
        assert all(b.eta_offset_s >= a.eta_offset_s for a, b in pairwise(cps))
