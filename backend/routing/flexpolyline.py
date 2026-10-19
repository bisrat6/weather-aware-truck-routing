"""Decoder for HERE's flexible polyline format (version 1). Pure Python.

Spec: https://github.com/heremaps/flexible-polyline. Only decoding is needed. A third
dimension, if present, is read and dropped. Raises ValueError for malformed input.
"""

from __future__ import annotations

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_INDEX = {char: index for index, char in enumerate(_ALPHABET)}


def _unsigned_values(text: str) -> list[int]:
    """Split the string into variable-length unsigned integers (5 data bits per char)."""
    values: list[int] = []
    value = shift = 0
    for char in text:
        chunk = _INDEX.get(char)
        if chunk is None:
            raise ValueError(f"invalid character {char!r} in flexible polyline")
        value |= (chunk & 0x1F) << shift
        shift += 5
        if not chunk & 0x20:  # control bit clear: last chunk of this integer
            values.append(value)
            value = shift = 0
    if shift:
        raise ValueError("flexible polyline ends in the middle of a value")
    return values


def decode(encoded: str) -> list[tuple[float, float]]:
    """Decode to a list of (lat, lon)."""
    values = _unsigned_values(encoded)
    if len(values) < 2:
        raise ValueError("flexible polyline has no header")
    version, header = values[0], values[1]
    if version != 1:
        raise ValueError(f"unsupported flexible polyline version {version}")
    scale = 10 ** (header & 0xF)
    size = 3 if (header >> 4) & 0x7 else 2
    data = values[2:]
    if len(data) % size:
        raise ValueError("flexible polyline has an incomplete coordinate")
    lat = lon = 0
    coords: list[tuple[float, float]] = []
    for start in range(0, len(data), size):
        # Signed values: the lowest bit is the sign.
        deltas = [-((v + 1) >> 1) if v & 1 else v >> 1 for v in data[start : start + size]]
        lat += deltas[0]
        lon += deltas[1]
        coords.append((lat / scale, lon / scale))
    return coords
