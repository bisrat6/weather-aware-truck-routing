"""Views and safe error handlers that do not belong to a feature."""

from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, JsonResponse
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    renderer_classes,
)
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response


@api_view(["GET"])
@authentication_classes([])  # no auth, no session, no database
@permission_classes([])
@renderer_classes([JSONRenderer])  # no browsable API (it needs templates)
def health(request: Request) -> Response:
    """Liveness check for the hosting platform. Touches nothing but the process."""
    return Response({"status": "ok"})


def json_bad_request(request: HttpRequest, exception: Exception | None = None) -> JsonResponse:
    """Return the documented error envelope for invalid or oversized requests."""
    message = (
        "Request body exceeds maximum allowed size."
        if isinstance(exception, RequestDataTooBig)
        else "The request could not be completed."
    )
    return JsonResponse(
        {"error": {"code": "invalid_request", "message": message}},
        status=400,
    )


def json_forbidden(request: HttpRequest, exception: Exception | None = None) -> JsonResponse:
    """Return the documented error envelope for forbidden access."""
    return JsonResponse(
        {"error": {"code": "forbidden", "message": "Access is forbidden."}},
        status=403,
    )


def json_not_found(request: HttpRequest, exception: Exception | None = None) -> JsonResponse:
    """Return the documented error envelope for URLs outside the API."""
    return JsonResponse(
        {"error": {"code": "not_found", "message": "The requested resource was not found."}},
        status=404,
    )


def json_server_error(request: HttpRequest) -> JsonResponse:
    """Do not expose exception details in production error responses."""
    return JsonResponse(
        {
            "error": {
                "code": "server_error",
                "message": "The server could not complete the request.",
            }
        },
        status=500,
    )
