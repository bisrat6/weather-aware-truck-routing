"""Record one real Open-Meteo forecast response as a test fixture.

Usage (from the backend/ directory; no API key or token is needed):

    python scripts/record_weather_fixture.py
    python scripts/record_weather_fixture.py --name utah_wyoming --days 2 \
        --point 40.7,-111.9 --point 39.7,-105.0 --point 41.1,-104.8

With no arguments it records three points (Salt Lake City, Denver, Cheyenne) for 2 days.
Output goes to tests/fixtures/open_meteo_<name>.json, in the exact shape the API returns
(a JSON list for several points), so tests/weather/test_weather_fixtures.py picks it up.

The request is built by weather.client, so it uses the same units (mph, inch, UTC) and
variables the app uses. Open-Meteo's free tier is for non-commercial use (CC BY 4.0).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from weather.client import MAX_FORECAST_DAYS, fetch_raw, parse_payload  # noqa: E402
from weather.errors import WeatherError  # noqa: E402

FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures"
DEFAULT_POINTS = [(40.7, -111.9), (39.7, -105.0), (41.1, -104.8)]


def parse_point(text: str) -> tuple[float, float]:
    lat, lon = (float(part) for part in text.split(","))
    return lat, lon


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--name", default="recorded", help="fixture name (default: recorded)")
    parser.add_argument("--days", type=int, default=2, help=f"forecast days, 1-{MAX_FORECAST_DAYS}")
    parser.add_argument(
        "--point", type=parse_point, action="append", help="lat,lon (repeat for several points)"
    )
    args = parser.parse_args()
    points = args.point or DEFAULT_POINTS

    try:
        payload = fetch_raw(points, args.days)
        series = parse_payload(payload, len(points))  # fail now if the shape or units are off
    except (WeatherError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / f"open_meteo_{args.name}.json"
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    nulls = sum(
        v is None
        for s in series
        for hour in s.values
        for v in (hour.wind_mph, hour.rain_in, hour.showers_in, hour.snow_in)
    )
    print(f"saved {path.relative_to(BACKEND_DIR)}: {len(points)} point(s), {args.days} day(s)")
    print(f"first hour {series[0].times[0].isoformat()}, null values in response: {nulls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
