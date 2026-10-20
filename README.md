# Weather-Aware Truck Routing

Recommends the safest truck route between two North American locations by combining turn-by-turn routing with hourly weather forecasts sampled at route checkpoints. Route options are scored against configurable risk thresholds and truck load rules, with an interactive corridor weather heatmap across a 48-hour departure window.

### Try It
- Live application: https://weather-aware-truck-routing-1-og3f.onrender.com
- Hosting note: The free Render web service spins down after 15 minutes of inactivity. Initial page load and first query can take up to 60 seconds while the backend wakes up (the UI displays a "waking up" notice during startup).
- TODO: screenshots (route comparison view, corridor heatmap with 0-48 h time slider, checkpoint risk popup).

## Requirements Matrix

| Requirement | Status | Where (file or module) |
|---|---|---|
| Inputs (origin, destination, departure, load, interval) | Done | `backend/api/validation.py`, `frontend/src/PlacePicker.tsx` |
| 3 route options | Partial | `backend/routing/here.py` (fallback spec in `backend/routing/fallback.py`) |
| Weather every 10/25/50 miles at ETA | Done | `backend/routing/checkpoints.py`, `backend/weather/service.py` |
| Risk rules and load rules | Done | `backend/risk/engine.py` |
| Recommendation by the four criteria | Done | `backend/analysis/scoring.py` |
| Map with checkpoints, risk and ETAs | Done | `frontend/src/RouteMap.tsx` |
| Heatmap with 0-48 h slider | Done | `backend/api/heatmap.py`, `frontend/src/App.tsx` |
| Hosted app | Done | Render (Web Service + Static Site; see `docs/DEPLOY.md`) |
| GitHub repo | Done | Repository root, `.github/workflows/ci.yml` |

For route options, routing providers commonly return only 2 alternatives. The fallback algorithm targets 3 routes by attempting an motorway-avoiding request followed by perpendicular via-detours (left/right at 8% and 16% of total route distance, lateral offset clamped to 1.5–35.0 miles). Extra requests are capped at 5 with a 6.0 s request timeout and an 8.0 s overall search budget. Candidates are rejected if >= 85% of their 0.01-degree grid cells overlap existing routes or if duration or distance exceeds 1.5x the best route. Each route retains its origin label, and the API returns a notice whenever fewer than 3 routes pass checks. A third valid route is not guaranteed.

## How It Works

Route timing generates checkpoints at 10, 25, or 50-mile intervals. Each checkpoint ETA is calculated from cumulative span durations multiplied by `TRUCK_TIME_FACTOR` (default `1.0` in settings). Checkpoint weather conditions are evaluated against risk bands and load escalation rules:

| Level | Wind (mph) | Rain (in/hr) | Snow (in/hr) | Load Rule Escalation |
|---|---|---|---|---|
| Low | < 25 | < 0.10 | < 0.5 | Base wind |
| Moderate | [25, 35) | [0.10, 0.25) | [0.5, 1.0) | None |
| High | [35, 45) | [0.25, 0.50) | [1.0, 2.0) | None |
| Severe | [45, 55) | [0.50, 1.00] | [2.0, 3.0] | Wind [35, 45) and load > 40,000 lb |
| No Travel | >= 55 | > 1.00 | > 3.0 | Wind [45, 55) and load > 30,000 lb |

Each checkpoint owns half the distance to its adjacent neighbors (`(left + right) / 2`); endpoints own half the first or last interval. Route miles in each risk level equal the sum of owned checkpoint miles.

Routes are ranked lexicographically:
1. Blocked routes (any checkpoint with No Travel risk) are relegated to rank last.
2. Routes with incomplete weather rank after complete routes, but before blocked routes.
3. No Travel miles (rounded to nearest whole mile).
4. Severe risk miles (rounded to nearest whole mile).
5. High risk miles (rounded to nearest whole mile).
6. Average risk score on a 0–4 scale (rounded to 2 decimal places).
7. Travel duration (rounded to nearest whole minute).

