# Fixtures

`mapbox_handbuilt_*.json` are **hand-built**, not real Mapbox responses. They follow the real
Directions response shape (`routes[].geometry.coordinates` as `[lon, lat]`,
`routes[].legs[].annotation.{distance,duration}`, `code`, `uuid`) with synthetic numbers.

Real responses are recorded with `scripts/record_fixture.py` into `mapbox_<name>.json`.
`tests/routing/test_fixtures.py` runs every `mapbox_*.json` here through the parser.
