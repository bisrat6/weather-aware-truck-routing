"""URL configuration."""

from django.urls import URLPattern, URLResolver, re_path

from api.views import analyze, geocode, heatmap
from config.views import health

handler400 = "config.views.json_bad_request"
handler403 = "config.views.json_forbidden"
handler404 = "config.views.json_not_found"
handler500 = "config.views.json_server_error"

urlpatterns: list[URLPattern | URLResolver] = [
    # Matches /api/health and /api/health/ (no redirect, so health checks never see a 301).
    re_path(r"^api/health/?$", health, name="health"),
    re_path(r"^api/analyze/?$", analyze, name="analyze"),
    re_path(r"^api/geocode/?$", geocode, name="geocode"),
    re_path(r"^api/heatmap/?$", heatmap, name="heatmap"),
]
