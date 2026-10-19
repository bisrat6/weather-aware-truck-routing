"""HERE Routing v8 client (truck mode). Pure Python, no Django imports.

`get_routes` = `fetch_directions` (HTTP + error mapping) + `parse_routes` (payload to Route).
They are separate so a recorded response can be parsed without network access.

A response has `routes[].sections[]`. Each section has a flexible-polyline geometry, a
`summary` (length in metres, duration in seconds) and `spans`. Spans split the section
wherever the requested attributes change (here the speed limit), each with its own length
and duration, so checkpoint ETAs follow the road's speed profile rather than a flat average.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

import requests

from routing.errors import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderNoRouteError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTimeoutError,
)
from routing.flexpolyline import decode
from routing.geodesy import segment_lengths_m
from routing.models import Coord, Route, cumulative, join_routes

logger = logging.getLogger(__name__)

BASE_URL = "https://router.hereapi.com/v8/routes"
# HERE's truck mode already models truck speeds, so no extra time factor by default.
DEFAULT_TRUCK_TIME_FACTOR = 1.0
DEFAULT_TIMEOUT: tuple[float, float] = (3.0, 30.0)  # (connect, read) seconds
PROVIDER_LABEL = "HERE route"
ALTERNATIVES = 2  # extra routes besides the best one, so up to 3 routes in total
SPANS = "length,duration,speedLimit"


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


def build_request(origin: Coord, dest: Coord, api_key: str) -> tuple[str, dict[str, str]]:
    """Return (url, query params). HERE wants "lat,lon" order."""
    _check_point("origin", origin)
    _check_point("dest", dest)
    if not api_key:
        raise ValueError("a HERE API key is required")
    params = {
        "transportMode": "truck",
        "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
        "destination": f"{dest[0]:.6f},{dest[1]:.6f}",
        "alternatives": str(ALTERNATIVES),
        "return": "polyline,summary",
        "spans": SPANS,
        "apiKey": api_key,
    }
    return BASE_URL, params


def _redact(text: str, api_key: str) -> str:
    return text.replace(api_key, "***") if api_key else text


def _json_body(response: requests.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _raise_for_error_status(response: requests.Response, api_key: str) -> None:
    status = response.status_code
    if status == 200:
        return
    body = _json_body(response)
    code = next((body[k] for k in ("code", "error") if isinstance(body.get(k), str)), None)
    detail = next(
        (body[k] for k in ("error_description", "title", "cause") if isinstance(body.get(k), str)),
        response.text[:200],
    )
    detail = _redact(detail, api_key)
    message = f"HERE returned HTTP {status}" + (f": {detail}" if detail else "")
    if status in (401, 403):
        raise ProviderAuthError(message, status_code=status, code=code)
    if status == 429:
        retry_after: float | None
        try:
            retry_after = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry_after = None
        raise ProviderRateLimitError(message, retry_after=retry_after)
    if status >= 500:
        raise ProviderServerError(message, status_code=status, code=code)
    raise ProviderRequestError(message, status_code=status, code=code)


def _no_route_error(payload: dict[str, Any], api_key: str) -> ProviderNoRouteError:
    notices = payload.get("notices")
    first = notices[0] if isinstance(notices, list) and notices else {}
    first = first if isinstance(first, dict) else {}
    title = first.get("title") if isinstance(first.get("title"), str) else "no route found"
    code = first.get("code") if isinstance(first.get("code"), str) else "noRouteFound"
    return ProviderNoRouteError(_redact(title, api_key), status_code=200, code=code)


def fetch_directions(
    origin: Coord,
    dest: Coord,
    api_key: str,
    *,
    timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Call HERE Routing and return the raw JSON payload (at least one route).

    Raises a ProviderError subclass for every failure. The API key never appears in
    messages; the underlying requests exception is deliberately not chained because its
    text contains the full URL, including the key.
    """
    url, params = build_request(origin, dest, api_key)
    try:
        response = requests.get(url, params=params, timeout=timeout)
    except requests.Timeout:
        raise ProviderTimeoutError(f"HERE request timed out (timeout={timeout})") from None
    except requests.RequestException as exc:
        raise ProviderConnectionError(f"could not reach HERE ({type(exc).__name__})") from None

    _raise_for_error_status(response, api_key)
    try:
        payload = response.json()
    except ValueError:
        raise ProviderResponseError("HERE response was not valid JSON", status_code=200) from None
    if not isinstance(payload, dict):
        raise ProviderResponseError("HERE response was not a JSON object", status_code=200)
    routes = payload.get("routes")
    if not isinstance(routes, list):
        raise ProviderResponseError("HERE response has no routes list", status_code=200)
    if not routes:
        raise _no_route_error(payload, api_key)
    return payload


# --------------------------------------------------------------------------- parsing


def _as_nonneg_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


# Distances below 1 millimetre (1e-3 m) are negligible for road routing. When the total
# weight is negligible or zero, weights cannot give a meaningful proportional breakdown.
NEGLIGIBLE_DISTANCE_M = 1e-3


