"""Helpers for tests that use recorded HERE Routing responses."""

import json
from pathlib import Path

from routing.flexpolyline import decode

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name):
    """A recorded response, e.g. load_fixture("slc_provo") reads here_slc_provo.json."""
    return json.loads((FIXTURES / f"here_{name}.json").read_text(encoding="utf-8"))


def route_coords(name, route_index=0):
    """The first section's polyline as [(lat, lon), ...]."""
    return decode(load_fixture(name)["routes"][route_index]["sections"][0]["polyline"])


def route_geometry(name, route_index=0):
    """GeoJSON-style {"coordinates": [[lon, lat], ...]} as the API returns it."""
    return {"coordinates": [[lon, lat] for lat, lon in route_coords(name, route_index)]}


_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _varint(value):
    chars = []
    while True:
        chunk = value & 0x1F
        value >>= 5
        if value:
            chars.append(_ALPHABET[chunk | 0x20])
        else:
            chars.append(_ALPHABET[chunk])
            return "".join(chars)


def _signed(value):
    return _varint(value << 1 if value >= 0 else ((-value) << 1) - 1)


def encode_polyline(coords, precision=6, third=None):
    """Flexible polyline encoder (test-only), the inverse of routing.flexpolyline.decode.

    `third` is an optional list of third-dimension integers (already scaled).
    """
    out = _varint(1) + _varint(precision | ((1 << 4) if third is not None else 0))
    scale = 10**precision
    last = [0, 0, 0]
    for index, (lat, lon) in enumerate(coords):
        values = [round(lat * scale), round(lon * scale)]
        if third is not None:
            values.append(third[index])
        for position, value in enumerate(values):
            out += _signed(value - last[position])
            last[position] = value
    return out


def section(coords, spans, *, length=None, duration=None):
    """A minimal HERE section for hand-built payloads."""
    return {
        "polyline": encode_polyline(coords),
        "summary": {
            "length": length if length is not None else sum(s["length"] for s in spans),
            "duration": duration if duration is not None else sum(s["duration"] for s in spans),
        },
        "spans": spans,
    }


def payload_of(*routes):
    """A payload whose routes each have the given list of sections."""
    return {"routes": [{"id": f"r{i}", "sections": sections} for i, sections in enumerate(routes)]}
