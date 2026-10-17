"""Mapbox forward geocoding proxy. Credentials stay in query params server-side."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from routing.errors import MapboxConnectionError, MapboxRateLimitError, MapboxServerError

GEOCODING_URL = "https://api.mapbox.com/geocoding/v5/mapbox.places/"


def fetch_geocodes(query: str, token: str) -> list[dict[str, object]]:
    url = GEOCODING_URL + quote(query, safe="") + ".json"
    try:
        response = requests.get(
            url,
            params={"access_token": token, "limit": 5, "types": "place,address,locality"},
            timeout=(5.0, 20.0),
        )
    except requests.Timeout:
        raise MapboxConnectionError("Mapbox geocoding timed out") from None
    except requests.RequestException as exc:
        raise MapboxConnectionError(
            f"Mapbox geocoding connection failed ({type(exc).__name__})"
        ) from None
    if response.status_code == 429:
        try:
            retry = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry = None
        raise MapboxRateLimitError("Mapbox geocoding rate limit exceeded", retry_after=retry)
    if response.status_code >= 500 or response.status_code != 200:
        raise MapboxServerError("Mapbox geocoding is unavailable", status_code=response.status_code)
    try:
        payload: Any = response.json()
    except ValueError:
        raise MapboxServerError("Mapbox geocoding returned an invalid response") from None
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise MapboxServerError("Mapbox geocoding returned an invalid response")
    found = []
    for item in features[:5]:
        if not isinstance(item, dict):
            continue
        center = item.get("center")
        label = item.get("place_name")
        if (
            isinstance(center, list)
            and len(center) >= 2
            and isinstance(label, str)
            and isinstance(center[0], int | float)
            and isinstance(center[1], int | float)
        ):
            found.append({"label": label, "lat": center[1], "lon": center[0]})
    return found
