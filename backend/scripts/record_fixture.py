"""Record real HERE Routing responses as test fixtures.

Usage (from the backend/ directory):

    HERE_API_KEY=... python scripts/record_fixture.py --name slc_provo \
        --origin 40.7608,-111.8910 --dest 40.2338,-111.6585

    # Or record the standard trips used by the tests:
    HERE_API_KEY=... python scripts/record_fixture.py --standard

Output goes to tests/fixtures/here_<name>.json. The key is read only from the HERE_API_KEY
environment variable (never from argv, so it stays out of shell history) and is verified to
be absent from every file before it is written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from routing.errors import ProviderError  # noqa: E402
from routing.here import Coord, fetch_directions, parse_routes  # noqa: E402

FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures"

STANDARD_TRIPS: dict[str, tuple[Coord, Coord]] = {
    "slc_provo": ((40.7608, -111.8910), (40.2338, -111.6585)),
    "la_nyc": ((34.0522, -118.2437), (40.7128, -74.0060)),
    "rural_wy": ((41.1398, -104.8202), (41.7910, -107.2387)),
}


def parse_point(text: str) -> Coord:
    lat, lon = (float(part.strip()) for part in text.split(","))
    return lat, lon


def record(name: str, origin: Coord, dest: Coord, api_key: str) -> bool:
    try:
        payload = fetch_directions(origin, dest, api_key, timeout=(5.0, 90.0))
    except ProviderError as exc:
        print(f"HERE error for {name}: {exc}", file=sys.stderr)
        return False
    text = json.dumps(payload, indent=1)
    if api_key in text:
        raise SystemExit(f"Refusing to write {name}: payload contains the API key")
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FIXTURE_DIR / f"here_{name}.json"
    out_path.write_text(text + "\n", encoding="utf-8")
    routes = parse_routes(payload)
    print(f"Saved {out_path.relative_to(BACKEND_DIR)}")
    for index, route in enumerate(routes):
        print(
            f"  route {index}: {len(route.coords)} points, "
            f"{route.distance_m / 1609.344:.1f} mi, {route.duration_s / 60:.0f} min, "
            f"eta_method={route.eta_method}"
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--name", help="fixture name (e.g. slc_provo)")
    parser.add_argument("--origin", type=parse_point, help="lat,lon")
    parser.add_argument("--dest", type=parse_point, help="lat,lon")
    parser.add_argument("--standard", action="store_true", help="record the standard trips")
    args = parser.parse_args()

    api_key = os.environ.get("HERE_API_KEY", "")
    if not api_key:
        print("HERE_API_KEY environment variable is not set", file=sys.stderr)
        return 2
    if args.standard:
        return 0 if all(record(n, o, d, api_key) for n, (o, d) in STANDARD_TRIPS.items()) else 1
    if not (args.name and args.origin and args.dest):
        parser.error("give --name, --origin and --dest, or use --standard")
    return 0 if record(args.name, args.origin, args.dest, api_key) else 1


if __name__ == "__main__":
    raise SystemExit(main())
