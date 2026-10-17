"""Single-origin CORS policy with no cross-origin access by default."""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils.cache import patch_vary_headers


class FrontendCorsMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        origin = request.headers.get("Origin", "")
        allowed = self._allowed_origin()
        if request.method == "OPTIONS" and allowed and origin == allowed:
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)
        if allowed and origin == allowed:
            response["Access-Control-Allow-Origin"] = allowed
            response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type"
            patch_vary_headers(response, ("Origin",))
        return response

    @staticmethod
    def _allowed_origin() -> str:
        from django.conf import settings

        return settings.FRONTEND_ORIGIN
