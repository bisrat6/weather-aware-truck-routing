"""GET /api/health.

None of these tests use the `django_db` marker, so pytest-django would fail them if the
endpoint touched the database (the project has none configured).
"""

import json

import pytest
from django.test.client import RequestFactory
from rest_framework.test import APIClient

from config.wsgi import application


@pytest.fixture
def client():
    return APIClient()


def test_health_returns_ok_json(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response["Content-Type"].startswith("application/json")


def test_health_accepts_trailing_slash_without_redirect(client):
    response = client.get("/api/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_needs_no_authentication_or_session(client):
    response = client.get("/api/health")  # no credentials, no cookies
    assert response.status_code == 200
    assert "sessionid" not in response.cookies


def test_health_with_browser_style_accept_header_is_still_json(client):
    # A browser sends text/html first; the browsable API would need templates we don't have.
    response = client.get("/api/health", HTTP_ACCEPT="text/html,application/xhtml+xml,*/*;q=0.8")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_health_rejects_non_get_methods(client, method):
    assert getattr(client, method)("/api/health").status_code == 405


def test_unknown_api_path_is_404(client):
    assert client.get("/api/nope").status_code == 404


def test_wsgi_application_serves_health():
    """The Render start command runs config.wsgi:application; exercise exactly that object."""
    environ = RequestFactory().get("/api/health").environ
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(application(environ, start_response))
    assert captured["status"].startswith("200")
    assert captured["headers"]["Content-Type"].startswith("application/json")
    assert json.loads(body) == {"status": "ok"}
