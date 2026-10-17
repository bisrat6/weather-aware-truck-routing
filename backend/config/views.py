"""Views that do not belong to a feature."""

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
