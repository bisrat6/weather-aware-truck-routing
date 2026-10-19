"""Shared helpers for the weather tests. All HTTP is mocked; nothing here uses the network."""

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
HANDBUILT = FIXTURE_DIR / "open_meteo_handbuilt.json"

# Cell centres of the three hand-built fixture locations, in fixture order.
FIXTURE_POINTS = [(40.7, -111.9), (39.7, -105.0), (41.1, -104.8)]
# Points that snap to those cells.
SLC, DENVER, CHEYENNE = (40.71, -111.88), (39.72, -105.02), (41.12, -104.78)
FIXTURE_START = datetime(2026, 10, 17, 0, 0)


def load_fixture():
    return json.loads(HANDBUILT.read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status=200, body=None, text=None, headers=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad_json = bad_json
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


def _padded_location(template, n_hours):
    """A location with `n_hours` hourly values: the template's, then benign padding."""
    item = copy.deepcopy(template)
    hourly = item["hourly"]
    have = len(hourly["time"])
    if n_hours <= have:
        for key in hourly:
            hourly[key] = hourly[key][:n_hours]
        return item
    start = FIXTURE_START
    hourly["time"] = [
        (start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(n_hours)
    ]
    for key in ("wind_speed_10m", "rain", "showers", "snowfall"):
        pad = 1.0 if key == "wind_speed_10m" else 0.0
        hourly[key] = hourly[key] + [pad] * (n_hours - have)
    return item


def open_meteo_response(params):
    """What Open-Meteo would return for these query params, built from the fixture.

    Emulates the real shape: an object for one location, a list for several (the first
    without `location_id`), sliced to forecast_days. Points not in the fixture get benign
    constant data. It always answers in mph/inch whatever units were requested; the tests
    check the requested units on the URL itself.
    """
    lats = [float(x) for x in params["latitude"].split(",")]
    lons = [float(x) for x in params["longitude"].split(",")]
    n_hours = 24 * int(params.get("forecast_days", 7))
    fixture = load_fixture()
    items = []
    for index, (lat, lon) in enumerate(zip(lats, lons, strict=True)):
        if (lat, lon) in FIXTURE_POINTS:
            template = fixture[FIXTURE_POINTS.index((lat, lon))]
        else:
            template = copy.deepcopy(fixture[0])
            hourly = template["hourly"]
            hourly["wind_speed_10m"] = [1.0] * len(hourly["time"])
            hourly["rain"] = [0.0] * len(hourly["time"])
            hourly["showers"] = [0.0] * len(hourly["time"])
            hourly["snowfall"] = [0.0] * len(hourly["time"])
        item = _padded_location(template, n_hours)
        item["latitude"], item["longitude"] = lat, lon
        item.pop("location_id", None)
        if index > 0:
            item["location_id"] = index
        items.append(item)
    return items if len(items) > 1 else items[0]


class FakeHttp:
    """Stands in for requests.get. Records every call; answers via `responder`."""

    def __init__(self):
        self.calls = []
        self.responder = lambda params: FakeResponse(body=open_meteo_response(params))
        self.raises = None

    def __call__(self, url, params=None, timeout=None, headers=None):
        self.calls.append(
            {
                "url": url,
                "params": dict(params or {}),
                "timeout": timeout,
                "headers": dict(headers or {}),
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.responder(params)

    def respond_with(self, response):
        self.responder = lambda params: response

    @property
    def count(self):
        return len(self.calls)

    def full_url(self, index=-1):
        """The URL requests would actually send for the recorded call."""
        call = self.calls[index]
        return requests.Request("GET", call["url"], params=call["params"]).prepare().url

    def query(self, index=-1):
        """The recorded call's query string, decoded."""
        return {k: v[0] for k, v in parse_qs(urlparse(self.full_url(index)).query).items()}
