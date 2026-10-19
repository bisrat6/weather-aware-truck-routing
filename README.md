# RouteCast — Weather-Aware Truck Routing

React + Django app that recommends the safest truck route based on weather conditions,
load weight, and travel time.

**Status:** Fully implemented. The pure-Python risk engine (`backend/risk/`), routing helpers (`backend/routing/`: HERE client, geodesic checkpoint sampling, Douglas-Peucker simplification), Open-Meteo weather client with grid caching (`backend/weather/`), lexicographic route ranking (`backend/analysis/`), REST API endpoints (`/api/analyze`, `/api/geocode`, `/api/health`, `/api/heatmap`), and the RouteCast interactive MapLibre frontend are all complete and fully tested.

## Development

### Backend (Django)

Requires Python 3.12+ (CI uses 3.12).

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements-dev.txt
ruff check .
pytest
python manage.py runserver
```

### Frontend (React + Vite + MapLibre GL)

Requires Node.js 18+.

```bash
cd frontend
npm install
npm run typecheck
npm run build
npm run dev
```

Visit `http://localhost:5173/` for live routing or `http://localhost:5173/?demo=sample` to load a pre-computed sample trip without external API calls.

### Documentation

- HTTP API specifications: `docs/API.md`
- Assumptions and risk calculation rules: `docs/ASSUMPTIONS.md`
- Deployment instructions (Render): `docs/DEPLOY.md`

## Recording real HERE fixtures

```
cd backend
HERE_API_KEY=... python scripts/record_fixture.py --standard
```

Responses are saved to `backend/tests/fixtures/here_<name>.json` and are picked up by
`tests/routing/test_fixtures.py` automatically.

## Recording a real Open-Meteo fixture

```
cd backend
python scripts/record_weather_fixture.py
```

Needs no key. Saves `backend/tests/fixtures/open_meteo_recorded.json`, which `tests/weather/test_weather_fixtures.py` picks up automatically.

Weather data: [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0). The free tier is for non-commercial use.
