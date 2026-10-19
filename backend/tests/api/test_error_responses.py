"""Production fallbacks keep unexpected failures credential-safe and JSON-shaped."""

from django.http import HttpRequest, HttpResponse
from django.test import override_settings
from django.urls import path
from rest_framework.test import APIClient


def explode(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("internal details and https://provider.example/token=secret")


urlpatterns = [path("explode", explode)]
handler400 = "config.views.json_bad_request"
handler404 = "config.views.json_not_found"
handler500 = "config.views.json_server_error"


@override_settings(DEBUG=False, ROOT_URLCONF="tests.api.test_error_responses")
def test_unhandled_error_is_sanitized_json():
    client = APIClient()
    client.raise_request_exception = False

    response = client.get("/explode")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "server_error", "message": "The server could not complete the request."}
    }
    assert response["Content-Type"].startswith("application/json")
    assert "provider.example" not in response.content.decode()
    assert "secret" not in response.content.decode()


@override_settings(DEBUG=False, ROOT_URLCONF="config.urls")
def test_unknown_path_returns_json_404():
    client = APIClient()
    response = client.get("/this-route-does-not-exist")

    assert response.status_code == 404
    assert response["Content-Type"].startswith("application/json")
    assert response.json() == {
        "error": {"code": "not_found", "message": "The requested resource was not found."}
    }


@override_settings(
    DEBUG=False,
    ROOT_URLCONF="config.urls",
    DATA_UPLOAD_MAX_MEMORY_SIZE=500,
    ALLOWED_HOSTS=["testserver"],
)
def test_oversized_request_body_returns_json_400():
    client = APIClient()
    secret = "do-not-leak https://secret.example/token=abc"
    oversized_data = {"text": "a" * 1000, "secret": secret}

    response = client.post("/api/analyze", data=oversized_data, format="json")

    assert response.status_code == 400
    assert response["Content-Type"].startswith("application/json")
    data = response.json()
    assert data["error"]["code"] == "invalid_request"
    assert "Request body exceeds maximum allowed size." in data["error"]["message"]
    assert secret not in response.content.decode()
    assert "secret.example" not in response.content.decode()

