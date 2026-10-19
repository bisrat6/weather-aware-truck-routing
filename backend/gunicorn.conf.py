"""Gunicorn configuration for production deployment on Render.

Using a single worker process with multiple worker threads ensures that
the in-memory weather grid cache (weather.cache.GridCache) is shared across
concurrent requests rather than duplicated across isolated process memories.
Threads also minimize memory footprint on Render instances.
"""

import os

# Render sets PORT; default to 10000 or 8000
port = os.environ.get("PORT", "10000")
bind = f"0.0.0.0:{port}"

# Single worker with 4 threads for shared in-memory weather cache
workers = 1
threads = 4

# Allow external HERE/Open-Meteo fetches without premature timeouts
timeout = 120

# Log to standard output/error
accesslog = "-"
errorlog = "-"
loglevel = "info"
