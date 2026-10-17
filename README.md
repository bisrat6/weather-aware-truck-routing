# Weather-Aware Truck Routing

React + Django app that recommends the safest truck route based on weather,
load weight and travel time.The assessment brief is not included in this repository.

**Status:** the pure-Python risk engine (`backend/risk/`) and routing helpers (`backend/routing/`: Mapbox client, checkpoint sampling, distance check) are implemented.
The Open-Meteo weather client (`backend/weather/`) is implemented too. Ranking, the API endpoints and the frontend are not built yet.

## Development

Requires Python 3.12+ (CI uses 3.12).

```
cd backend
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements-dev.txt
ruff check .
pytest
```

Assumptions made where the brief is ambiguous: `docs/ASSUMPTIONS.md`.

## Recording real Mapbox fixtures

```
cd backend
MAPBOX_TOKEN=pk.... python scripts/record_fixture.py
```

Responses are saved to `backend/tests/fixtures/mapbox_<name>.json` and are picked up by
`tests/routing/test_fixtures.py` automatically.

## Recording a real Open-Meteo fixture

```
cd backend
python scripts/record_weather_fixture.py
```

Needs no key. Saves `backend/tests/fixtures/open_meteo_recorded.json`, which `tests/weather/test_weather_fixtures.py` picks up automatically.

Weather data: [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0). The free tier is for non-commercial use.
