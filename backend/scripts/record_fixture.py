"""Record real Mapbox Directions responses as test fixtures.

Usage (from the backend/ directory):

    MAPBOX_TOKEN=pk.... python scripts/record_fixture.py
    MAPBOX_TOKEN=pk.... python scripts/record_fixture.py --name dallas_houston \
        --origin 32.7767,-96.7970 --dest 29.7604,-95.3698

With no arguments it records a few default city pairs. Output goes to
tests/fixtures/mapbox_<name>.json. The token is read only from the MAPBOX_TOKEN
environment variable (never from argv, so it stays out of shell history) and is
checked to be absent from every file before it is written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from routing.errors import MapboxError  # noqa: E402
from routing.mapbox import fetch_directions  # noqa: E402

FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures"

# name -> ((origin lat, lon), (dest lat, lon))
DEFAULT_PAIRS: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {
    "denver_slc": ((39.7392, -104.9903), (40.7608, -111.8910)),
    "chicago_indianapolis": ((41.8781, -87.6298), (39.7684, -86.1581)),
    "dallas_houston": ((32.7767, -96.7970), (29.7604, -95.3698)),
}


def parse_point(text: str) -> tuple[float, float]:
    lat, lon = (float(part) for part in text.split(","))
    return lat, lon


def record(name: str, origin: tuple[float, float], dest: tuple[float, float], token: str) -> Path:
    payload = fetch_directions(origin, dest, token)
    text = json.dumps(payload, indent=1)
    if token in text:
        raise SystemExit(f"refusing to write {name}: the response contains the access token")
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / f"mapbox_{name}.json"
    path.write_text(text + "\n", encoding="utf-8")
    routes = payload.get("routes", [])
    print(f"saved {path.relative_to(BACKEND_DIR)}: {len(routes)} route(s)")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--name", help="fixture name (requires --origin and --dest)")
    parser.add_argument("--origin", type=parse_point, help="lat,lon")
    parser.add_argument("--dest", type=parse_point, help="lat,lon")
    args = parser.parse_args()

    token = os.environ.get("MAPBOX_TOKEN", "")
    if not token:
        print("MAPBOX_TOKEN is not set", file=sys.stderr)
        return 2

    if args.name or args.origin or args.dest:
        if not (args.name and args.origin and args.dest):
            parser.error("--name, --origin and --dest must be given together")
        pairs = {args.name: (args.origin, args.dest)}
    else:
        pairs = DEFAULT_PAIRS

    try:
        for name, (origin, dest) in pairs.items():
            record(name, origin, dest, token)
    except MapboxError as exc:
        print(f"Mapbox error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
