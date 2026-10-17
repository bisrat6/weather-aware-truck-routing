# Weather-Aware Truck Routing

React + Django app that recommends the safest truck route based on weather,
load weight and travel time. The brief is in `docs/brief.pdf`.

**Status:** only the pure-Python risk engine (`backend/risk/`) is implemented.
Routing, weather fetching, the API and the frontend are not built yet.

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
