"""Mapbox Directions client. Pure Python, no Django imports.

`get_routes` = `fetch_directions` (HTTP + error mapping) + `parse_routes` (payload to Route).
They are separate so a recorded response can be parsed without network access.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

import requests

from routing.errors import (
    MapboxAuthError,
    MapboxConnectionError,
    MapboxError,
    MapboxNoRouteError,
    MapboxRateLimitError,
    MapboxRequestError,
    MapboxResponseError,
    MapboxServerError,
    MapboxTimeoutError,
)
from routing.geodesy import segment_lengths_m
from routing.models import Coord, Route, cumulative, join_routes

logger = logging.getLogger(__name__)

BASE_URL = "https://api.mapbox.com/directions/v5/mapbox/driving"
DEFAULT_TRUCK_TIME_FACTOR = 1.1
DEFAULT_TIMEOUT: tuple[float, float] = (5.0, 30.0)  # (connect, read) seconds
PROVIDER_LABEL = "provider alternative"

_NO_ROUTE_CODES = {"NoRoute", "NoSegment"}


# --------------------------------------------------------------------------- request


def _check_point(name: str, point: Coord) -> None:
    try:
        lat, lon = point
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a (lat, lon) pair, got {point!r}") from None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError(f"{name} must be finite, got {point!r}")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"{name} is out of range, got {point!r}")


def _check_factor(factor: float) -> None:
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError(f"truck_time_factor must be finite and > 0, got {factor!r}")


def build_request(origin: Coord, dest: Coord, token: str) -> tuple[str, dict[str, str]]:
    """Return (url, query params). Mapbox wants lon,lat order in the path."""
    _check_point("origin", origin)
    _check_point("dest", dest)
    if not token:
        raise ValueError("a Mapbox access token is required")
    url = f"{BASE_URL}/{origin[1]:.6f},{origin[0]:.6f};{dest[1]:.6f},{dest[0]:.6f}"
    params = {
        "alternatives": "true",
        "overview": "full",
        "geometries": "geojson",
        "annotations": "distance,duration",
        "access_token": token,
    }
    return url, params


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def _json_body(response: requests.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _raise_for_error_status(response: requests.Response, token: str) -> None:
    status = response.status_code
    if status == 200:
        return
    body = _json_body(response)
    code = body.get("code") if isinstance(body.get("code"), str) else None
    detail = body.get("message") if isinstance(body.get("message"), str) else response.text[:200]
    detail = _redact(detail, token)
    message = f"Mapbox returned HTTP {status}" + (f": {detail}" if detail else "")
    if code in _NO_ROUTE_CODES:
        raise MapboxNoRouteError(message, status_code=status, code=code)
    if status in (401, 403):
        raise MapboxAuthError(message, status_code=status, code=code)
    if status == 429:
        retry_after: float | None
        try:
            retry_after = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry_after = None
        raise MapboxRateLimitError(message, retry_after=retry_after)
    if status >= 500:
        raise MapboxServerError(message, status_code=status, code=code)
    raise MapboxRequestError(message, status_code=status, code=code)


def fetch_directions(
    origin: Coord,
    dest: Coord,
    token: str,
    *,
    timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Call Mapbox Directions and return the raw JSON payload (code == "Ok").

    Raises a MapboxError subclass for every failure. The token never appears in
    messages; the underlying requests exception is deliberately not chained because
    its text contains the full URL, including the token.
    """
    url, params = build_request(origin, dest, token)
    try:
        response = requests.get(url, params=params, timeout=timeout)
    except requests.Timeout:
        raise MapboxTimeoutError(f"Mapbox request timed out (timeout={timeout})") from None
    except requests.RequestException as exc:
        raise MapboxConnectionError(f"could not reach Mapbox ({type(exc).__name__})") from None

    _raise_for_error_status(response, token)
    try:
        payload = response.json()
    except ValueError:
        raise MapboxResponseError("Mapbox response was not valid JSON", status_code=200) from None
    if not isinstance(payload, dict):
        raise MapboxResponseError("Mapbox response was not a JSON object", status_code=200)

    code = payload.get("code")
    if code in _NO_ROUTE_CODES:
        raise MapboxNoRouteError(
            _redact(str(payload.get("message") or "no route found"), token),
            status_code=200,
            code=code,
        )
    if code != "Ok":
        raise MapboxRequestError(
            _redact(f"Mapbox returned code {code!r}: {payload.get('message', '')}", token),
            status_code=200,
            code=code if isinstance(code, str) else None,
        )
    return payload


# --------------------------------------------------------------------------- parsing


def _as_nonneg_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _usable_annotation(
    annotation: object, n_segments: int
) -> tuple[list[float], list[float]] | None:
    """Return (distances, durations) if the annotation is usable for this leg.

    Usable means: both lists exist, each has exactly `n_segments` entries
    (coordinate count - 1), and every entry is a finite non-negative number.
    """
    if not isinstance(annotation, dict):
        return None
    distances, durations = annotation.get("distance"), annotation.get("duration")
    if not isinstance(distances, list) or not isinstance(durations, list):
        return None
    if len(distances) != n_segments or len(durations) != n_segments:
        return None
    dist = [_as_nonneg_float(v) for v in distances]
    dur = [_as_nonneg_float(v) for v in durations]
    if any(v is None for v in dist) or any(v is None for v in dur):
        return None
    return [v for v in dist if v is not None], [v for v in dur if v is not None]