def _apportion(total: float, weights: Sequence[float]) -> list[float]:
    """Split `total` across segments in proportion to `weights` (equally if negligible)."""
    if not weights:
        return []
    if len(weights) == 1:
        return [total]
    weight_sum = math.fsum(weights)
    if weight_sum > NEGLIGIBLE_DISTANCE_M:
        return [(w / weight_sum) * total for w in weights]
    return [total / len(weights)] * len(weights)


def _usable_spans(spans: object, n_segments: int) -> list[tuple[int, float, float]] | None:
    """Return [(first segment, length_m, duration_s)] if the spans can be used, else None.

    Usable means: a non-empty list whose offsets (polyline point indexes) start at 0 and
    strictly increase, stay below the segment count, and carry finite non-negative length
    and duration. Each span covers the segments from its offset to the next span's offset.
    """
    if not isinstance(spans, list) or not spans:
        return None
    parsed: list[tuple[int, float, float]] = []
    for span in spans:
        if not isinstance(span, dict):
            return None
        offset = span.get("offset")
        length, duration = (
            _as_nonneg_float(span.get("length")),
            _as_nonneg_float(span.get("duration")),
        )
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or length is None
            or duration is None
        ):
            return None
        parsed.append((offset, length, duration))
    offsets = [offset for offset, _, _ in parsed]
    if offsets[0] != 0 or offsets[-1] >= n_segments:
        return None
    if any(b <= a for a, b in pairwise(offsets)):
        return None
    return parsed


def _build_section(section: dict[str, Any], factor: float) -> Route:
    """Build a single-section Route, using spans when they are usable."""
    try:
        coords = decode(section["polyline"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderResponseError(f"section has no usable polyline ({exc})") from None
    if len(coords) < 2:
        raise ProviderResponseError(f"route geometry has {len(coords)} point(s); need at least 2")
    summary = section.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    provider_dur = _as_nonneg_float(summary.get("duration"))
    if provider_dur is None:
        raise ProviderResponseError("section has no usable summary duration")
    provider_dist = _as_nonneg_float(summary.get("length"))

    geodesic = segment_lengths_m(coords)
    n_segments = len(coords) - 1
    spans = _usable_spans(section.get("spans"), n_segments)
    if spans is not None:
        seg_dist: list[float] = []
        seg_dur: list[float] = []
        bounds = [offset for offset, _, _ in spans] + [n_segments]
        for (_, length, duration), start, end in zip(spans, bounds[:-1], bounds[1:], strict=True):
            weights = geodesic[start:end]
            seg_dist.extend(_apportion(length, weights))
            seg_dur.extend(_apportion(duration * factor, weights or [1.0]))
        total_dist = provider_dist if provider_dist is not None else math.fsum(seg_dist)
        total_dur = provider_dur * factor
        method = "annotations"
    else:
        geo_len = math.fsum(geodesic)
        total_dist = provider_dist if provider_dist is not None else geo_len
        total_dur = provider_dur * factor
        seg_dist = _apportion(total_dist, geodesic)
        dur_weights = geodesic if geo_len > NEGLIGIBLE_DISTANCE_M else seg_dist
        seg_dur = _apportion(total_dur, dur_weights)
        method = "proportional"
        logger.warning(
            "spans unusable for a section with %d segments; using proportional durations",
            n_segments,
        )

    return Route(
        coords=coords,
        cum_dist_m=cumulative(seg_dist),
        cum_dur_s=cumulative(seg_dur),
        distance_m=total_dist,
        duration_s=total_dur,
        eta_method=method,  # type: ignore[arg-type]
        origin_label=PROVIDER_LABEL,
    )


def _parse_route(raw: dict[str, Any], factor: float) -> Route:
    sections = raw["sections"]
    if not isinstance(sections, list) or not sections:
        raise ProviderResponseError("route has no sections")
    return join_routes([_build_section(s, factor) for s in sections], PROVIDER_LABEL)


def parse_routes(
    payload: dict[str, Any], *, truck_time_factor: float = DEFAULT_TRUCK_TIME_FACTOR
) -> list[Route]:
    """Convert a HERE Routing payload into Routes.

    Durations are multiplied by `truck_time_factor`. A section whose spans are unusable
    gets durations apportioned by distance, and its route's eta_method becomes
    "proportional".
    """
    _check_factor(truck_time_factor)
    raw_routes = payload.get("routes")
    if not isinstance(raw_routes, list) or not raw_routes:
        raise _no_route_error(payload, "")
    routes: list[Route] = []
    for raw in raw_routes:
        try:
            routes.append(_parse_route(raw, truck_time_factor))
        except ProviderError:
            raise
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            raise ProviderResponseError(
                f"unexpected route shape in HERE response ({type(exc).__name__}: {exc})"
            ) from None
    return routes


def get_routes(
    origin: Coord,
    dest: Coord,
    api_key: str,
    *,
    truck_time_factor: float = DEFAULT_TRUCK_TIME_FACTOR,
    timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
) -> list[Route]:
    """Fetch up to 3 truck routes (best + 2 alternatives) from HERE.

    `origin` and `dest` are (lat, lon). Raises ValueError for bad arguments and a
    ProviderError subclass for any HTTP or API failure.
    """
    _check_factor(truck_time_factor)
    payload = fetch_directions(origin, dest, api_key, timeout=timeout)
    return parse_routes(payload, truck_time_factor=truck_time_factor)
