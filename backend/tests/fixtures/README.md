# Fixtures

`mapbox_handbuilt_*.json` are **hand-built**, not real Mapbox responses. They follow the real
Directions response shape (`routes[].geometry.coordinates` as `[lon, lat]`,
`routes[].legs[].annotation.{distance,duration}`, `code`, `uuid`) with synthetic numbers.

Real responses are recorded with `scripts/record_fixture.py` into `mapbox_<name>.json`.
`tests/routing/test_fixtures.py` runs every `mapbox_*.json` here through the parser.

## Weather fixtures

`open_meteo_handbuilt.json` is **hand-built** (not a real Open-Meteo response) in the real
`/v1/forecast` multi-location shape: a JSON list, the first entry without `location_id`,
`mp/h` and `inch` units, 48 hourly values from 2026-10-17T00:00 UTC. Request order:
(40.7, -111.9), (39.7, -105.0), (41.1, -104.8). Contents the tests rely on:

| Location | Hour index | Content |
|---|---|---|
| 0 (Salt Lake City) | all | wind = 5.0 + 0.5 x index, so each hour is identifiable |
| 0 | 20 | rain 0.12 + showers 0.03 |
| 0 | 30 | snowfall 0.6 |
| 1 (Denver) | 5 | wind is null |
| 1 | 10 | showers is null |
| 1 | 30 | snowfall is null |
| 2 (Cheyenne) | 12 | wind 52.3 |
| 2 | 13 | snowfall 2.5 |

Real responses are recorded with `scripts/record_weather_fixture.py` into
`open_meteo_<name>.json`; `tests/weather/test_weather_fixtures.py` picks up every
`open_meteo_*.json` automatically.
