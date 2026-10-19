"""JSON API endpoints for route analysis and HERE geocoding."""

from __future__ import annotations

import logging
from datetime import UTC

from django.conf import settings
from django.utils import timezone
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    renderer_classes,
)
from rest_framework.exceptions import ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from analysis import analyze_route, rank_routes
from api.errors import APIError
from api.geocoding import fetch_geocodes
from api.heatmap import build_heatmap
from api.serializers import serialize_route
from api.validation import (
    AnalyzeRequestSerializer,
    HeatmapRequestSerializer,
    validate_geocode_query,
)
from routing.errors import ProviderError, ProviderNoRouteError, ProviderRateLimitError
from routing.here import get_routes
from weather.errors import WeatherError, WeatherRateLimitError
from weather.service import WeatherService

logger = logging.getLogger(__name__)

_weather_service = WeatherService(
    base_url=settings.OPEN_METEO_BASE_URL,
    proxy_token=settings.OPEN_METEO_PROXY_TOKEN,
)


def _retry_hint(seconds: float | None) -> str:
    return f" Retry after {seconds:g} seconds." if seconds is not None else ""


@api_view(["POST"])
@authentication_classes([])
@permission_classes([])
@renderer_classes([JSONRenderer])
def analyze(request: Request) -> Response:
    serializer = AnalyzeRequestSerializer(
        data=request.data, context={"now": timezone.now().astimezone(UTC)}
    )
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if not settings.HERE_API_KEY:
        raise APIError("routing_unavailable", "Route service is not configured.", 503)
    origin = (data["origin"]["lat"], data["origin"]["lon"])
    destination = (data["destination"]["lat"], data["destination"]["lon"])
    try:
        routes = get_routes(
            origin,
            destination,
            settings.HERE_API_KEY,
            truck_time_factor=settings.TRUCK_TIME_FACTOR,
        )
    except ProviderRateLimitError as exc:
        raise APIError(
            "rate_limited", "Route service rate limit exceeded." + _retry_hint(exc.retry_after), 429
        ) from None
    except ProviderNoRouteError:
        raise APIError(
            "no_route_found",
            "No driving route found between these places.",
            422,
        ) from None
    except ProviderError as exc:
        if exc.status_code in (400, 422):
            raise APIError(
                "route_not_supported",
                "A driving route could not be built between these places.",
                422,
            ) from None
        raise APIError("routing_unavailable", "Route service is unavailable.", 502) from None

    try:
        results = [
            analyze_route(
                route,
                data["departure_utc"],
                float(data["interval_miles"]),
                data["load_lb"],
                _weather_service,
            )
            for route in routes
        ]
    except WeatherRateLimitError as exc:
        raise APIError(
            "rate_limited",
            "Weather service rate limit exceeded." + _retry_hint(exc.retry_after),
            429,
        ) from None
    except WeatherError as exc:
        logger.warning(
            "weather_unavailable returned endpoint=analyze exception=%s", type(exc).__name__
        )
        raise APIError("weather_unavailable", "Weather service is unavailable.", 502) from None

    ranking = rank_routes(results)
    route_indices = {id(result): index for index, result in enumerate(results)}
    notices = []
    if len(routes) < 3:
        notices.append(f"Only {len(routes)} routes available from the routing provider")
    recommended_summary = ranking.recommended.summary
    if recommended_summary.blocked:
        notices.append("Recommended route is blocked because it contains a No Travel checkpoint")
    if recommended_summary.incomplete:
        notices.append(
            "Recommended route has incomplete weather data: "
            f"{recommended_summary.missing_weather_miles:.1f} miles across "
            f"{recommended_summary.missing_weather_count} checkpoints"
        )
    return Response(
        {
            "routes": [
                serialize_route(i, route, result)
                for i, (route, result) in enumerate(zip(routes, results, strict=True))
            ],
            "ranking": [route_indices[id(result)] for result in ranking.ranked_results],
            "recommended": route_indices[id(ranking.recommended)],
            "recommendation_reason": ranking.reason,
            "notices": notices,
        }
    )


@api_view(["GET"])
@authentication_classes([])
@permission_classes([])
@renderer_classes([JSONRenderer])
def geocode(request: Request) -> Response:
    try:
        query = validate_geocode_query(request.query_params.get("q"))
    except ValidationError as exc:
        raise APIError("invalid_request", "; ".join(map(str, exc.detail)), 400) from None
    if not settings.HERE_API_KEY:
        raise APIError("geocoding_unavailable", "Geocoding service is not configured.", 503)
    try:
        results = fetch_geocodes(query, settings.HERE_API_KEY)
    except ProviderRateLimitError as exc:
        raise APIError(
            "rate_limited",
            "Geocoding service rate limit exceeded." + _retry_hint(exc.retry_after),
            429,
        ) from None
    except ProviderError:
        raise APIError("geocoding_unavailable", "Geocoding service is unavailable.", 502) from None
    return Response({"results": results})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([])
@renderer_classes([JSONRenderer])
def heatmap(request: Request) -> Response:
    serializer = HeatmapRequestSerializer(
        data=request.data, context={"now": timezone.now().astimezone(UTC)}
    )
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    try:
        result = build_heatmap(
            data["geometry"], data["departure_utc"], data["load_lb"], _weather_service
        )
    except WeatherRateLimitError as exc:
        raise APIError(
            "rate_limited",
            "Weather service rate limit exceeded." + _retry_hint(exc.retry_after),
            429,
        ) from None
    except WeatherError as exc:
        logger.warning(
            "weather_unavailable returned endpoint=heatmap exception=%s", type(exc).__name__
        )
        raise APIError("weather_unavailable", "Weather service is unavailable.", 502) from None
    return Response(result)
