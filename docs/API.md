# HTTP API

All endpoints are served by Django under `/api`. Requests and responses use JSON, except
`GET /api/geocode`, which uses query parameters. Error responses have this shape:

```json
{"error": {"code": "invalid_request", "message": "A clear explanation."}}
```

All errors—including unknown paths (404), unhandled exceptions (500), and oversized request bodies (400)—return
this standard JSON error shape without stack traces, tokens, or upstream URLs.
Responses with `Accept-Encoding: gzip` exceeding 200 bytes are compressed with GZip (`Content-Encoding: gzip`).
Rate-limit errors keep the same shape and include a retry hint in `message` when the provider
supplies one. Error messages do not include upstream URLs or credentials.

## `POST /api/analyze`

Request body:

```json
{
  "origin": {"lat": 40.7128, "lon": -74.0060},
  "destination": {"lat": 34.0522, "lon": -118.2437},
  "departure_utc": "2026-10-18T12:00:00Z",
  "load_lb": 34000,
  "interval_miles": 25
}
```

`origin` and `destination` are required WGS84 coordinate objects. `departure_utc` must be
an ISO 8601 UTC timestamp ending in `Z` or `+00:00`. `load_lb` is required and must be in
the inclusive range 0–100,000 lb. `interval_miles` is optional and must be 10, 25, or 50;
the default is 25. Origin and destination must differ. The accepted departure window begins
at 00:00 UTC today (the weather service's earliest usable hour) and ends 7 days from the
current UTC time.

Response body:

```json
{
  "routes": [
    {
      "route_index": 0,
      "origin_label": "provider alternative",
      "geometry": {"coordinates": [[-74.0, 40.7], [-73.9, 40.8]]},
      "distance_mi": 12.4,
      "duration_s": 1500,
      "eta_method": "annotations",
      "checkpoints": [
        {
          "index": 0,
          "lat": 40.7,
          "lon": -74.0,
          "dist_mi": 0.0,
          "eta_utc": "2026-10-18T12:00:00Z",
          "weather": {"wind_mph": 10.0, "rain_in_hr": 0.0, "snow_in_hr": 0.0},
          "risk_level": "Low",
          "risk_level_minus_2h": "Low",
          "risk_level_plus_2h": "Low",
          "unstable": false,
          "reasons": [],
          "owned_mi": 6.2,
          "weather_missing": false,
          "missing_reason": null
        }
      ],
      "summary": {
        "miles_by_level": {"Low": 12.4, "Moderate": 0, "High": 0, "Severe": 0, "No Travel": 0},
        "missing_weather_miles": 0,
        "max_level": "Low",
        "average_risk": 0,
        "duration_s": 1500,
        "eta_method": "annotations",
        "missing_weather_count": 0,
        "unstable_checkpoints": 0,
        "unknown_stability_checkpoints": 0
      }
    }
  ],
  "ranking": [0],
  "recommended": 0,
  "recommendation_reason": "Only route available",
  "notices": []
}
```

Each route's `geometry` uses the GeoJSON object shape `{"coordinates": [[longitude, latitude], ...]}`.
Returned route geometry is simplified for display using the Douglas-Peucker algorithm with a default
tolerance of 35 m (endpoints preserved exact), cutting payload size and render cost by over 90%.
All internal route calculations (distances, durations, checkpoint placement, ETAs, risk scoring,
and ranking) strictly continue to use the full-detail provider geometry.

Geometry coordinates are GeoJSON `[longitude, latitude]`. `ranking` lists route indices in
recommended-first order; `recommended` is the first route index. `risk_level` and `max_level`
are `null` when no level can be assessed. Missing weather is never converted to Low. The
`weather` object is `null` for a missing checkpoint. The summary risk buckets plus
`missing_weather_miles` account for all checkpoint-owned miles.
`risk_level_minus_2h` and `risk_level_plus_2h` are the risk levels after shifting the
checkpoint ETA by two hours earlier or later. `unstable` is true if any assessed shifted level
differs from the base level (even when the other shift is missing), false only when both shifted
levels were assessed and match the base, and `null` when no flip is known and a shifted level
(or the base) cannot be assessed because weather is missing or outside the forecast window. Summary
counts include true flags in `unstable_checkpoints` and unknown flags in
`unknown_stability_checkpoints`. Stability does not affect route ranking or recommendation.
`origin_label` is one of `"provider alternative"`, `"avoiding motorways"`,
`"forced detour, left of the main route"` or `"forced detour, right of the main route"`
(left and right are relative to the direction of travel; no place names).
When the provider returns fewer than three routes, the server makes up to 5 extra Mapbox
requests to look for a third acceptable route (see ASSUMPTIONS.md, "Third-route fallback").
Extra routes use the same weather, scoring and ranking as provider routes. If fewer than three
routes remain, `notices` includes `"Only N routes available (the provider returned M; the
detour search found K acceptable)"`. No synthetic route is added, and a fallback failure,
timeout or rate limit never fails the request. No extra requests are made when the provider
returns three routes.
`notices` also includes a plain-language warning if the recommended route contains any
No Travel checkpoint or has missing weather data; the latter gives missing miles and
checkpoint count. `recommendation_reason` names the criterion that decided the ranking
and includes the rounded duration difference when it is nonzero.

Validation errors return HTTP 400. A Mapbox Directions response indicating no driving route
(`NoRoute` or `NoSegment`, including an HTTP 200 response with a no-route code or a successful
response with no routes) returns HTTP 422 with code `no_route_found` and message
`No driving route found between these places.` Other invalid, unavailable, or expired Mapbox
route requests return HTTP 502, except Mapbox HTTP 422 responses with code `InvalidInput`.
Those return HTTP 422 with code `route_too_far` and message
`These places are too far apart for a driving route.` when the provider message mentions
"maximum distance"; other `InvalidInput` responses return HTTP 422 with code
`route_not_supported` and message `A driving route could not be built between these places.`
Provider message text is never returned to the client. Weather service failures return HTTP
502; rate limits return HTTP 429 and include a retry hint if one is supplied. Missing weather
at individual checkpoints remains in the successful response as `weather_missing: true`. An
unset `MAPBOX_TOKEN` returns HTTP 503 with a configuration error.

## `GET /api/geocode?q=`

Requires a non-empty `q` of at least 2 characters. Uses Mapbox Geocoding with the same
server-side `MAPBOX_TOKEN`; returns up to 5 results.

```json
{"results": [{"label": "Salt Lake City, Utah", "lat": 40.76, "lon": -111.89}]}
```

Provider errors use the standard error shape and never expose the token or upstream URL.

## `POST /api/heatmap`

Request body:

```json
{
  "geometry": {"coordinates": [[-74.0060, 40.7128], [-73.8, 40.5]]},
  "departure_utc": "2026-10-18T12:00:00Z",
  "load_lb": 34000
}
```

`geometry` must use the same GeoJSON object shape returned by `/api/analyze`, with an
ordered `coordinates` list of `[longitude, latitude]` WGS84 pairs. At least two and at most
40,000 points are accepted; this accommodates both raw unsimplified routes (such as the recorded
34,997-point Los Angeles–New York route, about 808 KB) and simplified routes (which typically contain
~2,000–3,500 points) while bounding request size.
`departure_utc` and `load_lb` use the same
validation window and inclusive 0–100,000 lb range as `/api/analyze`. The service samples
along the corridor and caps the output at 150 points. Hours are 49 consecutive UTC forecast hours;
hour 0 is the nearest forecast hour to `departure_utc`, using the same nearest-hour rule
as route checkpoint ETAs.

```json
{
  "hours": ["2026-10-18T12:00:00Z", "2026-10-18T13:00:00Z"],
  "points": [{"lat": 40.7, "lon": -74.0, "risk": [0, 1, null]}]
}
```

Each risk entry is an integer from 0 (Low) through 4 (No Travel), or `null` when any
required weather variable is missing for that point and hour. Missing weather is never
encoded as Low. Weather is fetched through the shared grid cache and risk is scored with
the same load-aware risk engine as route checkpoints. Validation and upstream errors use
the standard error shape above; provider details, URLs and credentials are sanitized.

## `GET /api/health`

Liveness response:

```json
{"status": "ok"}
```

## CORS and configuration

`FRONTEND_ORIGIN` sets the one frontend origin allowed by CORS. It defaults to empty, which
allows no cross-origin browser access. `MAPBOX_TOKEN` stays server-side. No endpoint uses a
database.
