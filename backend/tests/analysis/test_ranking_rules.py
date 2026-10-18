"""Ranking rules from PROJECT_GUIDE.md section 5, plus the documented missing-data rules."""

import json
from datetime import UTC, datetime
from pathlib import Path

from analysis import RouteResult, RouteSummary, analyze_route, rank_routes
from risk import RiskLevel
from routing.mapbox import parse_routes
from routing.models import Route
from weather.models import WeatherSample

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 10, 17, tzinfo=UTC)


def summary_result(
    label,
    miles=None,
    *,
    max_level=None,
    avg=1.0,
    duration=1000,
    missing_count=0,
    missing_miles=0.0,
):
    """A RouteResult with a hand-built summary (no checkpoints needed for ranking)."""
    by_level = {level: 0.0 for level in RiskLevel}
    by_level.update(miles or {})
    if max_level is None:
        max_level = max((level for level, mi in by_level.items() if mi > 0), default=None)
    summary = RouteSummary(
        by_level, max_level, avg, duration, "annotations", missing_count, missing_miles
    )
    return RouteResult((), summary, label)


class Samples:
    """Weather stub returning a fixed list (3 entries per checkpoint: -2h, base, +2h)."""

    def __init__(self, values):
        self.values = values

    def weather_at_many(self, queries):
        assert len(self.values) == len(queries)
        return self.values


def constant_weather(route, interval, wind):
    from routing.checkpoints import sample_checkpoints

    n = len(sample_checkpoints(route, interval))
    return Samples([WeatherSample(NOW, wind, 0.0, 0.0)] * (n * 3))


def slc_provo():
    payload = json.loads((FIXTURES / "mapbox_slc_provo.json").read_text(encoding="utf-8"))
    return parse_routes(payload)[0]


def zero_length_route():
    """Origin and destination at the same point: 0 miles, but still two checkpoints."""
    return Route(
        coords=[(40.0, -100.0), (40.0, -100.0)],
        cum_dist_m=[0.0, 0.0],
        cum_dur_s=[0.0, 60.0],
        distance_m=0.0,
        duration_s=60.0,
        eta_method="annotations",
        origin_label="zero-length",
    )


# ------------------------------------------------------------------ blocked = No Travel checkpoint


def test_zero_length_route_with_a_no_travel_checkpoint_ranks_last():
    # 70 mph is No Travel at both checkpoints, yet the route owns 0 miles, so a miles-based
    # "blocked" test would call it unblocked and rank it first (0 Severe miles).
    blocked = analyze_route(
        zero_length_route(), NOW, 25, 0, constant_weather(zero_length_route(), 25, 70)
    )
    assert {cp.risk_level for cp in blocked.checkpoints} == {RiskLevel.NO_TRAVEL}
    assert blocked.summary.miles_by_level[RiskLevel.NO_TRAVEL] == 0.0

    route = slc_provo()
    severe = analyze_route(route, NOW, 25, 0, constant_weather(route, 25, 50))  # Severe everywhere
    assert severe.summary.miles_by_level[RiskLevel.SEVERE] > 40

    ranking = rank_routes([blocked, severe])
    assert [r.origin_label for r in ranking.ranked_results] == [
        severe.origin_label,
        blocked.origin_label,
    ]
    assert ranking.recommended is severe


def test_blocked_follows_the_highest_checkpoint_level_not_the_no_travel_miles():
    no_travel_level_but_no_miles = summary_result(
        "no-travel-checkpoint", {RiskLevel.LOW: 100}, max_level=RiskLevel.NO_TRAVEL, avg=0.0
    )
    severe = summary_result("severe", {RiskLevel.SEVERE: 30, RiskLevel.LOW: 70}, avg=1.0)
    assert rank_routes([no_travel_level_but_no_miles, severe]).recommended is severe


def test_no_travel_bucket_marks_route_blocked_even_when_severe_miles_dominate():
    severe = summary_result("severe", {RiskLevel.SEVERE: 100}, avg=3.0)
    blocked = summary_result("blocked", {RiskLevel.NO_TRAVEL: 1, RiskLevel.LOW: 99}, avg=0.04)
    assert rank_routes([blocked, severe]).recommended is severe


def test_blocked_route_ranks_after_incomplete_route():
    blocked = summary_result(
        "blocked complete",
        {RiskLevel.NO_TRAVEL: 1, RiskLevel.LOW: 99},
        missing_count=0,
    )
    incomplete = summary_result(
        "incomplete but unblocked",
        {RiskLevel.SEVERE: 20, RiskLevel.LOW: 80},
        missing_count=1,
        missing_miles=5,
    )
    assert [route.origin_label for route in rank_routes([blocked, incomplete]).ranked_results] == [
        "incomplete but unblocked",
        "blocked complete",
    ]


def test_blocked_routes_round_no_travel_miles_before_severe_miles():
    more_exact_no_travel = summary_result(
        "severe winner",
        {RiskLevel.NO_TRAVEL: 1.4, RiskLevel.SEVERE: 2, RiskLevel.LOW: 90},
    )
    fewer_exact_no_travel = summary_result(
        "severe loser",
        {RiskLevel.NO_TRAVEL: 1.1, RiskLevel.SEVERE: 3, RiskLevel.LOW: 90},
    )
    assert rank_routes([fewer_exact_no_travel, more_exact_no_travel]).recommended is (
        more_exact_no_travel
    )


def test_duration_ranks_by_whole_minutes_and_keeps_input_order_when_rounded_equal():
    fractional_minute_later = summary_result("first", {RiskLevel.LOW: 1}, duration=1201)
    exact_minute = summary_result("second", {RiskLevel.LOW: 1}, duration=1200)
    assert rank_routes([fractional_minute_later, exact_minute]).ranked_results == (
        fractional_minute_later,
        exact_minute,
    )


def test_reason_names_severe_criterion_and_appends_duration_difference():
    winner = summary_result(
        "severe winner", {RiskLevel.SEVERE: 2, RiskLevel.LOW: 98}, avg=1.0, duration=1800
    )
    other = summary_result(
        "longer risk route", {RiskLevel.SEVERE: 8, RiskLevel.LOW: 92}, avg=1.0, duration=720
    )
    reason = rank_routes([other, winner]).reason
    assert reason.startswith("6 fewer Severe miles, 18 min longer")
    assert "tie" not in reason.lower()


def test_reason_names_severe_when_no_travel_miles_rounded_equal():
    winner = summary_result(
        "severe winner",
        {RiskLevel.NO_TRAVEL: 1.4, RiskLevel.SEVERE: 2, RiskLevel.LOW: 90},
    )
    other = summary_result(
        "severe loser",
        {RiskLevel.NO_TRAVEL: 1.1, RiskLevel.SEVERE: 3, RiskLevel.LOW: 90},
    )
    reason = rank_routes([other, winner]).reason
    assert "1 fewer Severe miles" in reason
    assert "No Travel miles" not in reason


def test_reason_names_whole_minute_duration_when_duration_decides():
    faster = summary_result("faster", {RiskLevel.LOW: 1}, duration=1200)
    slower = summary_result("slower", {RiskLevel.LOW: 1}, duration=1260)
    reason = rank_routes([slower, faster]).reason
    assert reason.startswith("1 min shorter")
    assert "tie" not in reason.lower()