Checkpoint weather is also sampled at ETA +/- 2 hours. If risk differs at either offset, an ETA instability flag is set. The corridor heatmap is fetched on demand for the currently selected route only, sampling up to 150 grid-deduplicated points across 49 forecast hours (0–48 h).

## Architecture

```text
[ Browser: React 19 + MapLibre GL ]
         │               │
  Static Assets     JSON API (/api/analyze, /api/geocode, /api/heatmap)
         │               ▼
  [ Render CDN ]  [ Django API (Gunicorn + GZip Middleware) ]
                         │               │
           Routing & Geocoding      Weather Queries
                         │               ▼
               [ Routing Provider ]  [ Cloudflare Worker Proxy ]
                                         │ (Rate limit & IP protection)
                                         ▼
                                   [ Open-Meteo API ]
                                         │
                             [ In-Memory GridCache (0.1°) ]
```

Open-Meteo requests pass through a Cloudflare Worker proxy. Open-Meteo free tier imposes concurrency limits per source IP, and shared cloud hosting IP pools (such as Render) frequently exhaust this quota without a dedicated egress proxy. The Django process caches weather samples in an in-memory 0.1-degree grid cache (45-minute TTL) across threads. See [docs/API.md](docs/API.md) and [docs/DEPLOY.md](docs/DEPLOY.md).

## Run It Locally

### Prerequisites
- Python 3.12+
- Node.js 18+

### Backend
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows; source .venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt
python manage.py runserver
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Environment Variables
- `DJANGO_SECRET_KEY`: Secret key for Django cryptographic signing; required unless `DJANGO_DEBUG=true`.
- `DJANGO_DEBUG`: Set to `true` for local development; enables debug endpoints and fallback dev key.
- `DJANGO_ALLOWED_HOSTS`: Comma-separated list of hostnames allowed to connect to the backend.
- `HERE_API_KEY`: Server-side API key for routing and autosuggest geocoding.
- `OPEN_METEO_BASE_URL`: Base URL for Open-Meteo forecasts or proxy endpoint.
- `OPEN_METEO_PROXY_TOKEN`: Optional authentication token passed to the weather proxy.
- `FRONTEND_ORIGIN`: Allowed browser origin for CORS headers; empty disables cross-origin requests.
- `RENDER_EXTERNAL_HOSTNAME`: Injected by Render to append service hostname to allowed hosts.
- `TRUCK_TIME_FACTOR`: Duration multiplier on route segments to reflect heavy vehicle travel time.
- `VITE_API_BASE_URL`: Base URL for frontend API requests; empty string uses the Vite dev proxy.

### Tests and Linters
```bash
# Backend tests and linting
cd backend
pytest
ruff check .

# Frontend typechecking and production build
cd frontend
npm run typecheck
npm run build
```

## Decisions and Assumptions

Detailed rationale is recorded in [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md). Key decisions affecting results:
- **Rain 0.25 boundary**: Shared table edges go to the higher band (rain 0.25/0.50, snow 1.0/2.0) as the safer reading of adjacent table columns.
- **Load weight used only in risk rules**: Provider receives no vehicle dimensions/weight and applies legal rather than physical truck limits; load is used strictly for weather risk escalation.
- **Blocked routes rank last**: Any route with a No Travel checkpoint is unsafe and sorted after all unblocked routes regardless of total duration or other miles.
- **Missing weather never treated as Low**: Unresolved checkpoint miles stay unassigned and excluded from risk averages rather than assumed safe.
- **Sustained wind not gusts**: 10 m sustained wind is requested from Open-Meteo (`wind_speed_10m`) as specified in the project guide; gusts are omitted.
- **Per-route heatmap**: Heatmap corridor samples only the currently active selected route up to 150 points along the path for 49 forecast hours (0–48 h).
- **Half-to-even rounding**: Ranking uses standard Python `round()` (half-to-even) for bucketed miles, average risk (2 decimals), and duration minutes to avoid upward bias.

## Verification

