"""HERE place search proxy (Autosuggest). The API key stays server-side."""

from __future__ import annotations

from typing import Any

import requests

from routing.errors import ProviderConnectionError, ProviderRateLimitError, ProviderServerError

GEOCODING_URL = "https://autosuggest.search.hereapi.com/v1/autosuggest"
# Autosuggest needs a bias point; this is roughly the centre of the contiguous US. It only
# ranks results, it does not exclude places elsewhere.
BIAS_POINT = "39.8,-98.6"
# Result types that are a single place we can route to (not categories or search chains).
PLACE_TYPES = frozenset({"locality", "place", "houseNumber", "street", "administrativeArea"})


def fetch_geocodes(query: str, api_key: str) -> list[dict[str, object]]:
    try:
        response = requests.get(
            GEOCODING_URL,
            params={"q": query, "at": BIAS_POINT, "limit": 8, "apiKey": api_key},
            timeout=(3.0, 20.0),
        )
    except requests.Timeout:
        raise ProviderConnectionError("HERE geocoding timed out") from None
    except requests.RequestException as exc:
        raise ProviderConnectionError(
            f"HERE geocoding connection failed ({type(exc).__name__})"
        ) from None
    if response.status_code == 429:
        try:
            retry = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry = None
        raise ProviderRateLimitError("HERE geocoding rate limit exceeded", retry_after=retry)
    if response.status_code != 200:
        raise ProviderServerError("HERE geocoding is unavailable", status_code=response.status_code)
    try:
        payload: Any = response.json()
    except ValueError:
        raise ProviderServerError("HERE geocoding returned an invalid response") from None
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ProviderServerError("HERE geocoding returned an invalid response")
    found = []
    for item in items:
        if not isinstance(item, dict) or item.get("resultType") not in PLACE_TYPES:
            continue
        position = item.get("position")
        label = item.get("title")
        if (
            isinstance(position, dict)
            and isinstance(label, str)
            and isinstance(position.get("lat"), int | float)
            and isinstance(position.get("lng"), int | float)
        ):
            found.append({"label": label, "lat": position["lat"], "lon": position["lng"]})
        if len(found) == 5:
            break
    return found
