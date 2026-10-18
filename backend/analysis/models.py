"""Analysis output models. Pure Python, no Django imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from risk import RiskLevel


@dataclass(frozen=True)
class CheckpointResult:
    index: int
    lat: float
    lon: float
    dist_mi: float
    eta_utc: datetime
    wind_mph: float | None
    rain_in_hr: float | None
    snow_in_hr: float | None
    risk_level: RiskLevel | None
    reasons: tuple[str, ...]
    owned_mi: float
    weather_missing: bool
    missing_reason: str | None = None
    risk_level_minus_2h: RiskLevel | None = None
    risk_level_plus_2h: RiskLevel | None = None
    unstable: bool | None = None


@dataclass(frozen=True)
class RouteSummary:
    miles_by_level: dict[RiskLevel, float]
    max_level: RiskLevel | None
    average_risk: float | None
    duration_s: float
    eta_method: str
    missing_weather_count: int
    missing_weather_miles: float
    unstable_checkpoints: int = 0
    unknown_stability_checkpoints: int = 0

    @property
    def blocked(self) -> bool:
        """True if any assessed checkpoint is No Travel. Derived from checkpoint levels, not
        from miles, so a route whose No Travel checkpoint owns 0 miles is still blocked."""
        return self.max_level == RiskLevel.NO_TRAVEL

    @property
    def incomplete(self) -> bool:
        """True if weather is missing at one or more checkpoints."""
        return self.missing_weather_count > 0


@dataclass(frozen=True)
class RouteResult:
    checkpoints: tuple[CheckpointResult, ...]
    summary: RouteSummary
    origin_label: str


@dataclass(frozen=True)
class RouteRanking:
    ranked_results: tuple[RouteResult, ...]
    recommended: RouteResult
    reason: str

    @property
    def results(self) -> tuple[RouteResult, ...]:
        """Alias convenient for callers that expect the ordered result list."""
        return self.ranked_results
