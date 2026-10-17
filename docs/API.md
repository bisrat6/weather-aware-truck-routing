# HTTP API

All endpoints are served by Django under `/api`. Requests and responses use JSON, except
`GET /api/geocode`, which uses query parameters. Error responses have this shape:

```json
{"error": {"code": "invalid_request", "message": "A clear explanation."}}
```

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
        "missing_weather_count": 0
      }
    }
  ],
  "ranking": [0],
  "recommended": 0,
  "recommendation_reason": "Only route available",
  "notices": []
}
```

Geometry coordinates are GeoJSON `[longitude, latitude]`. `ranking` lists route indices in
recommended-first order; `recommended` is the first route index. `risk_level` and `max_level`
are `null` when no level can be assessed. Missing weather is never converted to Low. The
`weather` object is `null` for a missing checkpoint. The summary risk buckets plus
`missing_weather_miles` account for all checkpoint-owned miles.
When fewer than three routes are returned, `notices` includes
`"Only N routes available from the provider"`. No synthetic route is added.

Validation errors return HTTP 400. Invalid, unavailable, or expired Mapbox route requests
return HTTP 502. Weather service failures return HTTP 502; rate limits return HTTP 429 and
include a retry hint if one is supplied. Missing weather at individual checkpoints remains
in the successful response as `weather_missing: true`.

## `GET /api/geocode?q=`

Requires a non-empty `q` of at least 2 characters. Uses Mapbox Geocoding with the same
server-side `MAPBOX_TOKEN`; returns up to 5 results.

```json
{"results": [{"label": "Salt Lake City, Utah", "lat": 40.76, "lon": -111.89}]}
```

Provider errors use the standard error shape and never expose the token or upstream URL.

## `GET /api/health`

Liveness response:

```json
{"status": "ok"}
```

## CORS and configuration

`FRONTEND_ORIGIN` sets the one frontend origin allowed by CORS. It defaults to empty, which
allows no cross-origin browser access. `MAPBOX_TOKEN` stays server-side. No endpoint uses a
database.
