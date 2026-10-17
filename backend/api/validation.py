"""Request validation for the public API."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from rest_framework import serializers


class CoordinateSerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lon = serializers.FloatField(min_value=-180, max_value=180)


class AnalyzeRequestSerializer(serializers.Serializer):
    origin = CoordinateSerializer()
    destination = CoordinateSerializer()
    departure_utc = serializers.CharField()
    load_lb = serializers.FloatField(min_value=0, max_value=100_000)
    interval_miles = serializers.ChoiceField(choices=(10, 25, 50), required=False, default=25)

    def validate_load_lb(self, value: float) -> float:
        if not math.isfinite(value):
            raise serializers.ValidationError("Must be a finite number between 0 and 100000 lb.")
        return value

    def validate_departure_utc(self, value: str) -> datetime:
        text = value.strip()
        if not text.endswith(("Z", "+00:00")):
            raise serializers.ValidationError("Must be an ISO 8601 timestamp in UTC (Z or +00:00).")
        try:
            parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        except ValueError:
            raise serializers.ValidationError("Must be a valid ISO 8601 UTC timestamp.") from None
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise serializers.ValidationError("Must be an ISO 8601 timestamp in UTC.")
        return parsed.astimezone(UTC)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["origin"] == attrs["destination"]:
            raise serializers.ValidationError({"destination": "Must differ from origin."})
        departure = attrs["departure_utc"]
        now = self.context["now"]
        earliest = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if departure < earliest:
            raise serializers.ValidationError(
                {"departure_utc": "Is before the earliest hour available from the weather source."}
            )
        if departure > now + timedelta(days=7):
            raise serializers.ValidationError(
                {"departure_utc": "Must be no more than 7 days ahead."}
            )
        return attrs


class HeatmapGeometrySerializer(serializers.Serializer):
    coordinates = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField(), min_length=2, max_length=2),
        min_length=2,
        max_length=40_000,
    )

    def validate_coordinates(self, value: list[list[float]]) -> list[list[float]]:
        for longitude, latitude in value:
            if not (math.isfinite(longitude) and -180 <= longitude <= 180):
                raise serializers.ValidationError(
                    "Longitude must be finite and between -180 and 180."
                )
            if not (math.isfinite(latitude) and -90 <= latitude <= 90):
                raise serializers.ValidationError(
                    "Latitude must be finite and between -90 and 90."
                )
        return value


class HeatmapRequestSerializer(serializers.Serializer):
    geometry = HeatmapGeometrySerializer()
    departure_utc = serializers.CharField()
    load_lb = serializers.FloatField(min_value=0, max_value=100_000)

    def validate_load_lb(self, value: float) -> float:
        if not math.isfinite(value):
            raise serializers.ValidationError("Must be a finite number between 0 and 100000 lb.")
        return value

    def validate_departure_utc(self, value: str) -> datetime:
        text = value.strip()
        if not text.endswith(("Z", "+00:00")):
            raise serializers.ValidationError("Must be an ISO 8601 timestamp in UTC (Z or +00:00).")
        try:
            parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        except ValueError:
            raise serializers.ValidationError("Must be a valid ISO 8601 UTC timestamp.") from None
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise serializers.ValidationError("Must be an ISO 8601 timestamp in UTC.")
        return parsed.astimezone(UTC)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        departure = attrs["departure_utc"]
        now = self.context["now"]
        earliest = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if departure < earliest:
            raise serializers.ValidationError(
                {"departure_utc": "Is before the earliest hour available from the weather source."}
            )
        if departure > now + timedelta(days=7):
            raise serializers.ValidationError(
                {"departure_utc": "Must be no more than 7 days ahead."}
            )
        return attrs


def validate_geocode_query(value: str | None) -> str:
    if value is None or len(value.strip()) < 2:
        raise serializers.ValidationError("q must contain at least 2 non-space characters.")
    query = value.strip()
    if len(query) > 200:
        raise serializers.ValidationError("q must be 200 characters or fewer.")
    return query
