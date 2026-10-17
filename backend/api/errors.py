"""Consistent, credential-safe API error responses."""

from __future__ import annotations

from typing import Any

from rest_framework.exceptions import APIException, ParseError, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class APIError(APIException):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        self.default_code = code
        self.status_code = status_code
        super().__init__(detail=message, code=code)


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, APIException):
        code = (
            "invalid_request"
            if isinstance(exc, ValidationError | ParseError)
            else str(getattr(exc, "default_code", "request_error"))
        )
        detail = exc.detail
        if isinstance(detail, dict):
            parts = []
            for key, value in detail.items():
                values = value if isinstance(value, list) else [value]
                parts.append(f"{key}: {', '.join(map(str, values))}")
            message = "; ".join(parts)
        elif isinstance(detail, list):
            message = "; ".join(str(item) for item in detail)
        else:
            message = str(detail)
    else:
        code = "request_error"
        message = "The request could not be completed."
    response.data = {"error": {"code": code, "message": message}}
    return response
