"""Django settings. Secrets come from the environment; see .env.example."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str) -> bool:
    """True only for an explicit truthy value; anything else (or unset) is False."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


# Safe by default: DEBUG is off unless DJANGO_DEBUG is explicitly truthy.
DEBUG = _env_bool("DJANGO_DEBUG")

# The dev key below is public (it is in the repo), so it is only ever used when DEBUG is on.
# Outside DEBUG a real key is required and startup fails without one.
_DEV_SECRET_KEY = "insecure-dev-only-key"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is not set. Set it, or set DJANGO_DEBUG=true for local development."
        )
    SECRET_KEY = _DEV_SECRET_KEY

ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS")
# Render sets RENDER_EXTERNAL_HOSTNAME to the service's public hostname automatically.
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
if _render_host and _render_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_render_host)
if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",  # DRF imports auth models even though no user model is used
    "django.contrib.staticfiles",
    "rest_framework",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.common.CommonMiddleware",
    "api.middleware.FrontendCorsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# No models in this project.
DATABASES: dict[str, dict[str, str]] = {}

# Multiplier applied to provider drive times to approximate a loaded truck.
TRUCK_TIME_FACTOR = float(os.environ.get("TRUCK_TIME_FACTOR", "1.1"))
MAPBOX_TOKEN = os.environ.get("MAPBOX_TOKEN", "")
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "").strip().rstrip("/")

REST_FRAMEWORK = {"EXCEPTION_HANDLER": "api.errors.exception_handler"}

USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