- **Test Suite**: 650 passed unit and property tests executed via `pytest`.
- **Risk Engine Coverage**: Float boundaries evaluated with `math.nextafter` for adjacent float transitions, load-rule cutoffs at 30,000 / 40,000 lb, and Hypothesis property tests enforcing monotonicity and invariant rules.
- **Differential Check**: A differential test against a second implementation written from the brief, with a harness validated by deliberately wrong variants. The second implementation was written by the same AI assistant that wrote the engine, so it checks the code against one reading of the brief, not the reading itself.
- **Continuous Integration**: GitHub Actions workflow (`.github/workflows/ci.yml`) runs linting (`ruff check .`) and the full test suite (`pytest`) on Python 3.12 for every push and pull request.

## Performance

| Metric | Target / Observed |
|---|---|
| Hosted cold `/api/analyze` time | TODO |
| Hosted warm `/api/analyze` time | TODO |
| Response size gzip vs uncompressed | TODO |
| Slider update time per frame (`?perf=1`) | TODO |
| DevTools frame time while dragging slider | TODO |
| Lighthouse performance score | TODO |
| JS bundle size (main chunk) | 1,359.61 kB (gzip: 379.39 kB) |

Measurement instructions:
- Hosted cold `/api/analyze`: Measure wall-clock duration of first POST request after 15 minutes of inactivity.
- Hosted warm `/api/analyze`: Measure wall-clock duration of repeated POST request with identical coordinates.
- Response size gzip vs uncompressed: Inspect `/api/analyze` network entry headers (`Content-Length` vs decoded size).
- Slider update time per frame: Load app with `?perf=1` query parameter and observe overlay console timing metrics.
- DevTools frame time: Record a Chrome Performance profile while continuously dragging the 0–48 h slider.
- Lighthouse performance score: Run Lighthouse audit in Chrome DevTools on production static deployment.

## Limits and Known Issues

- **Route Availability**: Routing providers generally return 2 routes; fallback detours may fail similarity (>= 85%) or distance/duration (>= 1.5x) filters, yielding fewer than 3 options.
- **Cold Start Delay**: Free-tier backend hosting on Render idles after 15 minutes, causing a 30–60 second delay on initial load.
- **Open-Meteo Rate Limits & Overload**: The free API tier is non-commercial. The backend retries HTTP 5xx responses and responses with "overloaded" in body up to 2 times (1 s then 2 s backoff within a 6 s budget); if exhausted, it returns HTTP 502/429.
- **Attribution**: Weather data provided by [Open-Meteo.com](https://open-meteo.com/) under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- **Vehicle Profile**: Uses standard truck routing mode with `TRUCK_TIME_FACTOR` (default 1.0); physical clearance restrictions (bridge heights, gross weight limits) are not evaluated.
- **No Road Closures**: Live emergency road closures or active incident blockages are not integrated into route calculations.
- **Forecast Sampling**: Forecast models update periodically; checkpoints sample discrete hourly point values rather than continuous spatial corridors.
- **Frontend Test Automation**: The frontend has no automated component or end-to-end test suite; verification relies on TypeScript strict typechecking and build validation.
- **Bundle Size**: MapLibre GL and vector fonts produce a minified JavaScript bundle exceeding 1.3 MB (379 kB gzipped).

## Not Verified / Future Work

- Mapbox terms of service regarding display of route geometries on non-Mapbox map renderers were not reviewed.
- Real-weather behaviour under extreme conditions (No Travel, severe snow) was exercised in tests and dev demo mode (`?demo=levels`), but has not been verified against live severe weather events.
- Active provider in current codebase uses HERE Routing v8 truck mode and Autosuggest, whereas earlier revisions and certain brief prompts reference Mapbox Directions and Geocoding.

## Credits and Licences

- Weather data by [Open-Meteo.com](https://open-meteo.com/) ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).
- Map tiles by [OpenFreeMap](https://openfreemap.org/) and map data by [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.
- Map rendering powered by [MapLibre GL](https://maplibre.org/).
- Routing and geocoding services by [HERE Technologies](https://www.here.com/) / [Mapbox](https://www.mapbox.com/).
