"""Record real Mapbox Directions responses as test fixtures.

Usage (from the backend/ directory):

    # Record both SLC -> Provo fallback responses (motorway exclusion and 3-waypoint via):
    MAPBOX_TOKEN=pk.... python scripts/record_fixture.py --slc-provo-fallbacks --via 40.35,-111.90

    # Or record individual variants:
    MAPBOX_TOKEN=pk.... python scripts/record_fixture.py --exclude-motorway
    MAPBOX_TOKEN=pk.... python scripts/record_fixture.py --via 40.35,-111.90

    # Or record standard city pairs:
    MAPBOX_TOKEN=pk.... python scripts/record_fixture.py
    MAPBOX_TOKEN=pk.... python scripts/record_fixture.py --name dallas_houston \
        --origin 32.7767,-96.7970 --dest 29.7604,-95.3698

Output goes to tests/fixtures/mapbox_<name>.json. The token is read only from the
MAPBOX_TOKEN environment variable (never from argv, so it stays out of shell history)
and is verified to be absent from every file before it is written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from routing.mapbox import BASE_URL, Coord, _split_leg_coords  # noqa: E402

FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures"

SLC: Coord = (40.7608, -111.8910)
PROVO: Coord = (40.2338, -111.6585)
DEFAULT_SLC_VIA: Coord = (40.3500, -111.9000)

DEFAULT_PAIRS: dict[str, tuple[Coord, Coord]] = {
    "denver_slc": ((39.7392, -104.9903), (40.7608, -111.8910)),
    "chicago_indianapolis": ((41.8781, -87.6298), (39.7684, -86.1581)),
    "dallas_houston": ((32.7767, -96.7970), (29.7604, -95.3698)),
}


def parse_point(text: str) -> tuple[float, float]:
    lat, lon = (float(part.strip()) for part in text.split(","))
    return lat, lon


def sanitize_payload(payload: dict[str, Any], token: str) -> dict[str, Any]:
    """Ensure no access tokens are present in any field or stringified nested object."""
    if not token:
        return payload
    raw_text = json.dumps(payload)
    if token in raw_text:
        raw_text = raw_text.replace(token, "***")
        return json.loads(raw_text)
    return payload


def print_inspection(payload: dict[str, Any], label: str) -> None:
    routes = payload.get("routes", [])
    waypoints = payload.get("waypoints", [])
    print("\n" + "=" * 50)
    print(f"Inspection: {label}")
    print(f"Number of routes: {len(routes)}")

    wp_info = [f"#{i}: {wp.get('name')!r}" for i, wp in enumerate(waypoints)]
    print(f"Waypoints names: [{', '.join(wp_info)}]")

    for r_idx, route in enumerate(routes):
        legs = route.get("legs", [])
        raw_coords = route.get("geometry", {}).get("coordinates", [])
        total_coords = len(raw_coords)
        print(f"\nRoute {r_idx}: {len(legs)} leg(s), {total_coords} coordinates total")

        coords_lat_lon = [(c[1], c[0]) for c in raw_coords]
        pieces = _split_leg_coords(coords_lat_lon, legs)

        for l_idx, leg in enumerate(legs):
            annotation = leg.get("annotation") or {}
            dist_ann = annotation.get("distance", [])
            dur_ann = annotation.get("duration", [])
            d_count = len(dist_ann) if isinstance(dist_ann, list) else 0
            t_count = len(dur_ann) if isinstance(dur_ann, list) else 0

            if pieces is not None and l_idx < len(pieces):
                leg_coords_count = len(pieces[l_idx])
                matches = (d_count == leg_coords_count - 1) and (t_count == leg_coords_count - 1)
                print(
                    f"  Leg {l_idx}: coordinates={leg_coords_count}, "
                    f"distance_annotations={d_count}, duration_annotations={t_count} "
                    f"-> (annotation count == coordinate count - 1: {matches})"
                )
            else:
                matches = (d_count == total_coords - 1) and (t_count == total_coords - 1)
                print(
                    f"  Leg {l_idx}: dist_annotations={d_count}, dur_annotations={t_count}, "
                    f"total_coords={total_coords} -> (annotation count == coords - 1: {matches})"
                )
    print("=" * 50 + "\n")


def fetch_and_record(
    name: str,
    waypoints: Sequence[Coord],
    token: str,
    *,
    extra_params: dict[str, str] | None = None,
    description: str = "",
) -> Path | None:
    """Request Mapbox Directions for 2 or more waypoints and record the response.

    Error messages print the status and Mapbox error code (e.g. 422 InvalidInput)
    without ever printing the request URL.
    """
    path_coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in waypoints)
    url = f"{BASE_URL}/{path_coords}"
    params: dict[str, str] = {
        "overview": "full",
        "geometries": "geojson",
        "annotations": "distance,duration",
        "access_token": token,
    }
    if extra_params:
        params.update(extra_params)

    try:
        response = requests.get(url, params=params, timeout=(5.0, 30.0))
    except requests.RequestException as exc:
        print(f"Network error requesting Mapbox ({type(exc).__name__})", file=sys.stderr)
        return None

    if response.status_code != 200:
        try:
            body = response.json()
        except ValueError:
            body = {}
        code = body.get("code") if isinstance(body, dict) else None
        message = body.get("message") if isinstance(body, dict) else response.text[:200]
        if token and message:
            message = message.replace(token, "***")
        err_msg = f"{response.status_code} {code or 'Error'}" + (f": {message}" if message else "")
        print(f"Mapbox error: {err_msg}", file=sys.stderr)
        return None

    try:
        payload = response.json()
    except ValueError:
        print("Mapbox error: response was not valid JSON", file=sys.stderr)
        return None

    clean_payload = sanitize_payload(payload, token)
    text = json.dumps(clean_payload, indent=1)
    if token in text:
        raise SystemExit(f"Refusing to write {name}: payload contains access token")

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FIXTURE_DIR / f"mapbox_{name}.json"
    out_path.write_text(text + "\n", encoding="utf-8")
    print(f"Saved {out_path.relative_to(BACKEND_DIR)}")

    print_inspection(clean_payload, description or name)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--name", help="fixture name (e.g. slc_provo_exclude_motorway)")
    parser.add_argument("--origin", type=parse_point, help="lat,lon")
    parser.add_argument("--dest", type=parse_point, help="lat,lon")
    parser.add_argument(
        "--via", type=parse_point, help="lat,lon via waypoint for 3-waypoint route"
    )
    parser.add_argument(
        "--exclude-motorway",
        action="store_true",
        help="include exclude=motorway and alternatives=true",
    )
    parser.add_argument(
        "--slc-provo-fallbacks",
        action="store_true",
        help="record both (a) exclude=motorway and (b) 3-waypoint via for SLC to Provo",
    )
    args = parser.parse_args()

    token = os.environ.get("MAPBOX_TOKEN", "")
    if not token:
        print("MAPBOX_TOKEN environment variable is not set", file=sys.stderr)
        return 2

    # Mode 1: --slc-provo-fallbacks
    if args.slc_provo_fallbacks:
        via_pt = args.via or DEFAULT_SLC_VIA
        print(f"Recording SLC -> Provo fallbacks (origin={SLC}, dest={PROVO}, via={via_pt})...")

        # (a) exclude=motorway and alternatives=true
        res_a = fetch_and_record(
            name="slc_provo_exclude_motorway",
            waypoints=[SLC, PROVO],
            token=token,
            extra_params={"exclude": "motorway", "alternatives": "true"},
            description="SLC to Provo with exclude=motorway and alternatives=true",
        )

        # (b) 3-waypoint request origin -> via -> destination
        res_b = fetch_and_record(
            name="slc_provo_via",
            waypoints=[SLC, via_pt, PROVO],
            token=token,
            extra_params={},
            description=f"SLC to Provo 3-waypoint detour via {via_pt}",
        )
        return 0 if (res_a and res_b) else 1

    # Mode 2: individual --exclude-motorway
    if args.exclude_motorway:
        origin = args.origin or SLC
        dest = args.dest or PROVO
        name = args.name or "slc_provo_exclude_motorway"
        res = fetch_and_record(
            name=name,
            waypoints=[origin, dest],
            token=token,
            extra_params={"exclude": "motorway", "alternatives": "true"},
            description=f"{origin} -> {dest} with exclude=motorway and alternatives=true",
        )
        return 0 if res else 1

    # Mode 3: individual --via
    if args.via:
        origin = args.origin or SLC
        dest = args.dest or PROVO
        name = args.name or "slc_provo_via"
        res = fetch_and_record(
            name=name,
            waypoints=[origin, args.via, dest],
            token=token,
            extra_params={},
            description=f"{origin} -> {args.via} -> {dest} (3-waypoint)",
        )
        return 0 if res else 1

    # Mode 4: Custom single pair
    if args.name or args.origin or args.dest:
        if not (args.name and args.origin and args.dest):
            parser.error("--name, --origin, and --dest must be specified together")
        res = fetch_and_record(
            name=args.name,
            waypoints=[args.origin, args.dest],
            token=token,
            extra_params={"alternatives": "true"},
            description=f"{args.name}: {args.origin} -> {args.dest}",
        )
        return 0 if res else 1

    # Mode 5: Default city pairs
    for name, (origin, dest) in DEFAULT_PAIRS.items():
        fetch_and_record(
            name=name,
            waypoints=[origin, dest],
            token=token,
            extra_params={"alternatives": "true"},
            description=name,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
