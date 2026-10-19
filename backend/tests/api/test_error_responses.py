"""Production fallbacks keep unexpected failures credential-safe and JSON-shaped."""

from django.http import HttpRequest, HttpResponse
from django.test import override_settings
from django.urls import path
from rest_framework.test import APIClient


def explode(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("internal details and https://provider.example/token=secret")


urlpatterns = [path("explode", explode)]
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
