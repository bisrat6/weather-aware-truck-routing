"""URL configuration."""

from django.urls import URLPattern, URLResolver, re_path

from api.views import analyze, geocode
from config.views import health

urlpatterns: list[URLPattern | URLResolver] = [
    # Matches /api/health and /api/health/ (no redirect, so health checks never see a 301).
    re_path(r"^api/health/?$", health, name="health"),
    re_path(r"^api/analyze/?$", analyze, name="analyze"),
    re_path(r"^api/geocode/?$", geocode, name="geocode"),
]
