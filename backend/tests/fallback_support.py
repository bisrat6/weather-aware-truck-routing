"""Helpers for the third-route fallback tests: recorded payloads and a fake Mapbox Directions."""

import copy
import json
import threading
import time
from pathlib import Path

from routing.mapbox import parse_routes

FIXTURES = Path(__file__).parent / "fixtures"
MAIN = "mapbox_slc_provo.json"  # 2 routes, 45.6 mi / 3380 s and 49.7 mi / 4183 s (x1.1 factor)
EXCLUDE = "mapbox_slc_provo_exclude_motorway.json"  # recorded: 2 routes, first is 2.1x longer
VIA = "mapbox_slc_provo_via.json"  # recorded: 1 route, 1.65x longer, 77% cells shared


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self.body = body
        self.headers = headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self.body


def load_payload(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def scaled(payload, *, duration=1.0, distance=1.0):
    """Copy of a recorded payload with every duration/distance multiplied (geometry unchanged).

    Lets a recorded response stand in for a candidate that is within the 1.5x limit, or
    beyond it by distance, without inventing a geometry.
    """
    out = copy.deepcopy(payload)
    for route in out["routes"]:
        route["duration"] *= duration
        route["distance"] *= distance
        for leg in route["legs"]:
            leg["duration"] *= duration
            leg["distance"] *= distance
            annotation = leg.get("annotation") or {}
            annotation["duration"] = [v * duration for v in annotation.get("duration", [])]
            annotation["distance"] = [v * distance for v in annotation.get("distance", [])]
    return out


def first_route_only(payload):
    out = copy.deepcopy(payload)
    out["routes"] = out["routes"][:1]
    return out


def with_routes(payload, count):
    """Payload with exactly `count` routes (extra ones are copies of the first)."""
    out = copy.deepcopy(payload)
    while len(out["routes"]) < count:
        out["routes"].append(copy.deepcopy(out["routes"][0]))
    out["routes"] = out["routes"][:count]
    return out


def main_routes():
    return parse_routes(load_payload(MAIN))


def endpoints():
    waypoints = load_payload(MAIN)["waypoints"]
    (lon_a, lat_a), (lon_b, lat_b) = waypoints[0]["location"], waypoints[-1]["location"]
    return (lat_a, lon_a), (lat_b, lon_b)


def via_key(point):
    lat, lon = point
    return f"{lon:.6f},{lat:.6f}"


def request_key(url, params):
    """ "main", "exclude", or the via point as "lon,lat" for a Directions request."""
    if params.get("exclude"):
        return "exclude"
    points = url.rsplit("/", 1)[1].split(";")
    return points[1] if len(points) == 3 else "main"


class Directions:
    """Stand-in for requests.get on the Directions API.

    `responses` maps a request key ("main", "exclude", or a via key) to a payload dict, an
    Exception to raise, or a (status, body) tuple. Unlisted keys use `default`.
    """

    def __init__(self, responses, *, default=None, delays=None):
        self.responses = responses
        self.default = default
        self.delays = delays or {}
        self.calls = []
        self._lock = threading.Lock()

    @property
    def extra_calls(self):
        return [key for key in self.calls if key != "main"]

    def __call__(self, url, params=None, timeout=None):
        key = request_key(url, params or {})
        with self._lock:
            self.calls.append(key)
        time.sleep(self.delays.get(key, 0))
        item = self.responses.get(key, self.default)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            status, body = item
            return FakeResponse(status, body, {})
        return FakeResponse(200, item)
