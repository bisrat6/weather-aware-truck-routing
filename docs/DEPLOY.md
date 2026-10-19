# Deploying to Render

## Backend web service

1. Create a Render **Web Service** from this repository.
2. Set the root directory to `backend`.
3. Set the Python version environment variable to `PYTHON_VERSION=3.12.0`.
4. Use build command: `pip install -r requirements.txt`.
5. Use start command: `gunicorn config.wsgi:application --workers 1 --threads 4 --timeout 120`.
6. Add the environment variables below, then deploy.

| Variable | Value |
| --- | --- |
| `DJANGO_SECRET_KEY` | A long, random private value. Required in production. |
| `DJANGO_DEBUG` | `false` |
| `DJANGO_ALLOWED_HOSTS` | Your backend hostnames, comma-separated. Render also adds its hostname through `RENDER_EXTERNAL_HOSTNAME`. |
| `MAPBOX_TOKEN` | Server-side Mapbox token. Never add this to the frontend. |
| `OPEN_METEO_BASE_URL` | Optional Open-Meteo host or proxy base URL; defaults to `https://api.open-meteo.com`. |
| `OPEN_METEO_PROXY_TOKEN` | Optional proxy credential, sent server-side as the `X-Proxy-Token` header. Never add this to the frontend. |
| `FRONTEND_ORIGIN` | Exact static-site origin, for example `https://weather-routing.onrender.com` (no trailing slash). |
| `TRUCK_TIME_FACTOR` | Optional; defaults to `1.1`. |

`RENDER_EXTERNAL_HOSTNAME` is supplied by Render when available and does not need to be set manually.

## Frontend static site

1. Create a Render **Static Site** from this repository with root directory `frontend`.
2. Use build command: `npm ci && npm run build`.
3. Publish directory: `dist`.
4. Set `VITE_API_BASE_URL` to the deployed backend origin, for example `https://weather-routing-api.onrender.com` (no trailing slash).
5. Update the backend's `FRONTEND_ORIGIN` to the exact static-site origin and redeploy the backend.

For local Vite development, leave `VITE_API_BASE_URL` empty. The Vite proxy forwards `/api` to `http://127.0.0.1:8000`.

## Verification checklist

- Open `https://<backend>/api/health` and confirm `{"status":"ok"}`.
- Open the static site and confirm the Analyze button becomes enabled.
- Search for two places, submit a route, and confirm results and the map load.
- Confirm browser requests use the static-site origin only for the page and the configured backend origin for API calls.
- Confirm a cross-origin request from a different site does not receive `Access-Control-Allow-Origin`.
- Confirm error responses are JSON and do not disclose provider URLs, tokens, or exception details.

Render free web services may sleep after 15 minutes without traffic. The frontend checks `/api/health` when it loads, shows a wake-up message after three seconds, and retries until the service responds.