def _apportion(total: float, weights: Sequence[float]) -> list[float]:
    """Split `total` across segments in proportion to `weights` (equally if all zero)."""
    weight_sum = math.fsum(weights)
    if weight_sum > 0:
        return [total * w / weight_sum for w in weights]
    return [total / len(weights)] * len(weights)


def _build_leg(
    coords: list[Coord],
    annotation: object,
    distance_m: object,
    duration_s: object,
    factor: float,
) -> Route:
    """Build a single-leg Route, using annotations when they are usable."""
    n_segments = len(coords) - 1
    provider_dist = _as_nonneg_float(distance_m)
    provider_dur = _as_nonneg_float(duration_s)
    usable = _usable_annotation(annotation, n_segments)

    if usable is not None:
        seg_dist, raw_dur = usable
        seg_dur = [d * factor for d in raw_dur]
        method = "annotations"
        total_dist = provider_dist if provider_dist is not None else math.fsum(seg_dist)
        total_dur = provider_dur * factor if provider_dur is not None else math.fsum(seg_dur)
    else:
        if provider_dur is None:
            raise MapboxResponseError("leg has no usable annotations and no duration to apportion")
        geodesic = segment_lengths_m(coords)
        total_dist = provider_dist if provider_dist is not None else math.fsum(geodesic)
        total_dur = provider_dur * factor
        seg_dist = _apportion(total_dist, geodesic)
        seg_dur = _apportion(total_dur, seg_dist)
        method = "proportional"
        logger.warning(
            "annotation unusable for a leg with %d segments; using proportional durations",
            n_segments,
        )

    cum_dist, cum_dur = cumulative(seg_dist), cumulative(seg_dur)
    return Route(
        coords=coords,
        cum_dist_m=cum_dist,
        cum_dur_s=cum_dur,
        distance_m=total_dist,
        duration_s=total_dur,
        eta_method=method,  # type: ignore[arg-type]
        origin_label=PROVIDER_LABEL,
    )


def _parse_coords(raw: object) -> list[Coord]:
    if not isinstance(raw, list):
        raise MapboxResponseError("route geometry has no coordinate list")
    coords: list[Coord] = []
    for point in raw:
        lon, lat = float(point[0]), float(point[1])  # GeoJSON order is lon, lat
        coords.append((lat, lon))
    if len(coords) < 2:
        raise MapboxResponseError(f"route geometry has {len(coords)} point(s); need at least 2")
    return coords


def _split_leg_coords(coords: list[Coord], legs: list[dict[str, Any]]) -> list[list[Coord]] | None:
    """Split the route geometry per leg, or None if that is not possible.

    A leg with k annotation entries covers k segments, and consecutive legs share
    their joint point. Without annotation counts the leg boundaries are unknown.
    """
    if len(legs) == 1:
        return [coords]
    counts: list[int] = []
    for leg in legs:
        annotation = leg.get("annotation")
        distances = annotation.get("distance") if isinstance(annotation, dict) else None
        if not isinstance(distances, list) or len(distances) < 1:
            return None
        counts.append(len(distances))
    if sum(counts) != len(coords) - 1:
        return None
    pieces, start = [], 0
    for count in counts:
        pieces.append(coords[start : start + count + 1])
        start += count
    return pieces


def _parse_route(raw: dict[str, Any], factor: float) -> Route:
    coords = _parse_coords(raw["geometry"]["coordinates"])
    legs = raw["legs"]
    if not isinstance(legs, list) or not legs:
        raise MapboxResponseError("route has no legs")
    leg_coords = _split_leg_coords(coords, legs)
    if leg_coords is None:
        # Several legs but unknown boundaries: treat the whole route as one leg.
        pieces = [_build_leg(coords, None, raw.get("distance"), raw.get("duration"), factor)]
    else:
        pieces = [
            _build_leg(c, leg.get("annotation"), leg.get("distance"), leg.get("duration"), factor)
            for c, leg in zip(leg_coords, legs, strict=True)
        ]
    return join_routes(pieces, PROVIDER_LABEL)


def parse_routes(
    payload: dict[str, Any], *, truck_time_factor: float = DEFAULT_TRUCK_TIME_FACTOR
) -> list[Route]:
    """Convert a Mapbox Directions payload into Routes.

    Durations are multiplied by `truck_time_factor`. Each leg whose annotations are
    unusable (count != coordinates - 1, missing, or invalid values) gets durations
    apportioned by distance, and the route's eta_method becomes "proportional".
    """
    _check_factor(truck_time_factor)
    raw_routes = payload.get("routes")
    if not isinstance(raw_routes, list) or not raw_routes:
        raise MapboxNoRouteError("Mapbox returned no routes", status_code=200, code="NoRoute")
    routes: list[Route] = []
    for raw in raw_routes:
        try:
            routes.append(_parse_route(raw, truck_time_factor))
        except MapboxError:
            raise
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            raise MapboxResponseError(
                f"unexpected route shape in Mapbox response ({type(exc).__name__}: {exc})"
            ) from None
    return routes


def get_routes(
    origin: Coord,
    dest: Coord,
    token: str,
    *,
    truck_time_factor: float = DEFAULT_TRUCK_TIME_FACTOR,
    timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
) -> list[Route]:
    """Fetch up to 3 driving routes (primary + alternatives) from Mapbox.

    `origin` and `dest` are (lat, lon). Raises ValueError for bad arguments and a
    MapboxError subclass for any HTTP or API failure.
    """
    _check_factor(truck_time_factor)
    payload = fetch_directions(origin, dest, token, timeout=timeout)
    return parse_routes(payload, truck_time_factor=truck_time_factor)
