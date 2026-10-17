"""Corridor sampling and load-aware hourly risk grid construction."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from pyproj import Geod

from risk import Weather, assess_checkpoint
from weather.cache import grid_cell
from weather.models import MissingWeather
from weather.sampling import select_hour
from weather.service import WeatherService

POINT_SPACING_M = 10 * 1609.344
MAX_HEATMAP_POINTS = 150
HOURS = 49
_GEOD = Geod(ellps="WGS84")


def sample_corridor(geometry: list[list[float]]) -> list[tuple[float, float]]:
    """Sample a lat/lon polyline every 10 miles, grid-dedupe, and cap at 150 points."""
    coords = [(float(lat), float(lon)) for lat, lon in geometry]
    segments: list[tuple[float, float, float, float, float]] = []
    cumulative = [0.0]
    for (lat1, lon1), (lat2, lon2) in pairwise(coords):
        azimuth, _, distance = _GEOD.inv(lon1, lat1, lon2, lat2)
        segments.append((lat1, lon1, azimuth, distance, cumulative[-1]))
        cumulative.append(cumulative[-1] + distance)
    total = cumulative[-1]
    if total <= 0:
        return [coords[0]]

    targets = [i * POINT_SPACING_M for i in range(math.floor(total / POINT_SPACING_M) + 1)]
    if not targets or total - targets[-1] > 1e-6:
        targets.append(total)
    points: list[tuple[float, float]] = []
    segment_index = 0
    for target in targets:
        while segment_index < len(segments) - 1 and target > cumulative[segment_index + 1]:
            segment_index += 1
        lat, lon, azimuth, length, start = segments[segment_index]
        offset = min(max(target - start, 0.0), length)
        out_lon, out_lat, _ = _GEOD.fwd(lon, lat, azimuth, offset)
        points.append((float(out_lat), float(out_lon)))

    unique: list[tuple[float, float]] = []
    seen = set()
    for point in points:
        cell = grid_cell(*point)
        if cell not in seen:
            seen.add(cell)
            unique.append(point)
    if len(unique) > MAX_HEATMAP_POINTS:
        indices = [
            round(i * (len(unique) - 1) / (MAX_HEATMAP_POINTS - 1))
            for i in range(MAX_HEATMAP_POINTS)
        ]
        unique = [unique[index] for index in indices]
    return unique


def build_heatmap(
    geometry: list[list[float]], departure_utc: datetime, load_lb: float, service: WeatherService
) -> dict[str, object]:
    """Fetch one shared batch of point-hour weather and score each known sample."""
    points = sample_corridor(geometry)
    first_hour = select_hour(departure_utc)
    hours = [first_hour + timedelta(hours=i) for i in range(HOURS)]
    queries = [(lat, lon, hour) for lat, lon in points for hour in hours]
    weather_samples = service.weather_at_many(queries)
    output_points = []
    for point_index, (lat, lon) in enumerate(points):
        risk_values: list[int | None] = []
        samples = weather_samples[point_index * HOURS : (point_index + 1) * HOURS]
        for sample in samples:
            if isinstance(sample, MissingWeather):
                risk_values.append(None)
            else:
                risk = assess_checkpoint(
                    Weather(sample.wind_mph, sample.rain_in_hr, sample.snow_in_hr), load_lb
                )
                risk_values.append(int(risk.level))
        output_points.append({"lat": lat, "lon": lon, "risk": risk_values})
    return {
        "hours": [hour.astimezone(UTC).isoformat().replace("+00:00", "Z") for hour in hours],
        "points": output_points,
    }
