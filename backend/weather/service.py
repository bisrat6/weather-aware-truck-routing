"""Weather lookups for checkpoints: grid cache in front of the Open-Meteo client.

Pure Python, no Django imports. One service instance (and so one cache) should be shared
by every request in the process.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from weather.cache import Cell, WeatherCache, cell_center, grid_cell
from weather.client import (
    DEFAULT_TIMEOUT,
    MAX_FORECAST_DAYS,
    OPEN_METEO_BASE_URL,
    fetch_hourly,
)
from weather.errors import MissingWeatherDataError
from weather.models import HourValues, MissingWeather, WeatherSample
from weather.sampling import sample_hour, select_hour

Query = tuple[float, float, datetime]  # (lat, lon, ETA as a timezone-aware datetime)
Result = WeatherSample | MissingWeather


def _utc_now() -> datetime:
    return datetime.now(UTC)


class WeatherService:
    """Look up weather at (lat, lon, ETA) triples, fetching only what is not cached.

    Coordinates are snapped to a 0.1 degree grid; Open-Meteo is asked for the cell centre,
    so every point in a cell gets the same data and shares one cache entry per hour.
    """

    def __init__(
        self,
        cache: WeatherCache | None = None,
        *,
        timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
        base_url: str = OPEN_METEO_BASE_URL,
        proxy_token: str | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.cache = cache if cache is not None else WeatherCache()
        self._timeout = timeout
        self._base_url = base_url
        self._proxy_token = proxy_token
        self._now = now

    def weather_at_many(self, queries: Sequence[Query]) -> list[Result]:
        """Weather for each (lat, lon, eta), in order.

        Missing data is flagged, never zeroed: an entry is a `MissingWeather` when a value
        is null, or when the chosen hour is before today 00:00 UTC or beyond the 16-day
        forecast window (no request is made for those). All uncached cells are fetched in
        a single request. Network and API failures raise a WeatherError and nothing is
        cached for the failed request.
        """
        today = self._now().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = today + timedelta(days=MAX_FORECAST_DAYS)

        results: list[Result | None] = [None] * len(queries)
        plan: dict[int, tuple[Cell, datetime]] = {}
        cached: dict[int, HourValues] = {}
        latest_needed: dict[Cell, datetime] = {}  # cells to fetch -> last hour required

        for index, (lat, lon, eta) in enumerate(queries):
            cell = grid_cell(lat, lon)
            hour = select_hour(eta)
            if not today <= hour < window_end:
                results[index] = MissingWeather(
                    hour,
                    f"outside the forecast window ({today:%Y-%m-%d} to +{MAX_FORECAST_DAYS} days)",
                )
                continue
            plan[index] = (cell, hour)
            values = self.cache.get(cell, hour)
            if values is not None:
                cached[index] = values
            else:
                latest_needed[cell] = max(latest_needed.get(cell, hour), hour)

        fetched: dict[Cell, dict[datetime, HourValues]] = {}
        if latest_needed:
            cells = list(latest_needed)
            days = max((hour - today).days for hour in latest_needed.values()) + 1
            series = fetch_hourly(
                [cell_center(c) for c in cells],
                days,
                timeout=self._timeout,
                base_url=self._base_url,
                proxy_token=self._proxy_token,
            )
            for cell, one in zip(cells, series, strict=True):
                self.cache.put_series(cell, one)
                fetched[cell] = dict(one.items())

        for index, (cell, hour) in plan.items():
            values = cached.get(index)
            if values is None:
                values = fetched[cell].get(hour)
            if values is None:
                results[index] = MissingWeather(hour, "hour not present in the forecast response")
            else:
                results[index] = sample_hour(hour, values)

        final = [r for r in results if r is not None]
        if len(final) != len(queries):  # pragma: no cover - would be a bug in this method
            raise RuntimeError("internal error: a weather query was left unresolved")
        return final

    def weather_at(self, lat: float, lon: float, eta: datetime) -> WeatherSample:
        """Weather for one checkpoint; raises MissingWeatherDataError instead of flagging."""
        (result,) = self.weather_at_many([(lat, lon, eta)])
        if isinstance(result, MissingWeather):
            raise MissingWeatherDataError(result)
        return result
